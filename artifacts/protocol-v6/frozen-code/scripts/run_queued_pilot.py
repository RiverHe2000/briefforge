"""Record a real-model pilot through the application's ordinary worker queue."""

import argparse
import json
import time
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--public", action="store_true")
    parser.add_argument("--architecture", choices=["single", "pipeline", "multi"], default="multi")
    parser.add_argument("--model-profile", choices=["qwen-default", "gemini-budget"], default="gemini-budget")
    args = parser.parse_args()
    with httpx.Client(base_url="http://127.0.0.1:8788", timeout=30) as api:

        def request(method, path, **kwargs):
            response = api.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()

        if args.public:
            project = request(
                "POST",
                "/api/projects",
                json={
                    "title": "真实联网：办公协作产品研究",
                    "question": "比较 Trello、Asana、ClickUp 面向中小团队的定位、价格条件和 SSO 支持，不能确认的内容保留未知。",
                    "audience": "产品经理",
                    "competitors": ["Trello", "Asana", "ClickUp"],
                    "dimensions": ["定位", "价格", "SSO"],
                    "mode": "public",
                },
            )
        else:
            project = request("POST", "/api/demo", json={"scenario": "dev-default"})
        run = request(
            "POST",
            f"/api/projects/{project['id']}/runs",
            json={
                "mode": "live",
                "model_profile": args.model_profile,
                "architecture": args.architecture,
                "budget_usd": 0.30 if args.public else 0.15,
                "bucket": "web" if args.public else "development",
            },
        )
        print(
            json.dumps(
                {"run_id": run["id"], "project_id": project["id"], "model_profile": args.model_profile}
            ),
            flush=True,
        )
        started = time.monotonic()
        while run["status"] in {"queued", "running"}:
            if time.monotonic() - started > 1200:
                request("POST", f"/api/runs/{run['id']}/cancel")
            time.sleep(2)
            run = request("GET", f"/api/runs/{run['id']}")
        folder = Path("artifacts/live")
        folder.mkdir(parents=True, exist_ok=True)
        summary = {**run, "public": args.public, "elapsed_seconds": round(time.monotonic() - started, 2)}
        for suffix, data in [("", summary), ("-events", request("GET", f"/api/runs/{run['id']}/event-log"))]:
            (folder / f"{run['id']}{suffix}.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        if run.get("report_id"):
            report = request("GET", f"/api/reports/{run['report_id']}")
            (folder / f"{run['id']}-report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        print(
            json.dumps(
                {
                    k: summary.get(k)
                    for k in [
                        "id",
                        "status",
                        "report_id",
                        "model_profile",
                        "error",
                        "spent_usd",
                        "reserved_usd",
                        "request_count",
                        "elapsed_seconds",
                    ]
                },
                ensure_ascii=False,
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
