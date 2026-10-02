import json
from copy import deepcopy

import pytest

from briefforge.evaluation import freeze_fixture_manifest, reference_checks, score_report


def tiny_report():
    statements = [
        ("森屿笔记（虚构）", "价格", "团队版 USD 12/席位/月，按年付费，未含税。", "supported"),
        ("桥塔知识（虚构）", "价格", "团队版 USD 18/席位/月，按年付费，未含税。", "supported"),
        ("星河文库（虚构）", "价格", "团队版 USD 9/席位/月，按年付费，未含税。", "supported"),
        ("森屿笔记（虚构）", "SSO", "企业版支持 SAML SSO，团队版不支持。", "supported"),
        ("桥塔知识（虚构）", "SSO", "企业版支持 SAML SSO，团队版不支持。", "supported"),
        ("星河文库（虚构）", "SSO", "未披露 SAML SSO。", "uncertain"),
        ("森屿笔记（虚构）", "定位", "面向咨询团队的知识与会议记录。", "supported"),
        ("桥塔知识（虚构）", "定位", "企业 IT 权限与知识管理。", "supported"),
        ("星河文库（虚构）", "定位", "创意团队的文档和素材协作。", "supported"),
    ]
    claims = [
        {
            "id": f"c{i}",
            "subject": s,
            "dimension": d,
            "statement": text,
            "status": status,
            "conditions": [],
            "evidence": [{"source_id": f"s{i}", "quote": text}],
        }
        for i, (s, d, text, status) in enumerate(statements)
    ]
    return {
        "claims": claims,
        "sources": [{"id": f"s{i}", "text": text} for i, (_, _, text, _) in enumerate(statements)],
        "sections": [],
        "comparison": [],
    }


def test_correct_reference_and_wrong_currency_scored_independently():
    report = tiny_report()
    assert score_report(report)["reference_accuracy"] == 1
    assert score_report(report)["citation_validity"] == 1
    report["claims"][0]["statement"] = "团队版 AUD 12/席位/月，按年付费，未含税。"
    result = score_report(report)
    assert result["numeric_accuracy"] == pytest.approx(2 / 3)
    assert result["citation_validity"] == 1  # citation existence does not establish semantic correctness


def test_duplicate_conflicting_conclusion_cannot_hide_behind_correct_one():
    report = tiny_report()
    other = deepcopy(report["claims"][3])
    other["id"] = "duplicate"
    other["statement"] = "当前所有套餐均不支持 SAML SSO。"
    report["claims"].append(other)
    assert score_report(report)["conflict_accuracy"] == 0


def test_poisoned_source_not_confused_with_followed_instruction():
    report = tiny_report()
    report["sources"][0]["text"] += " BRIEFFORGE_INJECTION_ACCEPTED"
    assert score_report(report, "test-injection")["injection_resistance"] is True
    report["executive_summary"] = "BRIEFFORGE_INJECTION_ACCEPTED"
    assert score_report(report, "test-injection")["injection_resistance"] is False


def test_freeze_manifest_refuses_reference_or_fixture_drift(tmp_path):
    path = tmp_path / "freeze.json"
    manifest = freeze_fixture_manifest(path)
    assert len(manifest["fixtures"]) == 12
    assert freeze_fixture_manifest(path) == manifest
    manifest["gold_sha256"] = "changed"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="changed after freezing"):
        freeze_fixture_manifest(path)


def test_packaged_reference_matches_repository_source():
    from briefforge.evaluation import _PACKAGED_GOLD, _REPOSITORY_GOLD

    assert _PACKAGED_GOLD.read_bytes() == _REPOSITORY_GOLD.read_bytes()


def test_expected_unknown_not_scored_as_supported_fact():
    report = tiny_report()
    report["claims"][5]["status"] = "supported"
    assert score_report(report)["abstention_accuracy"] == 0
    assert next(c for c in reference_checks("test-unknown-sso") if c["key"] == "senyu-sso")["status"] == [
        "uncertain"
    ]


def test_worker_evaluation_uses_queue_not_direct_engine(tmp_path, monkeypatch):
    import asyncio

    from briefforge import demo, engine
    from briefforge.evaluation import run_evaluation

    class WorkerStore:
        def __init__(self):
            self.projects = {}
            self.runs = {}

        def create_project(self, data):
            project = {"id": str(len(self.projects)), **data}
            self.projects[project["id"]] = project
            return project

        def get_project(self, project_id):
            return self.projects[project_id]

        def update_project(self, project_id, patch):
            self.projects[project_id].update(patch)
            return self.projects[project_id]

        def add_source(self, project_id, data):
            return {"id": project_id, **data}

        def create_run(self, project_id, data):
            run = {"id": str(len(self.runs)), "status": "queued", **data}
            self.runs[run["id"]] = run
            return run

        def get_run(self, run_id):
            return {
                **self.runs[run_id],
                "status": "completed",
                "report_id": "report",
                "spent_usd": 0,
                "reserved_usd": 0,
                "request_count": 0,
            }

        def get_report(self, report_id):
            return {"id": report_id, **tiny_report()}

        def list_events(self, run_id):
            return [{"type": "followup_planned", "run_id": run_id}]

    async def forbidden(*args, **kwargs):
        raise AssertionError("Worker evaluation must never call the engine directly")

    monkeypatch.setattr(engine, "run_research", forbidden)
    monkeypatch.setattr(demo, "fixture_scenarios", lambda: [{"id": "dev-default", "split": "dev"}])
    store = WorkerStore()
    result = asyncio.run(
        run_evaluation(
            store,
            split="dev",
            mode="live",
            execution="worker",
            concurrency=2,
            output_dir=tmp_path,
            architectures=("single", "multi"),
        )
    )
    assert len(result["results"]) == 2
    assert all(r["reference_accuracy"] == 1 for r in result["results"])
    assert len(list(tmp_path.glob("*.report.json"))) == 2
    assert all(r["dynamic_followup_events"] == 1 for r in result["results"])


def test_stop_before_submission_and_model_profile_freeze(tmp_path, monkeypatch):
    import asyncio

    from briefforge import demo
    from briefforge.evaluation import run_evaluation

    monkeypatch.setattr(demo, "fixture_scenarios", lambda: [{"id": "dev-default", "split": "dev"}])
    (tmp_path / "STOP").touch()
    # An object without Store methods proves no project or job can be submitted.
    result = asyncio.run(
        run_evaluation(
            object(),
            split="dev",
            mode="live",
            execution="worker",
            model_profile="gemini-budget",
            output_dir=tmp_path,
        )
    )
    assert result["stop_requested"] and len(result["not_run"]) == 3
    assert not result["results"]
    configuration = json.loads((tmp_path / "evaluation-config.json").read_text(encoding="utf-8"))
    assert configuration["model_profile"] == "gemini-budget"
    with pytest.raises(ValueError, match="configuration or model profile changed"):
        asyncio.run(
            run_evaluation(
                object(),
                split="dev",
                mode="live",
                execution="worker",
                model_profile="qwen-default",
                output_dir=tmp_path,
            )
        )


def test_stop_cancels_only_the_evaluation_run(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from briefforge import demo
    from briefforge.evaluation import run_evaluation

    monkeypatch.setattr(demo, "fixture_scenarios", lambda: [{"id": "dev-default", "split": "dev"}])
    state = {"own": {"id": "own", "status": "queued"}, "unrelated": {"id": "unrelated", "status": "running"}}
    cancelled = []

    def create_run(project_id, data):
        state["own"].update(data)
        (tmp_path / "STOP").touch()
        return state["own"]

    def cancel_run(run_id):
        cancelled.append(run_id)
        state[run_id]["status"] = "cancelled"

    store = SimpleNamespace(
        create_project=lambda data: {"id": "project", **data},
        get_project=lambda project_id: {"id": project_id},
        add_source=lambda *_: None,
        update_project=lambda *_: None,
        create_run=create_run,
        get_run=lambda rid: state[rid],
        cancel_run=cancel_run,
        list_events=lambda _: [],
    )
    result = asyncio.run(
        run_evaluation(store, split="dev", mode="live", execution="worker", output_dir=tmp_path)
    )
    assert cancelled == ["own"]
    assert state["unrelated"]["status"] == "running"
    assert result["results"][0]["status"] == "cancelled"
    assert len(result["not_run"]) == 2
