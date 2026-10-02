from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, config
from .collaboration import collaboration_summary
from .ingest import MAX_FILE, fetch_public_url, parse_file
from .schemas import (
    DemoCreate,
    ExportCreate,
    ProjectCreate,
    ProjectPatch,
    Revision,
    RunCreate,
    SourceCreate,
    UrlImport,
)
from .store import ACTIVE, BudgetExceeded, Conflict, Store


def create_app(store: Store | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        app.state.store = store or Store()
        yield

    app = FastAPI(title="BriefForge", version=__version__, lifespan=lifespan)
    if store:
        app.state.store = store

    def db():
        return app.state.store

    @app.middleware("http")
    async def headers(request: Request, call_next):
        origin = request.headers.get("origin")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and origin:
            allowed_host = request.headers.get("host", "")
            # Development frontend proxy is same-origin in the browser, but Vite
            # forwards its Origin header to the local backend.
            if urlsplit(origin).netloc not in {allowed_host, "localhost:5173", "127.0.0.1:5173"}:
                return JSONResponse(status_code=403, content={"detail": "不允许来自其他网站的写入请求"})
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse(status_code=404, content={"detail": "记录不存在"})

    @app.exception_handler(Conflict)
    async def conflict(request, exc):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(BudgetExceeded)
    async def budget(request, exc):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.get("/api/health")
    def health():
        return {
            "status": "ok",
            "version": __version__,
            "database": "postgresql" if db().database_url.startswith("postgres") else "sqlite",
            "model_configured": bool(os.getenv("OPENROUTER_API_KEY")),
            "single_user": True,
        }

    @app.get("/api/budget")
    def get_budget():
        return db().budget_summary()

    @app.get("/api/projects")
    def projects():
        return db().list_projects()

    @app.post("/api/projects", status_code=201)
    def create_project(data: ProjectCreate):
        return db().create_project(data.model_dump())

    @app.post("/api/demo", status_code=201)
    def demo(data: DemoCreate | None = None):
        from .demo import seed_demo

        return seed_demo(db(), scenario=data.scenario if data else "default")

    @app.get("/api/projects/{project_id}")
    def project(project_id: str):
        return db().get_project(project_id)

    @app.patch("/api/projects/{project_id}")
    def edit_project(project_id: str, data: ProjectPatch):
        old = db().get_project(project_id)
        if any(r["status"] in ACTIVE for r in db().list_runs(project_id)):
            raise Conflict("请等待当前研究结束后调整研究目标")
        patch = data.model_dump(exclude_none=True)
        normalized = ProjectCreate.model_validate(
            {**{k: old[k] for k in ProjectCreate.model_fields if k in old}, **patch}
        )
        if normalized.mode == "synthetic":
            normalized.web_enabled = False
        return db().edit_brief(
            project_id, {**normalized.model_dump(), "dirty": bool(old.get("latest_report_id"))}
        )

    @app.get("/api/projects/{project_id}/sources")
    def sources(project_id: str, history: bool = False):
        db().get_project(project_id)
        return db().list_sources(project_id, active_only=not history)

    @app.post("/api/projects/{project_id}/sources", status_code=201)
    def source(project_id: str, data: SourceCreate):
        return db().add_source(project_id, data.model_dump(exclude_none=True))

    @app.get("/api/sources/{source_id}")
    def get_source(source_id: str):
        return db().get_source(source_id)

    @app.post("/api/projects/{project_id}/upload", status_code=201)
    async def upload(
        project_id: str,
        file: Annotated[UploadFile, File()],
        competitor: str = Form(""),
        logical_key: str = Form(""),
    ):
        db().get_project(project_id)
        data = await file.read(MAX_FILE + 1)
        await file.close()
        parsed = await asyncio.to_thread(parse_file, file.filename or "upload", data)
        parsed["competitor"] = competitor[:120]
        if logical_key:
            parsed["logical_key"] = logical_key[:200]
        return db().add_source(project_id, parsed)

    @app.post("/api/projects/{project_id}/urls", status_code=201)
    async def import_url(project_id: str, data: UrlImport):
        p = db().get_project(project_id)
        if p["mode"] != "public":
            raise ValueError("请在真实资料工作区导入公开网页")
        if len([s for s in db().list_sources(project_id) if s["kind"] == "web"]) >= 20:
            raise ValueError("每个工作区最多20个网页来源")
        result = await fetch_public_url(data.url)
        return db().add_source(
            project_id, {**result, "competitor": data.competitor, "logical_key": result["url"]}
        )

    @app.post("/api/projects/{project_id}/outline")
    def outline(project_id: str):
        p = db().get_project(project_id)
        return {
            "dimensions": p["dimensions"],
            "questions": [
                f"{name}：{dimension}" for name in p["competitors"] for dimension in p["dimensions"]
            ],
            "estimated_cap_usd": 0.30 if p["mode"] == "public" and p.get("web_enabled", True) else 0.15,
            "note": "预算上限；费用按实际模型用量结算。",
        }

    @app.post("/api/projects/{project_id}/runs", status_code=202)
    def start(project_id: str, data: RunCreate, idempotency_key: str | None = Header(default=None)):
        if data.mode == "live" and not os.getenv("OPENROUTER_API_KEY"):
            raise ValueError("服务端尚未配置OPENROUTER_API_KEY；可先运行虚构案例回放")
        project = db().get_project(project_id)
        if not db().list_sources(project_id) and (
            project["mode"] == "synthetic" or not project.get("web_enabled", True)
        ):
            raise ValueError("请先导入研究资料；公开研究也可开启联网搜索")
        run = db().create_run(project_id, data.model_dump(), idempotency_key=idempotency_key)
        db().add_event(run["id"], "queued", "研究已进入队列", {"mode": run["mode"]})
        return run

    @app.get("/api/projects/{project_id}/runs")
    def runs(project_id: str):
        return db().list_runs(project_id)

    @app.get("/api/runs/{run_id}")
    def run(run_id: str):
        return db().get_run(run_id)

    @app.post("/api/runs/{run_id}/cancel")
    def cancel(run_id: str):
        result = db().cancel_run(run_id)
        db().add_event(run_id, "cancelled", "已取消后续工作；在途模型请求可能仍产生费用")
        return result

    @app.post("/api/runs/{run_id}/resume")
    def resume(run_id: str):
        return db().resume_run(run_id)

    @app.get("/api/runs/{run_id}/tasks")
    def tasks(run_id: str):
        return db().list_tasks(run_id)

    @app.get("/api/runs/{run_id}/collaboration")
    def collaboration(run_id: str):
        return collaboration_summary(db(), run_id)

    @app.get("/api/runs/{run_id}/event-log")
    def event_log(run_id: str, after: int = 0):
        return db().list_events(run_id, max(0, after))

    @app.get("/api/runs/{run_id}/events")
    async def stream(
        run_id: str, request: Request, after: int = 0, last_event_id: str | None = Header(default=None)
    ):
        db().get_run(run_id)
        cursor = max(after, int(last_event_id) if last_event_id and last_event_id.isdigit() else 0)

        async def output():
            nonlocal cursor
            for _ in range(1800):
                if await request.is_disconnected():
                    return
                rows = db().list_events(run_id, cursor)
                for event in rows:
                    cursor = event["id"]
                    yield f"id: {cursor}\nevent: message\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                if db().get_run(run_id)["status"] not in ACTIVE:
                    yield f"event: done\ndata: {json.dumps(db().get_run(run_id), ensure_ascii=False)}\n\n"
                    return
                if not rows:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(1)

        return StreamingResponse(
            output(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/projects/{project_id}/claims")
    def claims(project_id: str):
        return db().list_claims(project_id)

    @app.get("/api/projects/{project_id}/reports")
    def reports(project_id: str):
        return db().list_reports(project_id)

    @app.get("/api/reports/{report_id}")
    def report(report_id: str):
        return db().get_report(report_id)

    @app.post("/api/reports/{report_id}/revise", status_code=202)
    def revise(report_id: str, data: Revision, idempotency_key: str | None = Header(default=None)):
        report = db().get_report(report_id)
        old = db().get_run(report["run_id"])
        return db().create_run(
            report["project_id"],
            {
                "mode": old["mode"],
                "model_profile": old.get("model_profile", "qwen-default"),
                "architecture": old["architecture"],
                "instructions": data.instructions,
                "budget_usd": old["budget_usd"],
                "bucket": old["bucket"],
            },
            idempotency_key,
        )

    @app.post("/api/reports/{report_id}/exports", status_code=202)
    def export(report_id: str, data: ExportCreate):
        return public_export(db().create_export(report_id, data.format))

    def public_export(obj):
        return {k: v for k, v in obj.items() if k != "path"}

    @app.get("/api/exports/{export_id}")
    def export_job(export_id: str):
        return public_export(db().get_export(export_id))

    @app.get("/api/exports/{export_id}/download")
    def download(export_id: str):
        obj = db().get_export(export_id)
        if obj["status"] != "completed" or not obj.get("path"):
            raise Conflict("导出文件尚未就绪")
        path = Path(obj["path"]).resolve()
        if not path.is_relative_to(db().data_dir) or not path.is_file():
            raise HTTPException(404, "导出文件不存在")
        return FileResponse(
            path, filename=f"BriefForge-v{db().get_report(obj['report_id'])['version']}.{obj['format']}"
        )

    dist = config.ROOT / "frontend" / "dist"
    if dist.is_dir():
        if (dist / "assets").is_dir():
            app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                raise HTTPException(404, "接口不存在")
            candidate = (dist / path).resolve()
            if candidate.is_relative_to(dist.resolve()) and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")

    return app
