"""Offline, versioned fact-obligation diagnostics; never imported by the engine.

This is a bounded deterministic evaluator, not a general semantic judge. Its
reference rules are independently authored and must never become model input.
Old evaluation.py metrics and historical results deliberately remain unchanged.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any

VERSION = "fact-obligations-v2"
UNKNOWN = r"未披露|未确认|无法确认|无法判断|未知|需要.*确认|尚待确认|不确定|unknown|not disclosed"
CONFLICT = r"冲突|矛盾|不一致|相反|存在差异|不同说法|conflict|contradict"
FIELDS = ("statement", "value", "conditions")


def reference_spec(scenario: str, directory: Path | None = None) -> dict[str, Any]:
    """Load evaluation-only v2 rules; legacy frozen_gold.json is never changed."""
    if not re.fullmatch(r"[a-z0-9-]+", scenario):
        raise ValueError("Invalid scenario identifier.")
    directory = directory or Path(__file__).resolve().parents[2] / "data/evaluation/fact-v2"
    return json.loads((directory / f"{scenario}.json").read_text(encoding="utf-8"))


def _match(patterns: list[str], text: str) -> bool:
    return any(re.search(p, text, re.IGNORECASE | re.DOTALL) for p in patterns)


def _fields(claim: dict[str, Any]) -> dict[str, str]:
    return {
        "statement": str(claim.get("statement") or ""),
        "value": str(claim.get("value") or ""),
        "conditions": "\n".join(claim.get("conditions") or []),
    }


def _states(text: str, axis: dict[str, Any]) -> set[str]:
    return {name for name, patterns in axis["states"].items() if _match(patterns, text)}


def score_fact_report(report: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    """Score an external obligation spec without consulting engine/gold internals.

    Requirement patterns are OR alternatives; requirements themselves are AND.
    Axis states model mutually exclusive answers, enabling field conflict checks.
    A 'conflict' obligation needs explicit conflict wording and both source sides.
    Unparsed answers are coverage misses requiring review, never assumed correct.
    """
    sources = {s["id"]: s for s in report.get("sources", [])}
    checks = []
    for fact in spec["facts"]:
        expected = fact.get("expected", "known")
        if expected not in {"known", "unknown", "conflict"}:
            raise ValueError(f"Unknown expected state: {expected}")
        subject_claims = [c for c in report.get("claims", []) if c.get("subject") == fact["subject"]]
        candidates = [c for c in subject_claims if c.get("dimension") == fact["dimension"]]
        related = [
            c for c in subject_claims
            if c in candidates or _match(fact.get("cross_dimension_patterns", []), " ".join(_fields(c).values()))
        ]
        # Check the actual comparison view too; it has no independent evidence and
        # must not improve the primary claim's answer or citation score.
        comparison_key = {"价格": "price", "SSO": "sso", "定位": "positioning"}.get(fact["dimension"])
        if comparison_key:
            related += [
                {"id": f"comparison-{index}-{comparison_key}", "statement": row.get(comparison_key, "")}
                for index, row in enumerate(report.get("comparison", []))
                if row.get("competitor") == fact["subject"] and row.get(comparison_key)
            ]
        issues: list[dict[str, str]] = []
        claim_checks = []
        for claim in related:
            fields = _fields(claim)
            is_primary = claim in candidates
            exact_quotes = []
            evidence_keys = set()
            total_quotes = 0
            for evidence in claim.get("evidence", []):
                total_quotes += 1
                source = sources.get(evidence.get("source_id"))
                quote = evidence.get("quote", "")
                if source and quote and quote in source.get("text", ""):
                    exact_quotes.append(quote)
                    evidence_keys.add(source.get("logical_key", ""))
                else:
                    issues.append({"claim_id": claim.get("id", ""), "type": "invalid_quote", "field": "evidence"})
            quoted = "\n".join(exact_quotes)
            unknown = bool(re.search(UNKNOWN, fields["statement"], re.IGNORECASE))
            conflict = bool(re.search(CONFLICT, fields["statement"], re.IGNORECASE))
            # Null values alone do not imply abstention: a complete statement may stand alone.
            withdrawn = unknown and not fields["value"] and not conflict
            axis_checks = []
            for axis in fact.get("axes", []):
                states_by_field = {field: _states(text, axis) for field, text in fields.items()}
                observed = set().union(*states_by_field.values())
                expected_state = axis.get("expected")
                for field, states in states_by_field.items():
                    if not states:
                        continue
                    field_conflict = bool(re.search(CONFLICT, fields[field], re.IGNORECASE))
                    if expected == "conflict":
                        # A current source disagreement must remain visible in each answer field.
                        bad = (field == "value" and not field_conflict) or (
                            field == "statement" and (not field_conflict or len(states) == 1)
                        )
                    elif expected == "unknown":
                        bad = True
                    else:
                        bad = any(state != expected_state for state in states)
                    if bad:
                        issues.append({
                            "claim_id": claim.get("id", ""), "type": "contradictory_assertion",
                            "axis": axis["id"], "field": field,
                        })
                if expected != "conflict" and len(observed) > 1:
                    issues.append({
                        "claim_id": claim.get("id", ""), "type": "field_inconsistency",
                        "axis": axis["id"], "field": "statement/value/conditions",
                    })
                axis_checks.append({
                    "id": axis["id"], "observed": {k: sorted(v) for k, v in states_by_field.items()},
                    "answer_present": expected_state in states_by_field["statement"]
                    if expected == "known" else True,
                    "evidence_present": expected_state in _states(quoted, axis)
                    if expected == "known" else len(_states(quoted, axis)) >= 2 if expected == "conflict" else True,
                })
            requirements = []
            for requirement in fact.get("requirements", []):
                body = "\n".join(fields[f] for f in requirement.get("fields", ["statement", "value", "conditions"]))
                requirements.append({
                    "id": requirement["id"],
                    "answer_present": _match(requirement["patterns"], body),
                    "evidence_present": not requirement.get("evidence", True)
                    or _match(requirement["patterns"], quoted),
                })
            if expected == "known":
                answered = not withdrawn and all(a["answer_present"] for a in axis_checks + requirements)
            elif expected == "unknown":
                answered = unknown and not any(a["observed"][f] for a in axis_checks for f in FIELDS)
            else:
                answered = conflict and claim.get("status") in {"uncertain", "contradicted"}
            answered = answered and all(requirement["answer_present"] for requirement in requirements)
            cited = bool(exact_quotes) and all(a["evidence_present"] for a in axis_checks + requirements)
            cited = cited and set(fact.get("required_source_keys", [])).issubset(evidence_keys)
            claim_checks.append({
                "claim_id": claim.get("id", ""), "primary": is_primary,
                "answered": answered, "evidence_complete": cited,
                "withdrawn": withdrawn, "axes": axis_checks, "requirements": requirements,
                "exact_quotes": len(exact_quotes), "total_quotes": total_quotes,
            })
        primary = [c for c in claim_checks if c["primary"]]
        answered = bool(primary) and all(c["answered"] for c in primary)
        evidence_complete = bool(primary) and all(c["evidence_complete"] for c in primary)
        assertion_issues = [i for i in issues if i["type"] in {"contradictory_assertion", "field_inconsistency"}]
        correct = answered and evidence_complete and not issues
        checks.append({
            "id": fact["id"], "subject": fact["subject"], "dimension": fact["dimension"],
            "expected": expected, "present": bool(primary), "answered": answered,
            "evidence_complete": evidence_complete, "consistent": not assertion_issues,
            "correct": correct, "withdrawn": any(c["withdrawn"] for c in primary),
            "review_required": not correct, "issues": issues, "claims": claim_checks,
        })
    if not checks:
        raise ValueError("A diagnostic spec requires at least one fact obligation.")
    return {
        "metric_version": VERSION, "scenario": spec["scenario"], "facts": len(checks),
        "correct_facts": sum(c["correct"] for c in checks),
        "fact_accuracy": mean(c["correct"] for c in checks),
        "answer_coverage": mean(c["answered"] for c in checks),
        "claim_presence": mean(c["present"] for c in checks),
        "evidence_completeness": mean(c["evidence_complete"] for c in checks),
        "retained_assertion_issue_facts": sum(not c["consistent"] for c in checks),
        "withdrawn_facts": sum(c["withdrawn"] for c in checks),
        "unexpected_withdrawal_facts": sum(c["withdrawn"] and c["expected"] != "unknown" for c in checks),
        "unanswered_facts": sum(not c["answered"] for c in checks),
        "answered_but_not_verified_facts": sum(c["answered"] and not c["correct"] for c in checks),
        "answered_but_missing_evidence_facts": sum(c["answered"] and not c["evidence_complete"] for c in checks),
        "review_required_facts": sum(c["review_required"] for c in checks),
        "label": "Bounded independent fact-obligation checks; not whole-report semantic accuracy.",
        "checks": checks,
    }


def score_transitions(before: dict, after: dict, spec: dict) -> dict:
    """Separate actual corrections, harmful regressions and safe but incomplete withdrawal."""
    initial = score_fact_report(before, spec)
    final = score_fact_report(after, spec)
    pairs = zip(initial["checks"], final["checks"], strict=True)
    rows = []
    for old, new in pairs:
        transition = (
            "regressed" if old["correct"] and not new["correct"] else
            "corrected" if not old["correct"] and new["correct"] else
            "withdrawn_without_answer" if not old["consistent"] and new["consistent"] and not new["answered"] else
            "unchanged_correct" if new["correct"] else "unresolved"
        )
        rows.append({"id": old["id"], "transition": transition})
    return {
        "metric_version": VERSION, "scenario": spec["scenario"],
        "counts": {name: sum(r["transition"] == name for r in rows) for name in (
            "corrected", "regressed", "withdrawn_without_answer", "unchanged_correct", "unresolved"
        )},
        "before": initial, "after": final, "transitions": rows,
    }


def compare_outcomes(outcomes: list[dict[str, Any]], baseline: str = "single") -> dict[str, Any]:
    """Paired quality/expense view with failures retained and no presumed winner.

    Inputs carry metrics_v2, status, scenario, architecture, model_profile,
    sources_sha256, spec_sha256, spent_usd, reserved_usd and elapsed_seconds.
    A pair with different input/reference/model hashes is explicitly incomparable.
    """
    groups: dict[str, dict[str, dict]] = {}
    for outcome in outcomes:
        group = groups.setdefault(outcome["scenario"], {})
        if outcome["architecture"] in group:
            raise ValueError("Duplicate scenario/architecture; separate repetitions explicitly.")
        group[outcome["architecture"]] = outcome
    pairs = []
    for scenario, group in groups.items():
        first = group.get(baseline)
        if first is None:
            continue
        for architecture, item in group.items():
            if architecture == baseline:
                continue
            differences = [key for key in ("model_profile", "sources_sha256", "spec_sha256")
                           if not first.get(key) or first.get(key) != item.get(key)]
            pair: dict[str, Any] = {
                "scenario": scenario, "baseline": baseline, "architecture": architecture,
                "comparable": not differences, "mismatched_or_missing": differences,
                "baseline_completed": first["status"] == "completed",
                "completed": item["status"] == "completed",
            }
            if not differences:
                for metric in ("fact_accuracy", "answer_coverage"):
                    old = first.get("metrics_v2", {}).get(metric, 0) if first["status"] == "completed" else 0
                    new = item.get("metrics_v2", {}).get(metric, 0) if item["status"] == "completed" else 0
                    pair[f"delta_{metric}"] = new - old
                pair.update(
                    extra_confirmed_usd=item.get("spent_usd", 0) - first.get("spent_usd", 0),
                    extra_reserved_usd=item.get("reserved_usd", 0) - first.get("reserved_usd", 0),
                    extra_elapsed_seconds=item.get("elapsed_seconds", 0) - first.get("elapsed_seconds", 0),
                    extra_requests=item.get("request_count", 0) - first.get("request_count", 0),
                )
            pairs.append(pair)
    return {
        "metric_version": VERSION, "baseline": baseline, "pairs": pairs,
        "limitations": [
            "Failed runs receive zero answer/accuracy scores in paired differences; costs remain included.",
            "Reserved cost is reported separately because final billing may be unknown.",
            "Elapsed time includes any worker queue delay unless separately instrumented.",
            "Mechanism events and task counts are not semantic quality gains.",
        ],
    }


def write_score(report_path: Path, spec_path: Path, destination: Path) -> dict:
    """Append-only scoring artifact; never overwrite historical metrics or input reports."""
    if destination.exists():
        raise FileExistsError(f"Use a new metric artifact: {destination}")
    report = json.loads(report_path.read_text(encoding="utf-8-sig"))
    spec = json.loads(spec_path.read_text(encoding="utf-8-sig"))
    result = score_fact_report(report, spec)
    result.update(
        created_at=datetime.now(UTC).isoformat(),
        report_sha256=hashlib.sha256(report_path.read_bytes()).hexdigest(),
        spec_sha256=hashlib.sha256(spec_path.read_bytes()).hexdigest(),
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = write_score(args.report, args.spec, args.out)
    print(json.dumps({k: v for k, v in result.items() if k != "checks"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
