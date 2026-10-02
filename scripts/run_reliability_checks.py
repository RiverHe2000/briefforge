"""Run a preregistered, source-identical reliability matrix on the shared worker queue."""

import argparse
import asyncio
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean

from briefforge.collaboration import collaboration_summary
from briefforge.evaluation_v2 import compare_outcomes, score_fact_report
from briefforge.reliability import apply_reliability_update, freeze_reliability_manifest, seed_reliability
from briefforge.store import Store

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "data/evaluation/reliability-v1"


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


async def execute(args):
    store = Store()
    output = args.out
    output.mkdir(parents=True, exist_ok=True)
    manifest = freeze_reliability_manifest(output / "fixture-manifest.json", SPEC)
    fixture_hashes = {item["id"]: item for item in manifest["scenarios"]}
    config = {
        "mode": args.mode,
        "model_profile": "gemini-budget",
        "temperature": 0,
        "repeats": args.repeats,
        "architectures": ["single", "pipeline", "multi"],
        "budget_usd": 0.15,
        "concurrent_runs": 3,
        "quality_runs": args.repeats * 6,
        "update_runs": 9,
        "note": "Quality repetitions plus before/partial-update/full-rerun for each architecture. Queue time included.",
        "code_sha256": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in (
                "src/briefforge/engine.py",
                "src/briefforge/provider.py",
                "src/briefforge/store.py",
                "src/briefforge/collaboration.py",
                "src/briefforge/evaluation_v2.py",
                "src/briefforge/reliability.py",
            )
        },
    }
    config_path = output / "implementation-manifest.json"
    if config_path.exists():
        assert json.loads(config_path.read_text(encoding="utf-8")) == config, "Use a new output directory."
    else:
        save(config_path, config)
    gate = asyncio.Semaphore(3)

    async def one(scenario, architecture, label, project=None, instructions=""):
        filename = f"{label}-{architecture}"
        record = output / f"{filename}.json"
        if record.exists():
            return json.loads(record.read_text(encoding="utf-8"))
        async with gate:
            pending = output / f"{filename}.pending.json"
            if pending.exists():
                saved = json.loads(pending.read_text(encoding="utf-8"))
                run = store.get_run(saved["run_id"])
                project = store.get_project(run["project_id"])
            else:
                project = project or seed_reliability(store, scenario)
                run = store.create_run(
                    project["id"],
                    {
                        "mode": args.mode,
                        "model_profile": "gemini-budget",
                        "architecture": architecture,
                        "budget_usd": 0.15,
                        "bucket": "evaluation",
                        "instructions": instructions,
                    },
                    idempotency_key=f"reliability:{output.name}:{filename}",
                )
                save(pending, {"run_id": run["id"], "project_id": project["id"]})
            started = time.monotonic()
            while run["status"] in {"queued", "running"}:
                if time.monotonic() - started > 1200:
                    store.cancel_run(run["id"])
                await asyncio.sleep(1)
                run = store.get_run(run["id"])
            spec_path = SPEC / f"{scenario}.json"
            outcome = {
                "scenario": label,
                "source_scenario": scenario,
                "architecture": architecture,
                "project_id": project["id"],
                "run_id": run["id"],
                "report_id": run.get("report_id"),
                "status": run["status"],
                "error": run.get("error"),
                "mode": args.mode,
                "model_profile": run["model_profile"],
                "spent_usd": run["spent_usd"],
                "reserved_usd": run["reserved_usd"],
                "request_count": run["request_count"],
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "sources_sha256": fixture_hashes[scenario]["sources_sha256"],
                "spec_sha256": hashlib.sha256(spec_path.read_bytes()).hexdigest(),
            }
            save(record, outcome)
            # Capture execution failures too; a missing report is not filtered out.
            save(output / f"{filename}.events.json", store.list_events(run["id"]))
            save(output / f"{filename}.tasks.json", store.list_tasks(run["id"]))
            save(output / f"{filename}.collaboration.json", collaboration_summary(store, run["id"]))
            if run.get("report_id"):
                report = store.get_report(run["report_id"])
                save(output / f"{filename}.report.json", report)
                outcome["metrics_v2"] = score_fact_report(
                    report, json.loads(spec_path.read_text(encoding="utf-8"))
                )
                save(record, outcome)
            print(
                json.dumps({k: outcome[k] for k in ("scenario", "architecture", "status", "spent_usd")}),
                flush=True,
            )
            return outcome

    quality = await asyncio.gather(
        *(
            one(scenario, architecture, f"{scenario}-r{repeat}")
            for repeat in range(1, args.repeats + 1)
            for scenario in ("challenge-current-conflict", "challenge-faq")
            for architecture in config["architectures"]
        )
    )

    async def updates(architecture):
        before = await one("challenge-update-before", architecture, "update-before")
        if before["status"] != "completed":
            return {"architecture": architecture, "before": before, "status": "skipped_after_failed_initial"}
        project = store.get_project(before["project_id"])
        immutable_before = store.get_report(before["report_id"])
        digest = hashlib.sha256(json.dumps(immutable_before, sort_keys=True).encode()).hexdigest()
        replacement = apply_reliability_update(store, project["id"])
        after = await one("challenge-update-after", architecture, "update-partial", project)
        # Same project/source IDs, explicit instructions force a full investigation.
        full = await one(
            "challenge-update-after",
            architecture,
            "update-full",
            project,
            "重新完整研究全部竞品与维度，逐条核查当前资料，不复用旧报告结论。",
        )
        assert (
            digest
            == hashlib.sha256(
                json.dumps(store.get_report(before["report_id"]), sort_keys=True).encode()
            ).hexdigest()
        )
        partial_sources = store.get_run(after["run_id"])["source_ids"]
        full_sources = store.get_run(full["run_id"])["source_ids"]
        assert partial_sources == full_sources, "Update comparison inputs changed."
        return {
            "architecture": architecture,
            "before": before,
            "partial": after,
            "full": full,
            "old_report_unchanged": True,
            "same_final_source_ids": True,
            "replacement_source_id": replacement["id"],
            "replacement_version": replacement["version"],
            "partial_vs_full_request_difference": after["request_count"] - full["request_count"],
            "partial_vs_full_confirmed_usd_difference": round(after["spent_usd"] - full["spent_usd"], 6),
        }

    update_results = await asyncio.gather(*(updates(a) for a in config["architectures"]))
    all_runs = quality + [
        entry
        for group in update_results
        for key, entry in group.items()
        if key in {"before", "partial", "full"}
    ]
    result = {
        "created_at": datetime.now(UTC).isoformat(),
        "mode": args.mode,
        "quality": quality,
        "quality_comparison": compare_outcomes(quality),
        "updates": update_results,
        "runs": len(all_runs),
        "completed": sum(r["status"] == "completed" for r in all_runs),
        "spent_usd": round(sum(r["spent_usd"] for r in all_runs), 6),
        "reserved_usd": round(sum(r["reserved_usd"] for r in all_runs), 6),
        "quality_architectures": {},
    }
    for architecture in config["architectures"]:
        selected = [r for r in quality if r["architecture"] == architecture]
        if not selected:
            continue
        result["quality_architectures"][architecture] = {
            "runs": len(selected),
            "completed": sum(r["status"] == "completed" for r in selected),
            "mean_fact_accuracy": mean(r.get("metrics_v2", {}).get("fact_accuracy", 0) for r in selected),
            "mean_answer_coverage": mean(r.get("metrics_v2", {}).get("answer_coverage", 0) for r in selected),
            "spent_usd": round(sum(r["spent_usd"] for r in selected), 6),
        }
    save(output / "summary.json", result)
    print(
        json.dumps(
            {k: result[k] for k in ("runs", "completed", "spent_usd", "quality_architectures")}, indent=2
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("replay", "live"), default="replay")
    parser.add_argument("--allow-paid", action="store_true")
    parser.add_argument(
        "--repeats",
        type=int,
        choices=(0, 1, 2, 3),
        default=2,
        help="Quality repetitions; 0 runs only the nine before/partial/full update checks.",
    )
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.mode == "live" and not args.allow_paid:
        parser.error("Live requests require --allow-paid and the shared application database.")
    asyncio.run(execute(args))


if __name__ == "__main__":
    main()
