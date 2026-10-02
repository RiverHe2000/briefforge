import inspect
import uuid
from copy import deepcopy

import pytest

from briefforge import demo


class MemoryStore:
    def __init__(self):
        self.projects = {}
        self.sources = []

    def create_project(self, data):
        project = {"id": str(uuid.uuid4()), **deepcopy(data)}
        self.projects[project["id"]] = project
        return project

    def get_project(self, project_id):
        return self.projects[project_id]

    def add_source(self, project_id, source):
        self.sources.append({"project_id": project_id, **deepcopy(source)})


def test_seed_contract_and_disclosure():
    store = MemoryStore()
    project = demo.seed_demo(store)
    assert project["mode"] == "synthetic"
    assert len(project["competitors"]) == 3
    assert len(store.sources) == 30
    assert len({s["logical_key"] for s in store.sources}) == 30
    assert all(s["synthetic"] and "虚构测试资料" in s["text"] for s in store.sources)
    assert all(s["url"] is None for s in store.sources)
    assert [
        len([s for s in store.sources if s["competitor"] == name]) for name in project["competitors"]
    ] == [10, 10, 9]
    industry = [s for s in store.sources if s["kind"] == "industry"]
    assert len(industry) == 1 and not industry[0]["competitor"]


def test_fixture_manifest_split_and_independent_outputs():
    manifest = demo.fixture_scenarios()
    assert sum(f["split"] == "dev" for f in manifest) == 4
    assert sum(f["split"] == "test" for f in manifest) == 8
    assert all(len(demo.demo_sources(f["id"])) == 30 for f in manifest)
    manifest[0]["id"] = "changed"
    assert demo.fixture_scenarios()[0]["id"] == "dev-default"


def test_sources_never_load_evaluation_answers():
    implementation = inspect.getsource(demo)
    assert "read_text" not in implementation
    assert "from .evaluation" not in implementation
    assert not any(
        "reference_accuracy" in s["text"] or "expected_answer" in s["text"] for s in demo.demo_sources()
    )


def test_unknown_scenario_is_rejected_before_persistence():
    store = MemoryStore()
    with pytest.raises(ValueError):
        demo.seed_demo(store, "does-not-exist")
    assert not store.projects


def test_holdout_variations_have_real_semantic_differences():
    sources = demo.demo_sources("test-mixed-currency")
    price = next(s for s in sources if s["logical_key"] == "qiaota-pricing")
    assert "AUD 27" in price["text"] and "USD 18" not in price["text"]
    unknown = next(s for s in demo.demo_sources("test-unknown-sso") if s["logical_key"] == "senyu-security")
    assert "当前资料未披露" in unknown["text"]
    conflict = [
        s
        for s in demo.demo_sources("test-contradiction")
        if s["logical_key"] in {"senyu-security", "senyu-release"}
    ]
    assert conflict[0]["published_at"] == conflict[1]["published_at"]
    assert "团队版不支持" in conflict[0]["text"]
    assert "团队版与企业版均支持" in conflict[1]["text"]
