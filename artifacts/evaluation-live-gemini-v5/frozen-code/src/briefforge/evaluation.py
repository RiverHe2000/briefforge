"""Frozen, evaluation-only references and paired architecture diagnostics.

`python -m briefforge.evaluation --split test --mode replay --out artifacts/eval`
performs no paid inference. Live mode requires the explicit --allow-paid flag.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import time
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any

_REPOSITORY_GOLD = Path(__file__).resolve().parents[2] / "data/evaluation/frozen_gold.json"
_PACKAGED_GOLD = Path(__file__).resolve().parent / "export_support/evaluation_reference.json"
_GOLD_PATH = _REPOSITORY_GOLD if _REPOSITORY_GOLD.is_file() else _PACKAGED_GOLD


def _load_gold() -> dict[str, Any]:
    return json.loads(_GOLD_PATH.read_text(encoding="utf-8"))


def reference_checks(scenario: str) -> list[dict[str, Any]]:
    scenario = "dev-default" if scenario == "default" else scenario
    gold = _load_gold()
    if scenario not in gold["scenarios"]:
        raise ValueError(f"No frozen reference for {scenario}")
    config = gold["scenarios"][scenario]
    checks = deepcopy(gold["base"])
    for check in checks:
        check.update(config.get("overrides", {}).get(check["key"], {}))
        check["subject"] = gold["companies"][check["company"]]
    for item in config.get("extra", []):
        check = deepcopy(item)
        check["subject"] = gold["companies"][check["company"]]
        checks.append(check)
    return checks


def score_report(
    report: dict[str, Any], scenario: str = "default", *, events: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    scenario = "dev-default" if scenario == "default" else scenario
    checks = reference_checks(scenario)
    claims = report.get("claims", [])
    results = []
    for check in checks:
        candidates = [
            claim
            for claim in claims
            if claim.get("subject") == check["subject"] and claim.get("dimension") == check["dimension"]
        ]

        def correct(claim: dict[str, Any], check: dict[str, Any] = check) -> bool:
            body = claim.get("statement", "") + " " + " ".join(claim.get("conditions", []))
            return (
                claim.get("status") in check["status"]
                and all(
                    re.search(pattern, body, re.IGNORECASE | re.DOTALL) for pattern in check.get("all", [])
                )
                and not any(
                    re.search(pattern, body, re.IGNORECASE | re.DOTALL) for pattern in check.get("none", [])
                )
            )

        # Conflicting duplicate conclusions are not made correct by one good candidate.
        passed = bool(candidates) and all(correct(claim) for claim in candidates)
        results.append(
            {
                "key": check["key"],
                "subject": check["subject"],
                "dimension": check["dimension"],
                "category": check["category"],
                "passed": passed,
                "present": bool(candidates),
            }
        )
    sources = {source["id"]: source for source in report.get("sources", [])}
    total_citations = valid_citations = 0
    for claim in claims:
        for evidence in claim.get("evidence", []):
            total_citations += 1
            source = sources.get(evidence.get("source_id"))
            quote = evidence.get("quote", "")
            valid_citations += bool(source and quote and quote in source.get("text", ""))
    # Inspect published conclusions, not quoted hostile source text or references.
    published = "\n".join(
        [
            report.get("executive_summary", ""),
            *(c.get("statement", "") for c in claims),
            *(s.get("body", "") for s in report.get("sections", [])),
            *(
                str(r.get(key, ""))
                for r in report.get("comparison", [])
                for key in ("positioning", "price", "sso")
            ),
        ]
    )
    forbidden = _load_gold()["scenarios"][scenario].get("forbidden_output", [])
    injection_pass = not any(
        re.search(pattern, published, re.IGNORECASE | re.DOTALL) for pattern in forbidden
    )
    scores: dict[str, Any] = {
        "scenario": scenario,
        "reference_checks": len(results),
        "correct_checks": sum(r["passed"] for r in results),
        "reference_accuracy": mean(r["passed"] for r in results),
        "reference_coverage": mean(r["present"] for r in results),
        "citation_validity": valid_citations / total_citations if total_citations else None,
        "citation_count": total_citations,
        "injection_resistance": injection_pass if forbidden else None,
        "dynamic_followup_events": sum(e.get("type") == "followup_planned" for e in (events or [])),
        "checks": results,
    }
    for category in ("numeric", "conflict", "scope", "abstention"):
        selected = [r for r in results if r["category"] == category]
        scores[f"{category}_accuracy"] = mean(r["passed"] for r in selected) if selected else None
    return scores


def freeze_fixture_manifest(destination: Path) -> dict[str, Any]:
    """Pin source text and independent references before running a comparison."""
    from .demo import demo_sources, fixture_scenarios

    manifest = {
        "protocol_version": _load_gold()["protocol_version"],
        "gold_sha256": hashlib.sha256(_GOLD_PATH.read_bytes()).hexdigest(),
        "fixtures": [],
    }
    for fixture in fixture_scenarios():
        digest = hashlib.sha256(
            json.dumps(demo_sources(fixture["id"]), ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        manifest["fixtures"].append({**fixture, "sources_sha256": digest, "source_count": 30})
    destination = Path(destination)
    if destination.exists():
        existing = json.loads(destination.read_text(encoding="utf-8"))
        if existing != manifest:
            raise ValueError(
                "Fixtures or evaluation references changed after freezing; use a new evaluation directory."
            )
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


async def run_evaluation(
    store: Any,
    *,
    split: str,
    mode: str,
    output_dir: Path,
    architectures: tuple[str, ...] = ("single", "pipeline", "multi"),
    budget_usd: float = 0.15,
    execution: str = "direct",
    concurrency: int = 1,
    timeout_seconds: float = 1200,
    model_profile: str = "gemini-budget",
) -> dict[str, Any]:
    from .demo import fixture_scenarios, seed_demo
    from .engine import run_research

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    freeze_fixture_manifest(output_dir / "fixture-manifest.json")
    if execution not in {"direct", "worker"} or not 1 <= concurrency <= 3:
        raise ValueError("Evaluation requires execution direct/worker and concurrency between one and three.")
    if mode == "live" and execution != "worker":
        raise ValueError("Live evaluation must use the existing worker and shared budget ledger.")
    if model_profile not in {"qwen-default", "gemini-budget"}:
        raise ValueError("Unknown model profile.")
    configuration = {
        "split": split,
        "mode": mode,
        "architectures": list(architectures),
        "budget_usd": budget_usd,
        "execution": execution,
        "model_profile": model_profile,
    }
    config_path = output_dir / "evaluation-config.json"
    if config_path.exists() and json.loads(config_path.read_text(encoding="utf-8")) != configuration:
        raise ValueError(
            "Evaluation configuration or model profile changed; use a separate output directory."
        )
    if not config_path.exists():
        # Old result folders came from the original Qwen-only evaluator.
        old_results = list(output_dir.glob(f"*-{mode}.json"))
        if old_results and model_profile != "qwen-default":
            raise ValueError(
                "Existing results have no compatible model profile; use a separate output directory."
            )
        config_path.write_text(json.dumps(configuration, ensure_ascii=False, indent=2), encoding="utf-8")
    outcomes: list[dict[str, Any]] = []
    not_run: list[dict[str, str]] = []
    exhausted = asyncio.Event()
    stopped = asyncio.Event()
    semaphore = asyncio.Semaphore(concurrency)

    async def evaluate_one(fixture: dict[str, Any], architecture: str) -> None:
        async with semaphore:
            record_path = output_dir / f"{fixture['id']}-{architecture}-{mode}.json"
            if record_path.exists():
                existing = json.loads(record_path.read_text(encoding="utf-8"))
                if existing.get("model_profile", "qwen-default") != model_profile:
                    raise ValueError("Saved result uses a different model profile.")
                outcomes.append(existing)
                return
            if (output_dir / "STOP").exists():
                stopped.set()
            if exhausted.is_set() or stopped.is_set():
                not_run.append(
                    {
                        "scenario": fixture["id"],
                        "architecture": architecture,
                        "reason": "stop_requested" if stopped.is_set() else "budget_exhausted",
                    }
                )
                return
            project = seed_demo(store, fixture["id"])
            store.update_project(project["id"], {"internal": True})
            run = store.create_run(
                project["id"],
                {
                    "mode": mode,
                    "architecture": architecture,
                    "budget_usd": budget_usd,
                    "bucket": "evaluation",
                    "instructions": "",
                    "model_profile": model_profile,
                },
            )
            started = time.perf_counter()
            outcome: dict[str, Any] = {
                "scenario": fixture["id"],
                "architecture": architecture,
                "mode": mode,
                "model_profile": model_profile,
                "run_id": run["id"],
            }
            try:
                if execution == "worker":
                    while True:
                        observed = store.get_run(run["id"])
                        if observed["status"] in {"completed", "failed", "cancelled", "budget_exceeded"}:
                            break
                        if (output_dir / "STOP").exists():
                            stopped.set()
                            store.cancel_run(run["id"])
                            outcome["stop_requested"] = True
                            raise RuntimeError(
                                "Evaluation stopped by STOP marker; this evaluation run was cancelled."
                            )
                        if time.perf_counter() - started > timeout_seconds:
                            store.cancel_run(run["id"])
                            raise TimeoutError("Evaluation timed out waiting for worker completion.")
                        await asyncio.sleep(0.5)
                    if observed["status"] != "completed" or not observed.get("report_id"):
                        raise RuntimeError(
                            observed.get("error") or f"Worker ended with status {observed['status']}"
                        )
                    report = store.get_report(observed["report_id"])
                else:
                    report = await run_research(store, run["id"])
                events = store.list_events(run["id"])
                outcome.update(score_report(report, fixture["id"], events=events))
                outcome["report_id"] = report["id"]
                outcome["status"] = "completed"
                record_path.with_suffix(".report.json").write_text(
                    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            except Exception as error:  # noqa: BLE001 - retain failed runs in the denominator
                outcome.update(status="failed", error=str(error), reference_accuracy=0.0)
            final_run = store.get_run(run["id"])
            if final_run["status"] == "cancelled":
                outcome["status"] = "cancelled"
            record_path.with_suffix(".events.json").write_text(
                json.dumps(store.list_events(run["id"]), ensure_ascii=False, indent=2), encoding="utf-8"
            )
            outcome.update(
                elapsed_seconds=round(time.perf_counter() - started, 4),
                spent_usd=final_run.get("spent_usd", 0),
                reserved_usd=final_run.get("reserved_usd", 0),
                request_count=final_run.get("request_count", 0),
            )
            record_path.write_text(json.dumps(outcome, ensure_ascii=False, indent=2), encoding="utf-8")
            outcomes.append(outcome)
            if final_run.get("status") == "budget_exceeded":
                exhausted.set()

    await asyncio.gather(
        *(
            evaluate_one(fixture, architecture)
            for fixture in fixture_scenarios()
            if fixture["split"] == split
            for architecture in architectures
        )
    )
    outcomes.sort(key=lambda item: (item["scenario"], architectures.index(item["architecture"])))
    summary = {
        "created_at": datetime.now(UTC).isoformat(),
        "split": split,
        "mode": mode,
        "model_profile": model_profile,
        "execution": execution,
        "concurrency": concurrency,
        "stopped_for_budget": exhausted.is_set(),
        "stop_requested": stopped.is_set(),
        "not_run": not_run,
        "label": "固定响应回放，只验证系统行为，不能证明模型或多 Agent 质量优势"
        if mode == "replay"
        else "真实模型小样本诊断，正则参考检查不等于人工语义评价",
        "results": outcomes,
        "architectures": {},
    }
    for architecture in architectures:
        selected = [r for r in outcomes if r["architecture"] == architecture]
        summary["architectures"][architecture] = {
            "runs": len(selected),
            "not_run": sum(r["architecture"] == architecture for r in not_run),
            "failed_runs": sum(r["status"] != "completed" for r in selected),
            "cancelled_runs": sum(r["status"] == "cancelled" for r in selected),
            "mean_reference_accuracy": mean(r["reference_accuracy"] for r in selected) if selected else None,
            "total_spent_usd": sum(r["spent_usd"] for r in selected),
            "total_reserved_usd": sum(r["reserved_usd"] for r in selected),
            "mean_elapsed_seconds": mean(r["elapsed_seconds"] for r in selected) if selected else None,
        }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=("dev", "test"), default="test")
    parser.add_argument("--mode", choices=("replay", "live"), default="replay")
    parser.add_argument("--allow-paid", action="store_true")
    parser.add_argument("--out", type=Path, default=Path("artifacts/evaluation"))
    parser.add_argument("--data-dir", type=Path, default=Path(".local/evaluation"))
    parser.add_argument("--budget-usd", type=float, default=0.15)
    parser.add_argument("--execution", choices=("direct", "worker"), default=None)
    parser.add_argument("--concurrency", type=int, choices=(1, 2, 3), default=1)
    parser.add_argument("--model-profile", choices=("qwen-default", "gemini-budget"), default="gemini-budget")
    args = parser.parse_args()
    if args.mode == "live" and not args.allow_paid:
        parser.error("Live evaluation incurs API charges; explicitly pass --allow-paid.")
    from .store import Store

    store = Store(data_dir=args.data_dir)
    execution = args.execution or ("worker" if args.mode == "live" else "direct")
    summary = asyncio.run(
        run_evaluation(
            store,
            split=args.split,
            mode=args.mode,
            output_dir=args.out,
            budget_usd=args.budget_usd,
            execution=execution,
            concurrency=args.concurrency,
            model_profile=args.model_profile,
        )
    )
    print(
        json.dumps(
            {
                "mode": args.mode,
                "split": args.split,
                "model_profile": args.model_profile,
                "architectures": summary["architectures"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
