"""Read-only batch rescore of a completed fixed matrix or reliability summary.

Creates a new artifact directory; never changes raw reports, specs, old scores,
the database or providers. A failure stays in the denominator at zero accuracy.
"""

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean

from briefforge.evaluation_v2 import VERSION, write_score


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def flatten(summary):
    if "results" in summary:
        return [{**item, "group": "fixed_matrix"} for item in summary["results"]]
    records = [{**item, "group": "quality"} for item in summary["quality"]]
    for update in summary.get("updates", []):
        records.extend(
            {**update[key], "group": f"update_{key}"}
            for key in ("before", "partial", "full") if key in update
        )
    return records


def rescore(source: Path, output: Path, spec_directory: Path):
    if output.exists():
        raise FileExistsError("Choose a new output directory; prior metrics will not be overwritten.")
    summary_path = source / "summary.json"
    summary = read(summary_path)  # A final summary is required, never infer completed work.
    outcomes = flatten(summary)
    reports = {}
    for path in source.glob("*.report.json"):
        report = read(path)
        if report["run_id"] in reports:
            raise ValueError("Duplicate report snapshots for one run; inspect manually.")
        reports[report["run_id"]] = path
    # Validate references and snapshots before creating any output.
    for item in outcomes:
        spec = spec_directory / f"{item.get('source_scenario', item['scenario'])}.json"
        if not spec.is_file():
            raise FileNotFoundError(spec)
        if item["status"] == "completed" and item["run_id"] not in reports:
            raise ValueError(f"Completed run has no saved report: {item['run_id']}")
    output.mkdir(parents=True)
    results = []
    for item in outcomes:
        spec = spec_directory / f"{item.get('source_scenario', item['scenario'])}.json"
        row = {k: item.get(k) for k in (
            "scenario", "source_scenario", "architecture", "group", "run_id", "status", "model_profile",
            "spent_usd", "reserved_usd", "elapsed_seconds", "request_count", "error",
        )}
        row["spec_sha256"] = sha(spec)
        if item["status"] == "completed":
            path = reports[item["run_id"]]
            metrics = write_score(path, spec, output / f"{item['scenario']}-{item['architecture']}.metrics-v2.json")
            row.update({k: metrics[k] for k in (
                "facts", "correct_facts", "fact_accuracy", "answer_coverage", "evidence_completeness",
                "retained_assertion_issue_facts", "unexpected_withdrawal_facts", "unanswered_facts",
                "answered_but_not_verified_facts", "review_required_facts", "report_sha256",
            )})
        else:
            row.update(facts=len(read(spec)["facts"]), correct_facts=0, fact_accuracy=0,
                       answer_coverage=0, evidence_completeness=0, review_required_facts=None)
        results.append(row)
    aggregate = {}
    groups = sorted({row["group"] for row in results})
    for group in groups:
        aggregate[group] = {}
        for architecture in ("single", "pipeline", "multi"):
            rows = [r for r in results if r["group"] == group and r["architecture"] == architecture]
            if not rows:
                continue
            aggregate[group][architecture] = {
                "runs": len(rows), "completed": sum(r["status"] == "completed" for r in rows),
                "mean_fact_accuracy": mean(r["fact_accuracy"] for r in rows),
                "mean_answer_coverage": mean(r["answer_coverage"] for r in rows),
                "total_confirmed_usd": round(sum(r["spent_usd"] or 0 for r in rows), 6),
                "total_reserved_usd": round(sum(r["reserved_usd"] or 0 for r in rows), 6),
                "request_count": sum(r["request_count"] or 0 for r in rows),
                "mean_elapsed_seconds": mean(r["elapsed_seconds"] or 0 for r in rows),
                "unexpected_withdrawal_facts": sum(r.get("unexpected_withdrawal_facts", 0) for r in rows),
                "assertion_issue_facts": sum(r.get("retained_assertion_issue_facts", 0) for r in rows),
            }
    payload = {
        "created_at": datetime.now(UTC).isoformat(), "metric_version": VERSION,
        "source_summary_sha256": sha(summary_path),
        "scorer_sha256": sha(Path(__file__).resolve().parents[1] / "src/briefforge/evaluation_v2.py"),
        "helper_sha256": sha(Path(__file__)), "input_directory": str(source),
        "not_run": summary.get("not_run", []), "groups": aggregate, "results": results,
        "limitations": [
            "Same frozen6 price/SSO obligations; not whole-report semantic accuracy.",
            "Literal matching can miss correct paraphrases; independent review must annotate them separately.",
            "Failed submitted runs receive zero quality scores; unsubmitted work is separately listed.",
            "Quality runs and before/partial/full update runs are not pooled.",
            "Metric improvements are not evidence that multi is best without matched architecture comparisons.",
        ],
    }
    (output / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--spec-dir", type=Path, default=Path("data/evaluation/fact-v2"))
    args = parser.parse_args()
    result = rescore(args.input, args.out, args.spec_dir)
    print(json.dumps(result["groups"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
