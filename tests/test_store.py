from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import update

from briefforge.store import BudgetExceeded, Conflict, LeaseLost, Store, jobs


@pytest.fixture
def store(tmp_path):
    return Store(f"sqlite:///{(tmp_path / 'test.db').as_posix()}", tmp_path)


def project(store):
    return store.create_project(
        {"title": "研究", "question": "竞品比较", "competitors": ["A"], "mode": "synthetic"}
    )


def test_run_idempotency_and_single_active(store):
    p = project(store)
    run = store.create_run(p["id"], {"mode": "replay"}, "first")
    assert store.create_run(p["id"], {"mode": "replay"}, "first")["id"] == run["id"]
    with pytest.raises(Conflict):
        store.create_run(p["id"], {"mode": "live"}, "first")
    with pytest.raises(Conflict):
        store.create_run(p["id"], {"mode": "replay"})


def test_versions_freeze_run_and_invalidate_dependent_claims(store):
    p = project(store)
    source = store.add_source(p["id"], {"title": "价格", "text": "价格：USD 10", "logical_key": "pricing"})
    run = store.create_run(p["id"], {})
    store.save_claims(
        p["id"],
        run["id"],
        [
            {
                "subject": "A",
                "dimension": "价格",
                "statement": "USD 10",
                "status": "supported",
                "evidence": [{"source_id": source["id"], "quote": "USD 10", "locator": "价格"}],
            }
        ],
    )
    report = store.save_report(
        p["id"], run["id"], {"sources": [source], "claims": store.list_claims(p["id"])}
    )
    newer = store.add_source(p["id"], {"title": "价格", "text": "价格：USD 12", "logical_key": "pricing"})
    assert newer["version"] == 2
    assert store.get_run(run["id"])["source_ids"] == [source["id"]]
    assert store.list_claims(p["id"])[0]["dirty"]
    assert store.get_project(p["id"])["dirty"]
    assert store.get_report(report["id"])["sources"][0]["text"] == "价格：USD 10"
    assert store.save_report(p["id"], run["id"], {})["id"] == report["id"]


def test_fabricated_or_cross_project_citation_rejected(store):
    p = project(store)
    q = project(store)
    src = store.add_source(q["id"], {"title": "其他", "text": "可定位"})
    run = store.create_run(p["id"], {})
    with pytest.raises(ValueError):
        store.save_claims(
            p["id"],
            run["id"],
            [
                {
                    "subject": "A",
                    "dimension": "SSO",
                    "statement": "支持",
                    "evidence": [{"source_id": src["id"], "quote": "可定位"}],
                }
            ],
        )


def test_budget_race_is_atomic_across_stores(store):
    p = project(store)
    run = store.create_run(p["id"], {"mode": "live", "budget_usd": 0.10})
    other = Store(store.database_url, store.data_dir)

    def reserve(i):
        try:
            return (store if i % 2 else other).reserve_cost(run["id"], 0.03, str(i))
        except BudgetExceeded:
            return None

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(reserve, range(10)))
    assert sum(x is not None for x in results) == 3
    assert store.budget_summary()["reserved_usd"] == 0.09
    assert store.get_run(run["id"])["request_count"] == 3


def test_settlement_unknown_and_duplicate_are_honest(store):
    p = project(store)
    run = store.create_run(p["id"], {"mode": "live"})
    r = store.reserve_cost(run["id"], 0.04, "a")
    assert store.reserve_cost(run["id"], 0.04, "a")["reused"]
    store.settle_cost(r["id"], None)
    assert store.budget_summary()["reserved_usd"] == 0.04
    store.cancel_run(run["id"])
    store.settle_cost(r["id"], 0.012)
    store.settle_cost(r["id"], 0.02)
    assert store.budget_summary()["spent_usd"] == 0.012
    assert store.budget_summary()["reserved_usd"] == 0


def test_cancel_resume_preserves_completed_outputs(store):
    p = project(store)
    run = store.create_run(p["id"], {})
    complete = store.create_task(run["id"], {"status": "completed", "output": {"evidence": "retained"}})
    incomplete = store.create_task(run["id"], {})
    store.cancel_run(run["id"])
    store.resume_run(run["id"])
    tasks = {t["id"]: t for t in store.list_tasks(run["id"])}
    assert tasks[complete["id"]]["output"] == {"evidence": "retained"}
    assert tasks[incomplete["id"]]["status"] == "pending"


def test_stale_worker_cannot_commit(store):
    p = project(store)
    run = store.create_run(p["id"], {})
    first = store.claim_job()
    worker = Store(store.database_url, store.data_dir)
    worker.lease_id, worker.lease_token = first["id"], first["token"]
    with store.engine.begin() as c:
        c.execute(update(jobs).where(jobs.c.id == run["id"]).values(expires=0))
    second = store.claim_job()
    assert second["token"] != first["token"]
    with pytest.raises(LeaseLost):
        worker.update_run(run["id"], {"status": "completed"})
    assert not store.heartbeat(first["id"], first["token"])


def test_export_dedup_immutable_report(store):
    p = project(store)
    run = store.create_run(p["id"], {})
    report = store.save_report(p["id"], run["id"], {})
    assert store.create_export(report["id"], "docx")["id"] == store.create_export(report["id"], "docx")["id"]


def test_renderer_upgrade_creates_new_job_preserving_old_export_and_report(store, monkeypatch):
    import briefforge.store as store_module

    p = project(store)
    run = store.create_run(p["id"], {})
    report = store.save_report(p["id"], run["id"], {})
    old = store.create_export(report["id"], "pptx")
    store.update_export(old["id"], {"status": "completed", "path": "existing.pptx"})
    monkeypatch.setattr(store_module, "EXPORT_RENDERER_VERSION", "next-test-version")
    new = store.create_export(report["id"], "pptx")
    assert new["id"] != old["id"]
    assert new["exporter_version"] == "next-test-version"
    assert store.create_export(report["id"], "pptx")["id"] == new["id"]
    assert store.get_export(old["id"])["path"] == "existing.pptx"
    assert store.get_report(report["id"]) == report


def test_replay_cannot_spend_or_process_real_sources(store):
    p = project(store)
    run = store.create_run(p["id"], {})
    with pytest.raises(ValueError):
        store.reserve_cost(run["id"], 0.001, "bad")
    real = store.create_project({"mode": "public"})
    with pytest.raises(ValueError):
        store.create_run(real["id"], {"mode": "replay"})


def test_source_metadata_change_creates_version_and_run_brief_is_frozen(store):
    p = project(store)
    old = store.add_source(
        p["id"], {"title": "条款", "text": "支持SSO", "logical_key": "terms", "published_at": "2025-01-01"}
    )
    run = store.create_run(p["id"], {})
    with pytest.raises(Conflict):
        store.edit_brief(p["id"], {"question": "新目标"})
    newer = store.add_source(
        p["id"], {"title": "条款", "text": "支持SSO", "logical_key": "terms", "published_at": "2026-01-01"}
    )
    assert newer["version"] == 2 and newer["id"] != old["id"]
    assert store.get_run(run["id"])["source_ids"] == [old["id"]]
    store.cancel_run(run["id"])
    store.edit_brief(p["id"], {"question": "新目标"})
    assert store.get_run(run["id"])["brief"]["question"] == "竞品比较"
