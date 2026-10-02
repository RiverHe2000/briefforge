"""Verify native PostgreSQL recovery and budget races without calling any model.

This creates only briefforge_recovery and a fresh schema per invocation. It never
stops the application or PostgreSQL, and never connects to the application DB.
Ledger-only test runs must have mode='live' because Store rejects reservations
for replay; those jobs are held out of the queue and no provider is invoked.
Their simulated reservations are explicitly marked and settled to zero.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import re
import subprocess
import sys
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psycopg
from psycopg import sql
from sqlalchemy import update
from sqlalchemy.engine import URL, make_url

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from briefforge.demo import seed_demo
from briefforge.engine import ResearchEngine, run_research
from briefforge.store import BudgetExceeded, LeaseLost, Store, digest, jobs
from briefforge.worker import process_job

DATABASE = "briefforge_recovery"
HOST, PORT, USER = "127.0.0.1", 55432, "briefforge"
OUTPUT = ROOT / "artifacts" / "recovery"
EXIT_AFTER_DURABLE_TASK = 86


def credentials() -> dict:
    return {
        "host": HOST,
        "port": PORT,
        "user": USER,
        "password": (ROOT / ".local" / "pg-password.txt").read_text(encoding="utf-8").strip(),
    }


def connection_url(schema: str) -> str:
    if not re.fullmatch(r"recovery_[a-f0-9]{12}", schema):
        raise ValueError("Unexpected verification schema")
    params = credentials()
    url = URL.create(
        "postgresql+psycopg",
        username=params["user"], password=params["password"],
        host=HOST, port=PORT, database=DATABASE,
        query={"options": f"-csearch_path={schema}"},
    ).render_as_string(hide_password=False)
    assert make_url(url).database == DATABASE
    return url


def create_isolated_schema(schema: str) -> None:
    with psycopg.connect(dbname="postgres", autocommit=True, **credentials()) as conn:
        if not conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DATABASE,)).fetchone():
            conn.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(DATABASE), sql.Identifier(USER)))
    with psycopg.connect(dbname=DATABASE, autocommit=True, **credentials()) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))


def write_json(name: str, value: dict) -> Path:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    target = OUTPUT / name
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def new_store(schema: str) -> Store:
    return Store(connection_url(schema), OUTPUT)


def interrupt_child(schema: str, run_id: str, marker_name: str) -> None:
    """Fault is after the real transaction committed, with no graceful teardown."""
    store = new_store(schema)
    job = store.claim_job(lease_seconds=90)
    assert job and job["id"] == run_id and job["kind"] == "run"
    assert store.get_run(run_id)["mode"] == "replay"
    original = ResearchEngine.task
    armed = False

    async def task_then_exit(self, role, target, title, round_, operation, **kwargs):
        nonlocal armed
        output = await original(self, role, target, title, round_, operation, **kwargs)
        if self.run_id == run_id and role in {"industry", "competitor", "commercial"} and not armed:
            armed = True
            result = next(task for task in self.store.list_tasks(run_id) if task["role"] == role and task["target"] == target and task["title"] == title and task["round"] == round_)
            assert result["status"] == "completed"
            # Saver writes run asynchronously. Yield while this research task's
            # wrapper is still unfinished, so the research graph node cannot
            # return and produce a report before the injected abrupt exit.
            checkpoint_count = 0
            for _ in range(100):
                with psycopg.connect(dbname=DATABASE, options=f"-csearch_path={schema}", **credentials()) as conn:
                    checkpoint_count = conn.execute("SELECT count(*) FROM checkpoints WHERE thread_id = %s", (run_id,)).fetchone()[0]
                if checkpoint_count:
                    break
                await asyncio.sleep(0.05)
            assert checkpoint_count > 0, "Earlier graph checkpoint did not flush"
            write_json(marker_name, {
                "fault": "os._exit after committed research task output, while research graph node remains incomplete",
                "child_pid": os.getpid(), "run_id": run_id,
                "task_id": result["id"], "role": result["role"],
                "output_hash": digest(result["output"]),
                "task_updated_at": result["updated_at"],
                "lease_token": job["token"], "exit_code": EXIT_AFTER_DURABLE_TASK,
            })
            os._exit(EXIT_AFTER_DURABLE_TASK)
        return output

    ResearchEngine.task = task_then_exit
    asyncio.run(process_job(store, job))
    raise AssertionError("Fault injection did not execute")


def run_owned_child(schema: str, run_id: str, marker_name: str) -> dict:
    env = dict(os.environ)
    env.pop("OPENROUTER_API_KEY", None)
    command = [sys.executable, str(Path(__file__).resolve()), "--child-run", run_id, "--schema", schema, "--marker", marker_name]
    process = subprocess.Popen(
        command, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    try:
        stdout, stderr = process.communicate(timeout=90)
    except subprocess.TimeoutExpired:
        # This PID is the verification child created immediately above, never an app service.
        process.kill()
        process.communicate()
        raise AssertionError("Owned fault-injection child timed out") from None
    if process.returncode != EXIT_AFTER_DURABLE_TASK:
        raise AssertionError(f"Fault child exited {process.returncode}: {(stdout + stderr)[-3000:]}")
    return json.loads((OUTPUT / marker_name).read_text(encoding="utf-8"))


def verify_recovery(schema: str, label: str) -> dict:
    base = new_store(schema)
    project = seed_demo(base)
    base.update_project(project["id"], {"title": f"PostgreSQL recovery verification {label}"})
    run = base.create_run(project["id"], {"mode": "replay", "architecture": "multi", "budget_usd": 0.15})
    marker = run_owned_child(schema, run["id"], f"fault-{label}.json")
    persisted_before = base.list_tasks(run["id"])
    completed_before = {task["id"]: copy.deepcopy(task) for task in persisted_before if task["status"] == "completed"}
    assert marker["task_id"] in completed_before
    assert completed_before[marker["task_id"]]["output"]
    assert base.get_run(run["id"])["status"] == "running"
    assert base.list_reports(project["id"]) == []
    with psycopg.connect(dbname=DATABASE, options=f"-csearch_path={schema}", **credentials()) as conn:
        checkpoints_before = conn.execute("SELECT count(*) FROM checkpoints WHERE thread_id = %s", (run["id"],)).fetchone()[0]
    assert checkpoints_before > 0
    before_events = base.list_events(run["id"])
    initial_starts = {task_id: sum(event["type"] == "task_started" and event["payload"].get("task_id") == task_id for event in before_events) for task_id in completed_before}

    # Expire exactly the dead child's own lease; leave all other rows untouched.
    with base.engine.begin() as conn:
        changed = conn.execute(update(jobs).where(jobs.c.id == run["id"], jobs.c.token == marker["lease_token"], jobs.c.status == "running").values(expires=time.time() - 1)).rowcount
    assert changed == 1
    fresh = new_store(schema)
    recovered_job = fresh.claim_job()
    assert recovered_job and recovered_job["id"] == run["id"]
    assert recovered_job["token"] != marker["lease_token"]
    stale = new_store(schema)
    stale.lease_id, stale.lease_token = run["id"], marker["lease_token"]
    fenced = False
    try:
        stale.update_run(run["id"], {"phase": "this stale write must fail"})
    except LeaseLost:
        fenced = True
    assert fenced
    stale.engine.dispose()
    asyncio.run(process_job(fresh, recovered_job))
    finished = fresh.get_run(run["id"])
    assert finished["status"] == "completed", finished.get("error")
    final_tasks = {task["id"]: task for task in fresh.list_tasks(run["id"])}
    final_events = fresh.list_events(run["id"])
    for task_id, before in completed_before.items():
        after = final_tasks[task_id]
        assert after["output"] == before["output"]
        assert after["updated_at"] == before["updated_at"]
        assert sum(event["type"] == "task_started" and event["payload"].get("task_id") == task_id for event in final_events) == initial_starts[task_id]
    reports = fresh.list_reports(project["id"])
    assert len(reports) == 1
    report = reports[0]
    assert report["claims"] and report["sources"] and report["mode"] == "replay"
    # The completed checkpoint fast path must return the same immutable report.
    task_count = len(final_tasks)
    repeated = asyncio.run(run_research(new_store(schema), run["id"]))
    assert repeated["id"] == report["id"]
    assert len(fresh.list_reports(project["id"])) == 1
    assert len(fresh.list_tasks(run["id"])) == task_count
    assert finished["request_count"] == 0 and finished["spent_usd"] == 0
    result = {
        "status": "passed", "actual_fault": "abrupt child process exit (os._exit), not an exception simulation",
        "project_id": project["id"], "run_id": run["id"], "report_id": report["id"],
        "exit_code": EXIT_AFTER_DURABLE_TASK, "checkpoints_before_recovery": checkpoints_before,
        "completed_task_ids_before_crash": list(completed_before), "completed_outputs_reused_without_task_restart": True,
        "new_lease_token": True, "stale_worker_fenced": fenced,
        "final_task_count": task_count, "report_count_for_run": 1, "completed_graph_reentry_idempotent": True,
        "model_requests": 0, "actual_provider_cost_usd": 0,
    }
    result["source_version_immutability"] = verify_source_versions(fresh, project, report)
    fresh.engine.dispose()
    base.engine.dispose()
    return result


def verify_source_versions(store: Store, project: dict, original_report: dict) -> dict:
    frozen = copy.deepcopy(original_report)
    source = next(source for source in original_report["sources"] if source["kind"] == "pricing" and "USD 12/席位/月" in source["text"])
    replacement = store.add_source(project["id"], {
        "logical_key": source["logical_key"], "title": source["title"], "competitor": source["competitor"],
        "kind": source["kind"], "published_at": "2026-10-02", "url": source.get("url"),
        "text": source["text"].replace("USD 12/席位/月", "USD 17/席位/月"),
    })
    assert replacement["version"] == source["version"] + 1
    old = store.get_source(source["id"])
    assert old["text"] == source["text"] and not old["active"]
    dirty = [claim for claim in store.list_claims(project["id"]) if source["id"] in claim["source_ids"]]
    assert dirty and all(claim["dirty"] for claim in dirty)
    assert store.get_report(original_report["id"]) == frozen
    revision = store.create_run(project["id"], {"mode": "replay", "architecture": "multi", "budget_usd": 0.15})
    assert replacement["id"] in revision["source_ids"] and source["id"] not in revision["source_ids"]
    job = store.claim_job()
    assert job and job["id"] == revision["id"]
    asyncio.run(process_job(store, job))
    finished = store.get_run(revision["id"])
    assert finished["status"] == "completed", finished.get("error")
    report = store.get_report(finished["report_id"])
    assert report["version"] == original_report["version"] + 1
    assert store.get_report(original_report["id"]) == frozen
    assert any(s["id"] == replacement["id"] and "USD 17/席位/月" in s["text"] for s in report["sources"])
    assert any(claim["subject"] == source["competitor"] and claim["dimension"] == "价格" and "17" in claim["statement"] for claim in report["claims"])
    return {
        "status": "passed", "old_source_id": source["id"], "replacement_source_id": replacement["id"],
        "old_version": source["version"], "new_version": replacement["version"],
        "original_report_hash_unchanged": store.get_report(original_report["id"])["content_hash"] == frozen["content_hash"],
        "original_report_json_unchanged": True, "affected_claims_marked_dirty": len(dirty),
        "new_report_id": report["id"], "new_report_uses_new_price": True,
    }


def ledger_run(store: Store, label: str, bucket: str, cap: float = 0.30) -> dict:
    project = store.create_project({"title": f"LEDGER SIMULATION ONLY {label}", "competitors": ["simulation"], "mode": "synthetic"})
    run = store.create_run(project["id"], {
        "mode": "live", "budget_usd": cap, "bucket": bucket,
        "instructions": "Accounting concurrency simulation only. Do not invoke any provider or process this job.",
    })
    with store.engine.begin() as conn:
        conn.execute(update(jobs).where(jobs.c.id == run["id"]).values(status="test_only"))
    return run


def reserve_concurrently(schema: str, requests: list[tuple[str, float, str]]) -> list[dict]:
    gate = threading.Barrier(min(16, len(requests)))
    def attempt(item):
        run_id, amount, key = item
        store = new_store(schema)
        try:
            # Synchronize each full worker wave to force racing independent connections.
            gate.wait(timeout=30)
            try:
                reservation = store.reserve_cost(run_id, amount, key)
                return {"run_id": run_id, "status": "reserved", **reservation}
            except BudgetExceeded as error:
                return {"run_id": run_id, "status": "blocked", "reason": str(error)}
        finally:
            store.engine.dispose()
    # Use exact waves so the last partial wave cannot deadlock at the barrier.
    results = []
    for start in range(0, len(requests), 16):
        wave = requests[start:start+16]
        gate = threading.Barrier(len(wave))
        with ThreadPoolExecutor(max_workers=len(wave)) as executor:
            results.extend(executor.map(attempt, wave))
    return results


def release_simulation(store: Store, results: list[dict]) -> None:
    for result in results:
        if result["status"] == "reserved":
            store.settle_cost(result["id"], 0.0, {"simulation_only": True, "provider_called": False, "purpose": "concurrent reservation verification"})


def verify_budget_races(schema: str, label: str) -> dict:
    store = new_store(schema)
    assert store.budget_summary()["spent_usd"] == 0
    single = ledger_run(store, label + "-per-run", "development", 0.15)
    results = reserve_concurrently(schema, [(single["id"], 0.02, f"per-run-{n}") for n in range(16)])
    accepted = [result for result in results if result["status"] == "reserved"]
    assert len(accepted) == 7
    balance = store.get_run(single["id"])
    assert round(balance["reserved_usd"], 6) == 0.14 <= balance["budget_usd"]
    count_before = balance["request_count"]
    sample_key = next(f"per-run-{n}" for n, result in enumerate(results) if result["status"] == "reserved")
    reused = store.reserve_cost(single["id"], 0.02, sample_key)
    assert reused["reused"] and store.get_run(single["id"])["request_count"] == count_before
    store.settle_cost(accepted[0]["id"], None, {"simulation_only": True, "provider_called": False})
    assert round(store.get_run(single["id"])["reserved_usd"], 6) == 0.14
    per_run = {"requests_racing": 16, "accepted": 7, "blocked": 9, "reserved_usd": 0.14, "cap_usd": 0.15, "duplicate_key_reused": True, "unknown_cost_keeps_reservation": True}
    release_simulation(store, results)

    # Independent runs contend on one shared ledger row across every budget bucket.
    caps = {"development": 2, "evaluation": 4, "web": 2, "reserve": 2}
    requests = []
    for bucket, cap in caps.items():
        for number in range(cap * 4 + 4):
            run = ledger_run(store, f"{label}-{bucket}-{number}", bucket)
            requests.append((run["id"], 0.25, "global-race"))
    results = reserve_concurrently(schema, requests)
    snapshot = store.budget_summary()
    assert snapshot["spent_usd"] == 0 and snapshot["reserved_usd"] == 10.0
    for bucket, cap in caps.items():
        assert snapshot["buckets"][bucket]["reserved_usd"] == cap
    assert sum(result["status"] == "reserved" for result in results) == 40
    assert sum(result["status"] == "blocked" for result in results) == 16
    global_race = {"requests_racing_in_waves_of": 16, "requests_total": len(requests), "accepted": 40, "blocked": 16, "ledger_snapshot_simulated_not_model_cost": snapshot}
    release_simulation(store, results)

    bounded = ledger_run(store, label + "-request-count", "development")
    results = reserve_concurrently(schema, [(bounded["id"], 0.001, f"request-count-{n}") for n in range(48)])
    assert sum(result["status"] == "reserved" for result in results) == 40
    assert store.get_run(bounded["id"])["request_count"] == 40
    limit = {"attempts": 48, "accepted": 40, "blocked": 8, "maximum_requests": 40}
    release_simulation(store, results)
    final = store.budget_summary()
    assert final["spent_usd"] == 0 and final["reserved_usd"] == 0
    store.engine.dispose()
    return {
        "status": "passed", "note": "Isolated accounting simulation only. Zero model/provider HTTP requests. Reserved amounts are NOT actual model costs. Test reservations settled to zero after assertions.",
        "per_run_cap": per_run, "global_and_bucket_caps": global_race, "request_count_cap": limit,
        "provider_requests": 0, "actual_provider_cost_usd": 0, "final_isolated_ledger": final,
    }


def main() -> int:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    os.environ.pop("OPENROUTER_API_KEY", None)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child-run")
    parser.add_argument("--schema")
    parser.add_argument("--marker")
    args = parser.parse_args()
    if args.child_run:
        assert args.schema and args.marker and re.fullmatch(r"fault-[a-f0-9]{12}\.json", args.marker)
        interrupt_child(args.schema, args.child_run, args.marker)
        return 1
    label = uuid.uuid4().hex[:12]
    schema = f"recovery_{label}"
    result = {"verification_id": label, "database": DATABASE, "schema": schema, "database_engine": "native PostgreSQL", "production_database_accessed": False, "application_services_stopped": False}
    started = time.perf_counter()
    try:
        create_isolated_schema(schema)
        result["recovery"] = verify_recovery(schema, label)
        print("PASS abrupt-process recovery, completed-output reuse, lease fencing, report idempotency and source immutability")
        result["budget_concurrency"] = verify_budget_races(schema, label)
        print("PASS concurrent per-run, per-bucket, global and request-count caps (accounting simulation only)")
        result["status"] = "passed"
    except Exception as error:  # noqa: BLE001 - retain sanitized diagnostics for every verification failure
        result["status"] = "failed"
        secret = credentials()["password"]
        result["error"] = f"{type(error).__name__}: {error}".replace(secret, "[REDACTED]")
        result["traceback"] = traceback.format_exc().replace(secret, "[REDACTED]")
        print(result["error"])
        print(result["traceback"])
    result["duration_seconds"] = round(time.perf_counter() - started, 3)
    path = write_json(f"verification-{label}.json", result)
    write_json("latest.json", result)
    print(f"Evidence: {path.relative_to(ROOT)}")
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
