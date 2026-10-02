import copy

import pytest
from pydantic import ValidationError

from briefforge.engine import (
    EditorOutput,
    ResearchEngine,
    ReviewOutput,
    _ground_review_issues,
    _price_conflict_issues,
    _temporal_context,
    validate_claims,
)
from briefforge.store import Store


def replacement_case():
    old = {
        "id": "old",
        "title": "Earlier security",
        "competitor": "Atlas",
        "published_at": "2027-01-15",
        "text": "SSO：专业版支持 SAML SSO，基础版不支持；自 2027-01-15 生效。",
    }
    new = {
        "id": "new",
        "title": "Replacement security",
        "competitor": "Atlas",
        "published_at": "2027-03-01",
        "text": "SSO：基础版与专业版均支持 SAML SSO；自 2027-03-01 生效。\n版本范围：本说明替代 2027-01-15 的登录能力说明。",
    }
    claim = {
        "subject": "Atlas",
        "dimension": "SSO",
        "status": "supported",
        "statement": new["text"].splitlines()[0],
        "value": None,
        "conditions": [],
        "evidence": [{"source_id": "new", "quote": new["text"].splitlines()[0], "locator": "SSO"}],
    }
    issue = {
        "subject": "Atlas",
        "dimension": "SSO",
        "reason": "旧说明中基础版不支持，新说明却支持。",
        "source_ids": ["old", "new"],
        "resolution": "followup",
        "category": "current_conflict",
    }
    return [old, new], claim, issue


def test_explicit_field_replacement_rejects_false_conflict_without_mutating_sources():
    sources, claim, issue = replacement_case()
    frozen = copy.deepcopy(sources)
    context = _temporal_context([claim], sources)[0]
    assert context["current_source_ids"] == ["new"]
    assert context["historical_sources"][0]["superseded_by_source_id"] == "new"
    checked, notices = _ground_review_issues([claim], sources, [issue])
    assert checked == [] and notices
    assert sources == frozen
    # Exact evidence can omit the field label without losing the complete fact.
    claim["evidence"][0]["quote"] = claim["evidence"][0]["quote"].split("：", 1)[1]
    assert _ground_review_issues([claim], sources, [issue])[0] == []


@pytest.mark.parametrize(
    "variation",
    [
        "no_notice",
        "different_field",
        "different_replaced_date",
        "same_date",
        "old_undated",
        "new_undated",
        "different_subject",
        "different_publisher",
        "negative_notice",
        "no_longer_replaces",
        "unrelated_scope_same_line",
        "no_effective_date",
    ],
)
def test_scope_and_date_requirements_never_use_simple_newer_precedence(variation):
    sources, claim, issue = replacement_case()
    old, new = sources
    if variation == "no_notice":
        new["text"] = new["text"].splitlines()[0]
    elif variation == "different_field":
        new["text"] = new["text"].replace("登录能力说明", "价格说明")
    elif variation == "different_replaced_date":
        new["text"] = new["text"].replace("2027-01-15", "2027-01-16")
    elif variation == "same_date":
        old["published_at"] = "2027-03-01"
        new["text"] = new["text"].replace("2027-01-15", "2027-03-01")
    elif variation == "old_undated":
        old["published_at"] = None
    elif variation == "new_undated":
        new["published_at"] = None
    elif variation == "different_subject":
        new["competitor"] = "Boreal"
    elif variation == "different_publisher":
        old["url"], new["url"] = "https://atlas.example/security", "https://unrelated.example/post"
    elif variation == "negative_notice":
        new["text"] = new["text"].replace("本说明替代", "本说明不替代")
    elif variation == "no_longer_replaces":
        new["text"] = new["text"].replace("本说明替代", "本说明不再替代")
    elif variation == "unrelated_scope_same_line":
        new["text"] = new["text"].replace("登录能力说明", "价格说明，登录能力说明保持不变")
    else:
        new["text"] = new["text"].replace("；自 2027-03-01 生效", "")
    assert _temporal_context([claim], sources) == []
    checked, notices = _ground_review_issues([claim], sources, [issue])
    assert checked and not notices


@pytest.mark.parametrize("date", ["2027-03-01", None])
def test_an_additional_current_or_undated_conflict_still_requires_review(date):
    sources, claim, issue = replacement_case()
    additional = {**sources[0], "id": "other-current", "published_at": date}
    issue["source_ids"].append(additional["id"])
    sources.append(additional)
    assert _temporal_context([claim], sources) == []
    assert _ground_review_issues([claim], sources, [issue])[0]


def test_old_only_claim_generates_targeted_followup_even_if_critic_silent():
    sources, claim, _ = replacement_case()
    claim["statement"] = sources[0]["text"]
    claim["evidence"] = [{"source_id": "old", "quote": sources[0]["text"], "locator": "SSO"}]
    issues, _ = _ground_review_issues([claim], sources, [])
    assert issues[0]["category"] == "historical_update"
    assert issues[0]["resolution"] == "followup" and issues[0]["source_ids"] == ["new"]


def test_explicit_replacement_is_field_scoped_for_prices_too():
    sources, _, _ = replacement_case()
    for source, price in zip(sources, [23, 27], strict=True):
        source["kind"] = "pricing"
        source["text"] = f"价格：专业版 USD {price}/席位/月，按年付费；自 {source['published_at']} 生效。"
    sources[1]["text"] += "\n本说明替代 2027-01-15 的价格说明。"
    claim = {"subject": "Atlas", "dimension": "价格"}
    assert _price_conflict_issues([claim], sources) == []
    sources[1]["text"] = sources[1]["text"].replace("的价格说明", "的登录能力说明")
    assert _price_conflict_issues([claim], sources)[0]["category"] == "current_conflict"


async def test_actual_verifier_guard_exposes_rejected_temporal_conflict_and_preserves_new_claim(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'db.sqlite'}", tmp_path)
    project = store.create_project(
        {
            "title": "Replacement",
            "question": "Compare SSO",
            "competitors": ["Atlas"],
            "dimensions": ["SSO"],
            "mode": "synthetic",
        }
    )
    sources, claim, issue = replacement_case()
    saved = [
        store.add_source(project["id"], {**s, "synthetic": True, "logical_key": s["id"]}) for s in sources
    ]
    claim["evidence"][0]["source_id"] = saved[1]["id"]
    issue["source_ids"] = [s["id"] for s in saved]
    run = store.create_run(project["id"], {"mode": "live", "architecture": "multi"})

    class Provider:
        async def structured(self, messages, schema, purpose, **kwargs):
            assert issubclass(schema, ReviewOutput)
            return schema.model_validate({"issues": [issue]})

    engine = ResearchEngine(store, run["id"], provider=Provider())
    checked = validate_claims([claim], saved, {"Atlas"})
    state = {**engine.initial_state(), "claims": checked}
    result = await engine.verify(state)
    assert result["issues"] == []
    assert engine.next_after_verify({**state, **result}) == "edit"
    decision = next(
        e["payload"]
        for e in store.list_events(run["id"])
        if e["type"] == "evidence_decision" and e["payload"]["action"] == "review_rejected"
    )
    assert decision["before"] == decision["after"]
    assert decision["after"]["status"] == "supported"
    assert decision["task_ids"] == [store.list_tasks(run["id"])[0]["id"]]


async def test_editor_excess_summary_keys_selects_eight_without_discarding_report_facts(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'db.sqlite'}", tmp_path)
    subjects = ["Atlas", "Boreal", "Cedar"]
    dimensions = ["定位", "核心功能", "SSO"]
    project = store.create_project(
        {
            "title": "Editor selection",
            "question": "Compare",
            "competitors": subjects,
            "dimensions": dimensions,
            "mode": "synthetic",
        }
    )
    claims = []
    for subject in subjects:
        source = store.add_source(
            project["id"],
            {"title": subject, "competitor": subject, "synthetic": True, "text": "原文明确陈述的产品事实。"},
        )
        claims.extend(
            {
                "subject": subject,
                "dimension": dimension,
                "statement": f"{subject} 的 {dimension} 原文结论。",
                "status": "supported",
                "conditions": [],
                "value": None,
                "evidence": [{"source_id": source["id"], "quote": source["text"], "locator": ""}],
            }
            for dimension in dimensions
        )
    keys = [f"{c['subject']}::{c['dimension']}" for c in claims]
    run = store.create_run(project["id"], {"mode": "live", "architecture": "pipeline"})

    class Provider:
        calls = 0

        async def structured(self, messages, schema, purpose, **kwargs):
            self.calls += 1
            assert schema is EditorOutput
            return schema.model_validate(
                {
                    "summary_keys": [keys[0], "unknown-key", *keys],
                    "sections": [{"heading": "Comparison", "claim_keys": keys[:2]}],
                    "opportunities": [],
                }
            )

    provider = Provider()
    engine = ResearchEngine(store, run["id"], provider=provider)
    output = await engine.edit({**engine.initial_state(), "claims": claims, "issues": []})
    report = store.get_report(output["report_id"])
    assert provider.calls == 1 and len(report["claims"]) == 9
    assert len(report["executive_summary"].splitlines()) == 8
    body = "\n".join(s["body"] for s in report["sections"])
    assert all(c["statement"] in body for c in claims)
    selection = next(
        e["payload"] for e in store.list_events(run["id"]) if e["type"] == "editor_selection_clipped"
    )
    assert selection["selected_keys"] == keys[:8]
    assert not any(e["type"] == "schema_repair" for e in store.list_events(run["id"]))


def test_editor_summary_input_keeps_a_hard_bound_and_malformed_output_invalid():
    with pytest.raises(ValidationError):
        EditorOutput(summary_keys=["x"] * 41, sections=[{"heading": "test", "claim_keys": []}])
    with pytest.raises(ValidationError):
        EditorOutput.model_validate({"summary_keys": "x", "sections": []})
