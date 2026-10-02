"""Exercise the actual durable graph and job worker against the local database."""

import asyncio
import json
import os
import sys
from pathlib import Path
from urllib.parse import quote

from briefforge.demo import seed_demo
from briefforge.store import Store
from briefforge.worker import worker_loop

ROOT = Path(__file__).resolve().parents[1]


async def main():
    secret = ROOT / ".local" / "pg-password.txt"
    if secret.exists() and not os.getenv("BRIEFFORGE_DATABASE_URL"):
        os.environ["BRIEFFORGE_DATABASE_URL"] = (
            "postgresql+psycopg://briefforge:"
            + quote(secret.read_text().strip(), safe="")
            + "@127.0.0.1:55432/briefforge"
        )
    store = Store()
    p = seed_demo(store)
    run = store.create_run(p["id"], {"mode": "replay", "architecture": "multi"})
    await worker_loop(store, once=True)
    result = store.get_run(run["id"])
    if result["status"] != "completed":
        raise RuntimeError(result.get("error"))
    report = store.get_report(result["report_id"])
    summary = {
        "database": "postgresql" if store.database_url.startswith("postgres") else "sqlite",
        "project_id": p["id"],
        "run_id": run["id"],
        "report_id": report["id"],
        "sources": len(report["sources"]),
        "claims": len(report["claims"]),
        "tasks": len(store.list_tasks(run["id"])),
        "followups": sum(
            t["round"] > 0 and t["role"] not in {"verifier", "editor"} for t in store.list_tasks(run["id"])
        ),
        "status": result["status"],
        "budget": store.budget_summary(),
    }
    output = ROOT / "artifacts" / "integration.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (ROOT / ".local" / "sample-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
