import json
from copy import deepcopy
from pathlib import Path

import pytest

from briefforge.evaluation_v2 import compare_outcomes, score_fact_report, score_transitions, write_score
from briefforge.reliability import (
    SCENARIOS,
    apply_reliability_update,
    freeze_reliability_manifest,
    reliability_sources,
)


def fact_spec(expected="known"):
    return {"scenario": "independent-test", "facts": [{
        "id": "sso", "subject": "Example", "dimension": "SSO", "expected": expected,
        "cross_dimension_patterns": ["SSO"],
        "axes": [{"id": "team", "expected": "yes", "states": {
            "yes": [r"团队版支持"], "no": [r"团队版不支持"],
        }}],
    }]}


def report(statement="团队版支持 SSO", value="团队版支持 SSO", status="supported"):
    return {
        "claims": [{"id": "c", "subject": "Example", "dimension": "SSO", "statement": statement,
                    "value": value, "status": status, "conditions": [],
                    "evidence": [{"source_id": "s", "quote": "团队版支持 SSO"}]}],
        "sources": [{"id": "s", "logical_key": "current", "text": "团队版支持 SSO"}],
    }


def test_value_contradiction_cannot_hide_behind_correct_statement_and_quote():
    result = score_fact_report(report(value="团队版不支持 SSO"), fact_spec())
    assert result["answer_coverage"] == 1
    assert result["evidence_completeness"] == 1
    assert result["fact_accuracy"] == 0
    assert result["retained_assertion_issue_facts"] == 1
    assert any(i["type"] == "field_inconsistency" for i in result["checks"][0]["issues"])


def test_complete_statement_with_empty_value_is_not_abstention():
    result = score_fact_report(report(value=None), fact_spec())
    assert result["fact_accuracy"] == 1
    assert result["withdrawn_facts"] == 0


def test_duplicate_or_comparison_wrong_answer_invalidates_fact():
    r = report()
    r["comparison"] = [{"competitor": "Example", "sso": "团队版不支持 SSO"}]
    assert score_fact_report(r, fact_spec())["fact_accuracy"] == 0
    r.pop("comparison")
    other = deepcopy(r["claims"][0])
    other.update(id="bad-duplicate", statement="团队版不支持 SSO", value=None)
    r["claims"].append(other)
    assert score_fact_report(r, fact_spec())["fact_accuracy"] == 0


def conflicting_report():
    r = report("一份文件称团队版支持 SSO，另一份称团队版不支持 SSO，现行资料冲突，无法确认。", None, "uncertain")
    r["sources"].append({"id": "t", "logical_key": "other", "text": "团队版不支持 SSO"})
    r["claims"][0]["evidence"].append({"source_id": "t", "quote": "团队版不支持 SSO"})
    return r


def test_current_conflict_requires_both_evidence_sides_and_neutral_value():
    r = conflicting_report()
    spec = fact_spec("conflict")
    spec["facts"][0]["required_source_keys"] = ["current", "other"]
    assert score_fact_report(r, spec)["fact_accuracy"] == 1
    r["claims"][0]["value"] = "团队版不支持 SSO"
    assert score_fact_report(r, spec)["fact_accuracy"] == 0
    r["claims"][0]["value"] = None
    r["claims"][0]["evidence"].pop()
    assert score_fact_report(r, spec)["evidence_completeness"] == 0


def test_current_conflict_cannot_leak_through_recent_change_dimension():
    r = conflicting_report()
    r["claims"].append({
        "id": "change", "subject": "Example", "dimension": "近期变化", "status": "supported",
        "statement": "团队版支持 SSO，但与其他说明存在冲突。", "evidence": [],
    })
    assert score_fact_report(r, fact_spec("conflict"))["fact_accuracy"] == 0


def test_withdrawal_is_neither_correction_nor_answer_coverage():
    before = report(value="团队版不支持 SSO")
    after = report("无法确认", None, "uncertain")
    result = score_transitions(before, after, fact_spec())
    assert result["counts"]["withdrawn_without_answer"] == 1
    assert result["counts"]["corrected"] == 0
    assert result["after"]["answer_coverage"] == 0
    assert result["after"]["withdrawn_facts"] == 1
    corrected = score_transitions(before, report(), fact_spec())
    assert corrected["counts"]["corrected"] == 1


def test_expected_unknown_can_be_answered_without_claiming_unsupported():
    r = report("未披露 SSO", None, "uncertain")
    r["sources"][0]["text"] = "未披露 SSO"
    r["claims"][0]["evidence"][0]["quote"] = "未披露 SSO"
    assert score_fact_report(r, fact_spec("unknown"))["fact_accuracy"] == 1
    r["claims"][0]["value"] = "团队版不支持 SSO"
    assert score_fact_report(r, fact_spec("unknown"))["fact_accuracy"] == 0


def test_invalid_quote_and_missing_material_condition_fail_separately():
    r = report()
    spec = fact_spec()
    spec["facts"][0]["requirements"] = [{"id": "effective", "patterns": ["2026-10-01"]}]
    r["claims"][0]["conditions"] = ["2026-10-01 生效"]
    assert score_fact_report(r, spec)["answer_coverage"] == 1
    assert score_fact_report(r, spec)["evidence_completeness"] == 0
    r["claims"][0]["evidence"][0]["quote"] = "不存在的引文"
    assert any(i["type"] == "invalid_quote" for i in score_fact_report(r, spec)["checks"][0]["issues"])


def test_new_price_obligation_detects_wrong_year_even_when_value_is_right():
    root = Path(__file__).resolve().parents[1]
    spec = json.loads((root / "data/evaluation/reliability-v1/challenge-faq.json").read_text(encoding="utf-8"))
    spec["facts"] = [spec["facts"][0]]
    text = "团队版 USD 12/席位/月，按年付费，未含税；月付为 USD 16/席位/月。企业版需单独询价。"
    r = {"claims": [{"id": "price", "subject": spec["facts"][0]["subject"], "dimension": "价格",
                     "statement": text, "value": text, "evidence": [{"source_id": "p", "quote": text}]}],
         "sources": [{"id": "p", "text": text}]}
    assert score_fact_report(r, spec)["fact_accuracy"] == 1
    r["claims"][0]["statement"] = "团队版每年支付12美元/席位，月付16美元/席位，未含税。企业版需单独询价。"
    result = score_fact_report(r, spec)
    assert result["fact_accuracy"] == 0
    assert result["retained_assertion_issue_facts"] == 1


def test_new_sources_do_not_change_frozen_suite_and_update_exactly_one_source():
    from briefforge.demo import demo_sources

    old = deepcopy(demo_sources("dev-default"))
    before = reliability_sources("challenge-update-before")
    after = reliability_sources("challenge-update-after")
    assert [a["logical_key"] for a, b in zip(before, after, strict=True) if a != b] == ["senyu-security"]
    assert demo_sources("dev-default") == old
    assert all(len(reliability_sources(s)) == 30 for s in SCENARIOS)
    calls = []
    class Store:
        def add_source(self, project, data):
            calls.append((project, data))
            return data
    assert apply_reliability_update(Store(), "example")["logical_key"] == "senyu-security"
    assert len(calls) == 1


def test_full_fixed_matrix_has_separate_v2_references():
    from briefforge.demo import fixture_scenarios
    from briefforge.evaluation_v2 import reference_spec

    for scenario in fixture_scenarios():
        if scenario["split"] == "test":
            spec = reference_spec(scenario["id"])
            assert len(spec["facts"]) == 6
            assert spec["independent_reference"]
    with pytest.raises(ValueError):
        reference_spec("../frozen_gold")


def test_new_manifest_and_score_are_append_only(tmp_path):
    specs = Path(__file__).resolve().parents[1] / "data/evaluation/reliability-v1"
    manifest = tmp_path / "manifest.json"
    assert len(freeze_reliability_manifest(manifest, specs)["scenarios"]) == 4
    assert freeze_reliability_manifest(manifest, specs)
    manifest.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        freeze_reliability_manifest(manifest, specs)
    r, s, dest = (tmp_path / name for name in ["report.json", "spec.json", "metrics-v2.json"])
    r.write_text(json.dumps(report()), encoding="utf-8")
    s.write_text(json.dumps(fact_spec()), encoding="utf-8")
    assert write_score(r, s, dest)["fact_accuracy"] == 1
    with pytest.raises(FileExistsError):
        write_score(r, s, dest)


def test_paired_costs_include_failures_and_mismatched_models_are_not_compared():
    common = {"scenario": "case", "sources_sha256": "sources", "spec_sha256": "spec",
              "model_profile": "same", "status": "completed", "spent_usd": .02,
              "metrics_v2": {"fact_accuracy": 1, "answer_coverage": 1}}
    one = {**common, "architecture": "single"}
    two = {**common, "architecture": "multi", "status": "failed", "spent_usd": .05}
    pair = compare_outcomes([one, two])["pairs"][0]
    assert pair["comparable"] and pair["delta_fact_accuracy"] == -1
    assert pair["extra_confirmed_usd"] == pytest.approx(.03)
    two["model_profile"] = "other"
    pair = compare_outcomes([one, two])["pairs"][0]
    assert not pair["comparable"] and "delta_fact_accuracy" not in pair
