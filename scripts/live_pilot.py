"""Explicit paid smoke test using the same durable US$10 ledger as the app."""

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import quote

from briefforge.demo import seed_demo
from briefforge.store import Store

ROOT = Path(__file__).resolve().parents[1]


async def main(args):
    if not args.live:
        raise SystemExit("Add --live to authorize this paid smoke test (per-run and total caps apply).")
    secret = ROOT / ".local" / "pg-password.txt"
    if secret.exists() and not os.getenv("BRIEFFORGE_DATABASE_URL"):
        os.environ["BRIEFFORGE_DATABASE_URL"] = (
            "postgresql+psycopg://briefforge:"
            + quote(secret.read_text().strip(), safe="")
            + "@127.0.0.1:55432/briefforge"
        )
    store = Store()
    if args.public:
        p = store.create_project(
            {
                "title": "公开办公协作产品研究",
                "question": "比较Trello、Asana、ClickUp面向中小团队的定位、价格条件与SSO支持。无法核实的内容保留未知。",
                "audience": "产品经理",
                "competitors": ["Trello", "Asana", "ClickUp"],
                "dimensions": ["定位", "价格", "SSO"],
                "mode": "public",
            }
        )
    else:
        p = seed_demo(store)
    run = store.create_run(
        p["id"],
        {
            "mode": "live",
            "model_profile": args.model_profile,
            "architecture": args.architecture,
            "budget_usd": 0.30 if args.public else 0.15,
            "bucket": "web" if args.public else "development",
        },
    )
    print(
        json.dumps(
            {
                "project_id": p["id"],
                "run_id": run["id"],
                "mode": "live",
                "architecture": args.architecture,
                "public": args.public,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    started = time.monotonic()
    # Claim this one job directly, without consuming unrelated user work.
    from briefforge.worker import process_job

    job = store.claim_job()
    if job is None or job["id"] != run["id"]:
        raise RuntimeError("Another queued job exists; run worker first.")
    await process_job(store, job)
    result = store.get_run(run["id"])
    summary = {
        "project_id": p["id"],
        "run_id": run["id"],
        "report_id": result.get("report_id"),
        "mode": "live",
        "model_profile": args.model_profile,
        "public": args.public,
        "architecture": args.architecture,
        "status": result["status"],
        "error": result.get("error"),
        "seconds": round(time.monotonic() - started, 2),
        "request_count": result["request_count"],
        "spent_usd": result["spent_usd"],
        "reserved_usd": result["reserved_usd"],
        "tasks": len(store.list_tasks(run["id"])),
        "budget": store.budget_summary(),
    }
    destination = ROOT / "artifacts" / "live"
    destination.mkdir(parents=True, exist_ok=True)
    (destination / f"{run['id']}.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (destination / f"{run['id']}-events.json").write_text(
        json.dumps(store.list_events(run["id"]), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if result.get("report_id"):
        (destination / f"{run['id']}-report.json").write_text(
            json.dumps(store.get_report(result["report_id"]), ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--public", action="store_true")
    parser.add_argument("--model-profile", choices=["qwen-default", "gemini-budget"], default="qwen-default")
    parser.add_argument("--architecture", choices=["single", "pipeline", "multi"], default="multi")
    args = parser.parse_args()
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main(args))
