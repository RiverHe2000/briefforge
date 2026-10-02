from datetime import UTC, datetime, timedelta

from sqlalchemy import update

from briefforge.collaboration import collaboration_summary
from briefforge.store import Store, events


def setup(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'collaboration.db'}", tmp_path)
    project = store.create_project({"title": "Audit", "mode": "synthetic", "competitors": ["A"]})
    source = store.add_source(project["id"], {"title": "Current terms", "text": "Enterprise supports SSO"})
    run = store.create_run(project["id"], {"mode": "replay"})
    return store, project, source, run


def stamp(store, run_id, kind, payload, seconds):
    event = store.add_event(run_id, kind, kind, payload)
    ts = (datetime(2026, 10, 3, tzinfo=UTC) + timedelta(seconds=seconds)).isoformat()
    with store.engine.begin() as connection:
        connection.execute(update(events).where(events.c.id == event["id"]).values(created=ts))
    return event


def test_actual_overlap_retries_and_missing_end_are_not_invented(tmp_path):
    store, _project, _source, run = setup(tmp_path)
    first = store.create_task(run["id"], {"role": "competitor", "target": "A"})
    second = store.create_task(run["id"], {"role": "commercial", "target": "A"})
    verifier = store.create_task(run["id"], {"role": "verifier"})
    stamp(store, run["id"], "task_started", {"task_id": first["id"]}, 0)
    stamp(store, run["id"], "task_started", {"task_id": second["id"]}, 2)
    stamp(store, run["id"], "task_started", {"task_id": verifier["id"]}, 3)
    stamp(store, run["id"], "task_completed", {"task_id": first["id"]}, 6)
    stamp(store, run["id"], "task_failed", {"task_id": second["id"]}, 7)
    stamp(store, run["id"], "task_started", {"task_id": second["id"]}, 9)
    stamp(store, run["id"], "task_started", {"task_id": second["id"]}, 15)
    stamp(store, run["id"], "task_completed", {"task_id": second["id"]}, 17)
    stamp(store, run["id"], "task_reused", {"task_id": first["id"]}, 18)
    stamp(store, run["id"], "task_reused", {"task_id": first["id"]}, 19)
    result = collaboration_summary(store, run["id"])
    assert result["metrics"]["peak_parallel_research"] == 2
    assert result["metrics"]["overlap_seconds"] == 4
    assert result["metrics"]["reused_tasks"] == 1
    assert result["metrics"]["reused_claims"] is None
    retried = next(t for t in result["tasks"] if t["id"] == second["id"])
    assert len(retried["intervals"]) == 3
    assert retried["intervals"][1]["status"] == "interrupted"
    assert retried["intervals"][1]["ended_at"] is None


def test_frozen_final_status_decisions_and_reuse_are_source_bound(tmp_path):
    store, project, source, run = setup(tmp_path)
    task = store.create_task(run["id"], {"role": "commercial", "round": 1, "target": "A"})
    before = {"statement": "No SSO", "status": "supported", "value": "No", "conditions": []}
    after = {
        "statement": "Enterprise supports SSO",
        "status": "supported",
        "value": "Enterprise",
        "conditions": [],
    }
    data = {
        "decision_id": "decision-1",
        "action": "claim_revised",
        "subject": "A",
        "dimension": "SSO",
        "before": before,
        "after": after,
        "source_ids": [source["id"], "foreign"],
        "task_ids": [task["id"], "foreign"],
        "round": 1,
        "reason": "Current terms",
    }
    for _ in range(2):
        store.add_event(run["id"], "evidence_decision", "Revision", data)
    store.add_event(run["id"], "claim_corrected", "Legacy duplicate", data)
    store.add_event(
        run["id"],
        "reuse_planned",
        "Source changed",
        {"preserved_claim_keys": ["A::定位", "A::定位"], "affected_claim_keys": ["A::SSO"]},
    )
    report = store.save_report(
        project["id"],
        run["id"],
        {"claims": [{"subject": "A", "dimension": "SSO", "status": "uncertain"}], "sources": [source]},
    )
    store.update_run(run["id"], {"status": "completed", "report_id": report["id"]})
    result = collaboration_summary(store, run["id"])
    assert len(result["decisions"]) == 1
    assert result["decisions"][0]["final_status"] == "uncertain"
    assert result["decisions"][0]["roles"] == ["commercial"]
    assert result["decisions"][0]["source_ids"] == [source["id"]]
    assert result["metrics"]["changed_claims"] == 1
    assert result["metrics"]["unresolved_claims"] == 1
    assert result["metrics"]["reused_claims"] is None  # No prior frozen report proves reuse.
    assert result["metrics"]["planned_reused_claims"] == result["metrics"]["recomputed_claims"] == 1


def test_legacy_record_has_no_manufactured_timings_or_roles(tmp_path):
    store, _project, source, run = setup(tmp_path)
    store.add_event(
        run["id"],
        "claim_corrected",
        "Recorded revision",
        {
            "subject": "A",
            "dimension": "SSO",
            "before": "No",
            "after": "Enterprise only",
            "evidence": [{"source_id": source["id"], "quote": "Enterprise supports SSO"}],
        },
    )
    result = collaboration_summary(store, run["id"])
    assert result["metrics"]["peak_parallel_research"] is None
    assert result["decisions"][0]["roles"] == []
    assert result["decisions"][0]["after"]["status"] is None
    assert result["decisions"][0]["final_status"] is None


def test_collaboration_does_not_lose_decisions_after_event_page_limit(tmp_path):
    store, _project, _source, run = setup(tmp_path)
    for index in range(1001):
        store.add_event(run["id"], "progress", "Progress", {"index": index})
    store.add_event(
        run["id"], "reuse_planned", "Reuse", {"preserved_claim_keys": ["A::定位"], "affected_claim_keys": []}
    )
    result = collaboration_summary(store, run["id"])
    assert result["metrics"]["reused_claims"] is None
    assert result["metrics"]["planned_reused_claims"] == 1
    assert result["metrics"]["recomputed_claims"] == 0


def test_revision_provenance_keeps_own_previous_source_but_not_other_project(tmp_path):
    store, project, source, first = setup(tmp_path)
    store.cancel_run(first["id"])
    current = store.add_source(
        project["id"],
        {"title": "Changed terms", "logical_key": source["logical_key"], "text": "Changed SSO scope"},
    )
    another = store.create_project({"title": "Private", "mode": "synthetic"})
    foreign = store.add_source(another["id"], {"title": "Private terms", "text": "Private source"})
    run = store.create_run(project["id"], {})
    store.add_event(
        run["id"],
        "evidence_decision",
        "Version change",
        {"decision_id": "change", "source_ids": [source["id"], current["id"], foreign["id"]]},
    )
    result = collaboration_summary(store, run["id"])
    assert set(result["decisions"][0]["source_ids"]) == {source["id"], current["id"]}
    assert foreign["id"] not in {s["id"] for s in result["sources"]}


def test_version_comparison_shows_real_update_and_counts_only_verified_reuse(tmp_path):
    import copy

    store, project, source, first = setup(tmp_path)

    def claim(dimension, statement):
        return {
            "subject": "A",
            "dimension": dimension,
            "statement": statement,
            "status": "supported",
            "value": statement,
            "conditions": [],
            "computation": None,
            "evidence": [{"source_id": source["id"], "quote": source["text"]}],
        }

    prior_claims = [claim("价格", "USD 12/month"), claim("定位", "Small teams"), claim("SSO", "Enterprise")]
    prior = store.save_report(project["id"], first["id"], {"claims": prior_claims, "sources": [source]})
    store.update_run(first["id"], {"status": "completed", "report_id": prior["id"]})
    newer_source = store.add_source(
        project["id"], {"title": "New price", "text": "USD 15/month", "logical_key": source["logical_key"]}
    )
    run = store.create_run(project["id"], {})
    current_claims = copy.deepcopy(prior_claims)
    current_claims[0].update(
        statement="USD 15/month",
        value="USD 15/month",
        evidence=[{"source_id": newer_source["id"], "quote": newer_source["text"]}],
    )
    current_claims[1].update(statement="Unknown", status="uncertain", value=None)
    current = store.save_report(
        project["id"], run["id"], {"claims": current_claims, "sources": [newer_source]}
    )
    store.update_run(run["id"], {"status": "completed", "report_id": current["id"]})
    store.add_event(
        run["id"],
        "reuse_planned",
        "Plan",
        {"preserved_claim_keys": ["A::定位", "A::SSO"], "affected_claim_keys": ["A::价格"]},
    )
    result = collaboration_summary(store, run["id"])
    assert result["metrics"]["planned_reused_claims"] == 2
    assert result["metrics"]["reused_claims"] == 1
    price = next(d for d in result["decisions"] if d["dimension"] == "价格")
    assert price["action"] == "report_revision" and price["event_id"] is None
    assert price["before"]["value"] == "USD 12/month" and price["after"]["value"] == "USD 15/month"
    assert set(price["source_ids"]) == {source["id"], newer_source["id"]}
    assert price["roles"] == price["task_ids"] == []
    assert next(d for d in result["decisions"] if d["dimension"] == "定位")["final_status"] == "uncertain"
    assert store.get_report(prior["id"]) == prior
