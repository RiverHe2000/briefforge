"""Transactional JSON records and a micro-dollar ledger, backed by Postgres or SQLite.

The single ledger row is also the short transaction lock. No network or model work is
performed while it is held. It serializes claims, budgets and worker leases across processes.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import (
    JSON,
    Column,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    insert,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError

from . import config


class BudgetExceeded(RuntimeError):
    pass


class RunCancelled(RuntimeError):
    pass


class Conflict(ValueError):
    pass


class LeaseLost(RuntimeError):
    pass


def now() -> str:
    return datetime.now(UTC).isoformat()


def new_id() -> str:
    return str(uuid.uuid4())


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def micros(value: float) -> int:
    d = Decimal(str(value))
    if not d.is_finite() or d < 0:
        raise ValueError("Invalid cost")
    return int((d * 1_000_000).to_integral_value(rounding=ROUND_CEILING))


metadata = MetaData()
records = Table(
    "bf_records",
    metadata,
    Column("id", String(128), primary_key=True),
    Column("kind", String(32), nullable=False, index=True),
    Column("parent", String(128), nullable=False, index=True),
    Column("created", String(64), nullable=False),
    Column("payload", JSON, nullable=False),
)
ledger = Table(
    "bf_ledger", metadata, Column("id", Integer, primary_key=True), Column("payload", JSON, nullable=False)
)
events = Table(
    "bf_events",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String(128), index=True),
    Column("created", String(64)),
    Column("payload", JSON, nullable=False),
)
reservations = Table(
    "bf_reservations",
    metadata,
    Column("id", String(128), primary_key=True),
    Column("request_key", String(256), unique=True),
    Column("run_id", String(128), index=True),
    Column("bucket", String(32)),
    Column("reserved", Integer),
    Column("actual", Integer, nullable=True),
    Column("payload", JSON),
)
jobs = Table(
    "bf_jobs",
    metadata,
    Column("id", String(128), primary_key=True),
    Column("kind", String(20)),
    Column("status", String(20), index=True),
    Column("token", String(128)),
    Column("expires", Float),
    Column("created", String(64)),
)

BUCKETS = {"development": 2_000_000, "evaluation": 4_000_000, "web": 2_000_000, "reserve": 2_000_000}
ACTIVE = {"queued", "running"}


class Store:
    def __init__(self, database_url: str | None = None, data_dir: Path | str | None = None):
        self.data_dir = Path(data_dir or config.data_dir()).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.database_url = database_url or (
            os.getenv("BRIEFFORGE_DATABASE_URL")
            or (
                f"sqlite:///{(self.data_dir / 'briefforge.db').as_posix()}"
                if data_dir
                else config.database_url()
            )
        )
        args = {"check_same_thread": False, "timeout": 30} if self.database_url.startswith("sqlite") else {}
        self.engine = create_engine(self.database_url, connect_args=args, pool_pre_ping=True)
        self._mutex = threading.RLock()
        self.lease_id: str | None = None
        self.lease_token: str | None = None
        if self.database_url.startswith("sqlite"):
            with self.engine.connect() as c:
                c.exec_driver_sql("PRAGMA journal_mode=WAL")
        metadata.create_all(self.engine)
        try:
            with self.engine.begin() as c:
                if c.execute(select(ledger).where(ledger.c.id == 1)).first() is None:
                    c.execute(insert(ledger).values(id=1, payload={"schema_version": 1, "limit": 10_000_000}))
        except IntegrityError:
            pass

    @contextmanager
    def _tx(self, *, check_lease=True):
        with self._mutex, self.engine.connect() as c:
            if self.database_url.startswith("sqlite"):
                c.exec_driver_sql("BEGIN IMMEDIATE")
            else:
                c.begin()
                c.execute(select(ledger.c.id).where(ledger.c.id == 1).with_for_update())
            try:
                if self.lease_id and check_lease:
                    self._check_lease(c)
                yield c
                c.commit()
            except BaseException:
                c.rollback()
                raise

    def _check_lease(self, c):
        row = c.execute(select(jobs).where(jobs.c.id == self.lease_id)).mappings().first()
        if (
            not row
            or row["token"] != self.lease_token
            or row["status"] != "running"
            or row["expires"] < time.time()
        ):
            raise LeaseLost("任务已交由其他工作进程恢复")

    @staticmethod
    def _get(c, rid: str, kind: str | None = None):
        q = select(records.c.payload).where(records.c.id == rid)
        if kind:
            q = q.where(records.c.kind == kind)
        result = c.execute(q).scalar_one_or_none()
        if result is None:
            raise KeyError(rid)
        return dict(result)

    @staticmethod
    def _put(c, kind: str, obj: dict, parent: str = ""):
        c.execute(
            insert(records).values(
                id=obj["id"], kind=kind, parent=parent, created=obj.get("created_at", now()), payload=obj
            )
        )
        return obj

    @staticmethod
    def _replace(c, obj: dict):
        c.execute(update(records).where(records.c.id == obj["id"]).values(payload=obj))
        return obj

    def _read(self, rid, kind=None):
        with self.engine.connect() as c:
            return self._get(c, rid, kind)

    def _list(self, kind, parent=None):
        with self.engine.connect() as c:
            return self._list_in(c, kind, parent)

    @staticmethod
    def _list_in(c, kind, parent=None):
        q = select(records.c.payload).where(records.c.kind == kind)
        if parent is not None:
            q = q.where(records.c.parent == parent)
        return [dict(x) for x in c.execute(q.order_by(records.c.created)).scalars()]

    def create_project(self, data):
        ts = now()
        obj = {
            "id": new_id(),
            "title": data.get("title", "新研究"),
            "question": data.get("question", ""),
            "audience": data.get("audience", "管理团队"),
            "time_range": data.get("time_range", "最近12个月"),
            "web_enabled": bool(data.get("web_enabled", True)) and data.get("mode", "synthetic") == "public",
            "competitors": data.get("competitors", []),
            "dimensions": data.get(
                "dimensions", ["目标客户", "核心功能", "价格与套餐", "近期变化", "差异化机会"]
            ),
            "mode": data.get("mode", "synthetic"),
            "status": "draft",
            "dirty": False,
            "latest_report_id": None,
            "created_at": ts,
            "updated_at": ts,
        }
        with self._tx() as c:
            return self._put(c, "project", obj)

    def get_project(self, rid):
        return self._read(rid, "project")

    def list_projects(self, include_internal=False):
        return [p for p in self._list("project")[::-1] if include_internal or not p.get("internal", False)]

    def update_project(self, rid, patch):
        with self._tx() as c:
            obj = self._get(c, rid, "project")
            self._replace(c, {**obj, **patch, "id": rid, "updated_at": now()})
        return self.get_project(rid)

    def edit_brief(self, rid, patch):
        with self._tx() as c:
            obj = self._get(c, rid, "project")
            if any(run["status"] in ACTIVE for run in self._list_in(c, "run", rid)):
                raise Conflict("请等待当前研究结束后调整研究目标")
            return self._replace(c, {**obj, **patch, "id": rid, "updated_at": now()})

    def add_source(self, project_id, data):
        with self._tx() as c:
            project = self._get(c, project_id, "project")
            logical = data.get("logical_key") or new_id()
            old = [s for s in self._list_in(c, "source", project_id) if s["logical_key"] == logical]
            text = data.get("text", "")
            if not text.strip():
                raise ValueError("资料没有可读取的文本")
            hash_value = hashlib.sha256(text.encode()).hexdigest()
            comparable = {
                "title": data.get("title", "未命名资料"),
                "competitor": data.get("competitor", ""),
                "kind": data.get("kind", "document"),
                "published_at": data.get("published_at"),
                "url": data.get("url"),
            }
            if (
                old
                and old[-1]["sha256"] == hash_value
                and all(old[-1].get(field) == value for field, value in comparable.items())
            ):
                return old[-1]
            source = {
                **data,
                "id": new_id(),
                "project_id": project_id,
                "logical_key": logical,
                "title": data.get("title", "未命名资料"),
                "text": text,
                "competitor": data.get("competitor", ""),
                "kind": data.get("kind", "document"),
                "published_at": data.get("published_at"),
                "retrieved_at": now(),
                "url": data.get("url"),
                "synthetic": project["mode"] == "synthetic",
                "version": len(old) + 1,
                "sha256": hash_value,
                "active": True,
            }
            for previous in old:
                if previous["active"]:
                    self._replace(c, {**previous, "active": False})
            self._put(c, "source", source, project_id)
            old_ids = {s["id"] for s in old}
            for claim in self._list_in(c, "claim", project_id):
                if old_ids.intersection(claim.get("source_ids", [])):
                    self._replace(c, {**claim, "dirty": True})
            self._replace(c, {**project, "dirty": bool(project.get("latest_report_id")), "updated_at": now()})
            return source

    def get_source(self, rid):
        return self._read(rid, "source")

    def list_sources(self, project_id, active_only=True):
        return [s for s in self._list("source", project_id) if not active_only or s["active"]]

    def create_run(self, project_id, data, idempotency_key=None):
        with self._tx() as c:
            project = self._get(c, project_id, "project")
            if idempotency_key:
                key = "idem-" + digest([project_id, idempotency_key])
                try:
                    saved = self._get(c, key, "idempotency")
                    if saved["fingerprint"] != digest(data):
                        raise Conflict("同一请求标识对应不同内容")
                    return self._get(c, saved["target_id"], "run")
                except KeyError:
                    pass
            if any(r["status"] in ACTIVE for r in self._list_in(c, "run", project_id)):
                raise Conflict("这个项目已有运行中的研究")
            mode = data.get("mode", "replay")
            model_profile = data.get("model_profile", "qwen-default")
            if model_profile not in {"qwen-default", "gemini-budget"}:
                raise ValueError("未知模型配置")
            if mode == "replay" and project["mode"] != "synthetic":
                raise ValueError("固定响应回放仅适用于虚构测试工作区")
            ts = now()
            cap = data.get("budget_usd") or (
                0.30 if project["mode"] == "public" and project.get("web_enabled", True) else 0.15
            )
            if not 0 < cap <= 0.30:
                raise ValueError("单次预算须大于0且不超过US$0.30")
            obj = {
                "id": new_id(),
                "project_id": project_id,
                "brief": dict(project),
                "mode": mode,
                "model_profile": model_profile,
                "architecture": data.get("architecture", "multi"),
                "status": "queued",
                "budget_usd": cap,
                "bucket": data.get("bucket", "development"),
                "spent_usd": 0,
                "reserved_usd": 0,
                "request_count": 0,
                "source_ids": [s["id"] for s in self._list_in(c, "source", project_id) if s["active"]],
                "instructions": data.get("instructions", ""),
                "created_at": ts,
                "updated_at": ts,
                "report_id": None,
                "error": None,
                "phase": "queued",
            }
            self._put(c, "run", obj, project_id)
            c.execute(
                insert(jobs).values(
                    id=obj["id"], kind="run", status="queued", token="", expires=0, created=ts
                )
            )
            self._replace(c, {**project, "status": "researching", "updated_at": ts})
            if idempotency_key:
                self._put(c, "idempotency", {"id": key, "fingerprint": digest(data), "target_id": obj["id"]})
            return obj

    def get_run(self, rid):
        return self._read(rid, "run")

    def list_runs(self, project_id):
        return self._list("run", project_id)

    def update_run(self, rid, patch):
        with self._tx() as c:
            obj = self._get(c, rid, "run")
            if obj["status"] == "cancelled" and patch.get("status") not in {None, "cancelled"}:
                raise RunCancelled("研究已取消")
            result = self._replace(c, {**obj, **patch, "id": rid, "updated_at": now()})
            return result

    def cancel_run(self, rid):
        with self._tx() as c:
            obj = self._get(c, rid, "run")
            if obj["status"] in {"completed", "failed", "budget_exceeded", "cancelled"}:
                return obj
            obj = self._replace(c, {**obj, "status": "cancelled", "updated_at": now()})
            c.execute(update(jobs).where(jobs.c.id == rid).values(status="cancelled"))
            for task in self._list_in(c, "task", rid):
                if task["status"] in {"pending", "running"}:
                    self._replace(c, {**task, "status": "cancelled"})
            project = self._get(c, obj["project_id"], "project")
            self._replace(c, {**project, "status": "ready" if project.get("latest_report_id") else "draft"})
            return obj

    def resume_run(self, rid):
        with self._tx() as c:
            obj = self._get(c, rid, "run")
            if obj["status"] in ACTIVE or obj["status"] == "completed":
                return obj
            if any(
                r["id"] != rid and r["status"] in ACTIVE for r in self._list_in(c, "run", obj["project_id"])
            ):
                raise Conflict("项目已有其他运行中的研究")
            obj = self._replace(c, {**obj, "status": "queued", "error": None, "updated_at": now()})
            c.execute(update(jobs).where(jobs.c.id == rid).values(status="queued", token="", expires=0))
            for task in self._list_in(c, "task", rid):
                if task["status"] != "completed":
                    self._replace(c, {**task, "status": "pending", "error": None})
            return obj

    def assert_run_active(self, rid):
        if self.get_run(rid)["status"] == "cancelled":
            raise RunCancelled("研究已取消")
        if self.lease_id:
            with self.engine.connect() as c:
                self._check_lease(c)

    def create_task(self, run_id, data):
        with self._tx() as c:
            existing = self._list_in(c, "task", run_id)
            if len(existing) >= 24:
                raise ValueError("已达到24个研究任务上限")
            ts = now()
            obj = {
                "id": new_id(),
                "run_id": run_id,
                "role": "researcher",
                "target": "",
                "title": "研究任务",
                "status": "pending",
                "round": 0,
                "depends_on": [],
                "output": {},
                "error": None,
                "created_at": ts,
                "updated_at": ts,
                **data,
            }
            obj["run_id"] = run_id
            return self._put(c, "task", obj, run_id)

    def update_task(self, rid, patch):
        with self._tx() as c:
            obj = self._get(c, rid, "task")
            return self._replace(c, {**obj, **patch, "id": rid, "updated_at": now()})

    def list_tasks(self, run_id):
        return self._list("task", run_id)

    def add_event(self, run_id, type, message, payload=None):
        ts = now()
        body = {"type": type, "message": message, "payload": payload or {}}
        with self._tx() as c:
            pk = c.execute(
                insert(events).values(run_id=run_id, created=ts, payload=body)
            ).inserted_primary_key[0]
        return {"id": pk, "run_id": run_id, "created_at": ts, **body}

    def list_events(self, run_id, after=0):
        with self.engine.connect() as c:
            q = (
                select(events)
                .where(events.c.run_id == run_id, events.c.id > after)
                .order_by(events.c.id)
                .limit(1000)
            )
            return [
                {"id": r.id, "run_id": r.run_id, "created_at": r.created, **r.payload} for r in c.execute(q)
            ]

    def save_claims(self, project_id, run_id, claims):
        with self._tx() as c:
            current = self._list_in(c, "claim", project_id)
            result = []
            for claim in claims:
                old = next(
                    (
                        x
                        for x in current
                        if (x["subject"], x["dimension"]) == (claim["subject"], claim["dimension"])
                    ),
                    None,
                )
                obj = {
                    "status": "uncertain",
                    "evidence": [],
                    "value": None,
                    "conditions": [],
                    "computation": None,
                    **claim,
                    "id": old["id"] if old else new_id(),
                    "project_id": project_id,
                    "run_id": run_id,
                    "dirty": False,
                }
                obj["source_ids"] = list(dict.fromkeys(e["source_id"] for e in obj["evidence"]))
                for ev in obj["evidence"]:
                    source = self._get(c, ev["source_id"], "source")
                    if (
                        source["project_id"] != project_id
                        or not ev.get("quote")
                        or ev["quote"] not in source["text"]
                    ):
                        raise ValueError("证据引用不属于这个项目或无法在原文定位")
                if obj["status"] == "supported" and not obj["evidence"]:
                    obj["status"] = "uncertain"
                (self._replace(c, obj) if old else self._put(c, "claim", obj, project_id))
                current = [x for x in current if x["id"] != obj["id"]] + [obj]
                result.append(obj)
            return result

    def list_claims(self, project_id):
        return self._list("claim", project_id)

    def save_report(self, project_id, run_id, report_data):
        with self._tx() as c:
            run = self._get(c, run_id, "run")
            if run["status"] == "cancelled":
                raise RunCancelled("研究已取消")
            existing = self._list_in(c, "report", project_id)
            already = next((r for r in existing if r["run_id"] == run_id), None)
            if already:
                return already
            project = self._get(c, project_id, "project")
            obj = {
                "executive_summary": "",
                "sections": [],
                "comparison": [],
                "claims": [],
                "sources": [],
                "unresolved": [],
                "changes": [],
                **report_data,
                "id": new_id(),
                "project_id": project_id,
                "run_id": run_id,
                "version": len(existing) + 1,
                "title": report_data.get("title", project["title"]),
                "synthetic": project["mode"] == "synthetic",
                "mode": run["mode"],
                "model_profile": run.get("model_profile", "qwen-default"),
                "created_at": now(),
            }
            obj["content_hash"] = digest(obj)
            self._put(c, "report", obj, project_id)
            latest_ids = {s["id"] for s in self._list_in(c, "source", project_id) if s["active"]}
            used_ids = {s["id"] for s in obj["sources"]}
            self._replace(
                c,
                {
                    **project,
                    "latest_report_id": obj["id"],
                    "status": "ready",
                    "dirty": not latest_ids.issubset(used_ids),
                    "updated_at": now(),
                },
            )
            self._replace(c, {**run, "report_id": obj["id"], "updated_at": now()})
            return obj

    def get_report(self, rid):
        return self._read(rid, "report")

    def list_reports(self, project_id):
        return self._list("report", project_id)

    @staticmethod
    def _totals(c):
        amounts = {k: {"spent": 0, "reserved": 0, "limit": v} for k, v in BUCKETS.items()}
        for r in c.execute(select(reservations)).mappings():
            if r["actual"] is None:
                amounts[r["bucket"]]["reserved"] += r["reserved"]
            else:
                amounts[r["bucket"]]["spent"] += r["actual"]
        return amounts

    def reserve_cost(self, run_id, amount_usd, request_key):
        amount = micros(amount_usd)
        if amount <= 0:
            raise ValueError("费用预留必须大于0")
        with self._tx() as c:
            key = f"{run_id}:{request_key}"
            prev = c.execute(select(reservations).where(reservations.c.request_key == key)).mappings().first()
            if prev:
                return {"id": prev["id"], "amount_usd": prev["reserved"] / 1e6, "reused": True}
            run = self._get(c, run_id, "run")
            if run["status"] == "cancelled":
                raise RunCancelled("研究已取消")
            if run["mode"] != "live":
                raise ValueError("回放模式不能进行付费调用")
            bucket = run["bucket"]
            if bucket not in BUCKETS:
                raise ValueError("未知预算类别")
            totals = self._totals(c)
            total = sum(x["spent"] + x["reserved"] for x in totals.values())
            run_amount = sum(
                (r.actual if r.actual is not None else r.reserved)
                for r in c.execute(select(reservations).where(reservations.c.run_id == run_id))
            )
            if (
                total + amount > 10_000_000
                or totals[bucket]["spent"] + totals[bucket]["reserved"] + amount > BUCKETS[bucket]
            ):
                raise BudgetExceeded("项目或预算类别已达到上限；未确认费用仍保留预留")
            if run_amount + amount > micros(run["budget_usd"]):
                raise BudgetExceeded("已达到本次研究预算上限")
            if run["request_count"] >= 40:
                raise BudgetExceeded("已达到40次模型请求上限")
            rid = new_id()
            c.execute(
                insert(reservations).values(
                    id=rid,
                    request_key=key,
                    run_id=run_id,
                    bucket=bucket,
                    reserved=amount,
                    actual=None,
                    payload={"created_at": now()},
                )
            )
            self._replace(
                c,
                {
                    **run,
                    "request_count": run["request_count"] + 1,
                    "reserved_usd": round(run["reserved_usd"] + amount / 1e6, 6),
                },
            )
            return {"id": rid, "amount_usd": amount / 1e6, "reused": False}

    def settle_cost(self, reservation_id, actual_usd, metadata=None):
        # Settlement remains allowed after cancellation or a lost lease: the provider
        # may have billed an in-flight request. It never starts new work.
        with self._tx(check_lease=False) as c:
            r = c.execute(select(reservations).where(reservations.c.id == reservation_id)).mappings().one()
            if r["actual"] is not None:
                return
            meta = {**r["payload"], **(metadata or {})}
            if actual_usd is None:
                c.execute(
                    update(reservations).where(reservations.c.id == reservation_id).values(payload=meta)
                )
                return
            amount = micros(actual_usd)
            c.execute(
                update(reservations)
                .where(reservations.c.id == reservation_id)
                .values(actual=amount, payload=meta)
            )
            run = self._get(c, r["run_id"], "run")
            self._replace(
                c,
                {
                    **run,
                    "reserved_usd": max(0, round(run["reserved_usd"] - r["reserved"] / 1e6, 6)),
                    "spent_usd": round(run["spent_usd"] + amount / 1e6, 6),
                },
            )

    def budget_summary(self):
        with self.engine.connect() as c:
            totals = self._totals(c)
        return {
            "limit_usd": 10.0,
            "spent_usd": sum(v["spent"] for v in totals.values()) / 1e6,
            "reserved_usd": sum(v["reserved"] for v in totals.values()) / 1e6,
            "buckets": {k: {f"{n}_usd": v / 1e6 for n, v in item.items()} for k, item in totals.items()},
        }

    def create_export(self, report_id, format):
        with self._tx() as c:
            report = self._get(c, report_id, "report")
            rid = "export-" + digest([report["content_hash"], format])[:32]
            try:
                existing = self._get(c, rid, "export")
                if existing["status"] == "failed":
                    existing = self._replace(c, {**existing, "status": "queued", "error": None})
                    c.execute(
                        update(jobs).where(jobs.c.id == rid).values(status="queued", token="", expires=0)
                    )
                return existing
            except KeyError:
                pass
            obj = {
                "id": rid,
                "report_id": report_id,
                "format": format,
                "status": "queued",
                "error": None,
                "path": None,
                "download_url": None,
                "created_at": now(),
            }
            self._put(c, "export", obj, report_id)
            c.execute(
                insert(jobs).values(
                    id=rid, kind="export", status="queued", token="", expires=0, created=now()
                )
            )
            return obj

    def get_export(self, rid):
        return self._read(rid, "export")

    def update_export(self, rid, patch):
        with self._tx() as c:
            return self._replace(c, {**self._get(c, rid, "export"), **patch})

    def claim_job(self, lease_seconds=90):
        with self._tx() as c:
            row = (
                c.execute(
                    select(jobs)
                    .where(
                        (jobs.c.status == "queued")
                        | ((jobs.c.status == "running") & (jobs.c.expires < time.time()))
                    )
                    .order_by(jobs.c.created)
                )
                .mappings()
                .first()
            )
            if not row:
                return None
            token = new_id()
            c.execute(
                update(jobs)
                .where(jobs.c.id == row["id"])
                .values(status="running", token=token, expires=time.time() + lease_seconds)
            )
            return {**row, "token": token, "status": "running"}

    def heartbeat(self, job_id, token, lease_seconds=90):
        with self.engine.begin() as c:
            n = c.execute(
                update(jobs)
                .where(jobs.c.id == job_id, jobs.c.token == token, jobs.c.status == "running")
                .values(expires=time.time() + lease_seconds)
            ).rowcount
            return n == 1

    def finish_job(self, job_id, token, status="completed"):
        with self.engine.begin() as c:
            c.execute(
                update(jobs)
                .where(jobs.c.id == job_id, jobs.c.token == token, jobs.c.status == "running")
                .values(status=status, expires=0)
            )
