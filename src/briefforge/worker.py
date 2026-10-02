from __future__ import annotations

import asyncio
import logging

from .store import BudgetExceeded, LeaseLost, RunCancelled, Store

log = logging.getLogger("briefforge.worker")


async def process_job(base: Store, job: dict):
    store = Store(base.database_url, base.data_dir)
    store.lease_id, store.lease_token = job["id"], job["token"]

    async def pulse():
        while True:
            await asyncio.sleep(10)
            if not base.heartbeat(job["id"], job["token"]):
                return

    heartbeat = asyncio.create_task(pulse())
    status = "completed"
    try:
        if job["kind"] == "run":
            from .engine import run_research

            store.assert_run_active(job["id"])
            store.update_run(job["id"], {"status": "running"})
            report = await run_research(store, job["id"])
            store.assert_run_active(job["id"])
            store.update_run(
                job["id"], {"status": "completed", "report_id": report["id"], "phase": "completed"}
            )
            store.add_event(job["id"], "completed", "研究完成，报告已保存", {"report_id": report["id"]})
        else:
            from .exports import export_report

            export = store.get_export(job["id"])
            store.update_export(job["id"], {"status": "running"})
            report = store.get_report(export["report_id"])
            path = await asyncio.to_thread(
                export_report,
                report,
                export["format"],
                store.data_dir / "exports" / export["id"] / job["token"],
            )
            store.update_export(
                job["id"],
                {
                    "status": "completed",
                    "path": str(path.resolve()),
                    "download_url": f"/api/exports/{job['id']}/download",
                },
            )
    except (RunCancelled, LeaseLost):
        status = "cancelled"
    except Exception as exc:  # noqa: BLE001 - persist all job failures at the worker boundary
        status = "failed"
        message = f"{type(exc).__name__}: {exc}"[:1600]
        # Only error classes/messages are persisted; providers must redact their
        # errors. Never dump headers, environment or model request objects.
        log.error("Job %s failed: %s", job["id"], message)
        try:
            if job["kind"] == "run":
                store.update_run(
                    job["id"],
                    {
                        "status": "budget_exceeded" if isinstance(exc, BudgetExceeded) else "failed",
                        "error": message,
                    },
                )
                store.add_event(job["id"], "failed", message)
                project = store.get_project(store.get_run(job["id"])["project_id"])
                store.update_project(
                    project["id"], {"status": "ready" if project.get("latest_report_id") else "draft"}
                )
            else:
                store.update_export(job["id"], {"status": "failed", "error": message})
        except (RunCancelled, LeaseLost):
            pass
    finally:
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
        base.finish_job(job["id"], job["token"], status)
        store.engine.dispose()


async def worker_loop(store: Store | None = None, once: bool = False):
    store = store or Store()
    while True:
        job = store.claim_job()
        if job:
            await process_job(store, job)
        if once:
            return
        if not job:
            await asyncio.sleep(0.75)
