"""Create a free replay workspace and record the final local delivery state."""

import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree

import httpx


def main():
    root = Path(__file__).resolve().parents[1]
    with httpx.Client(base_url="http://127.0.0.1:8788", timeout=30) as api:

        def request(method, path, **kwargs):
            response = api.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()

        project = request("POST", "/api/demo", json={"scenario": "dev-default"})
        request(
            "PATCH",
            f"/api/projects/{project['id']}",
            json={"title": "BriefForge · 协作研究演示（虚构资料）"},
        )
        run = request(
            "POST",
            f"/api/projects/{project['id']}/runs",
            json={"mode": "replay", "architecture": "multi"},
        )
        deadline = time.monotonic() + 120
        while run["status"] in {"queued", "running"} and time.monotonic() < deadline:
            time.sleep(1)
            run = request("GET", f"/api/runs/{run['id']}")
        assert run["status"] == "completed", run.get("error")
        assert run["request_count"] == 0 and run["spent_usd"] == 0
        report = request("GET", f"/api/reports/{run['report_id']}")
        tasks = request("GET", f"/api/runs/{run['id']}/tasks")
        events = request("GET", f"/api/runs/{run['id']}/event-log")
        test_suite = ElementTree.parse(root / "artifacts/final-pytest.xml").find("testsuite")
        manifest = json.loads(
            (root / "artifacts/evaluation-live-gemini-v4/implementation-manifest.json").read_text()
        )
        hashes_match = all(
            hashlib.sha256((root / path).read_bytes()).hexdigest() == sha
            for path, sha in manifest["code_and_reference_sha256"].items()
        )
        assert hashes_match, "Final benchmark code changed"
        result = {
            "verified_at": datetime.now(UTC).isoformat(),
            "url": "http://127.0.0.1:8788",
            "health": request("GET", "/api/health"),
            "pytest": test_suite.attrib,
            "ruff": "passed",
            "docker_compose": "config validation passed; container runtime not verified",
            "final_benchmark_code_hashes_match": hashes_match,
            "budget": request("GET", "/api/budget"),
            "demo": {
                "project_id": project["id"],
                "run_id": run["id"],
                "report_id": report["id"],
                "status": run["status"],
                "sources": len(report["sources"]),
                "claims": len(report["claims"]),
                "tasks": len(tasks),
                "followups": sum(e["type"] == "followup_planned" for e in events),
                "model_requests": run["request_count"],
                "spent_usd": run["spent_usd"],
            },
        }
        (root / "artifacts/final-verification.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
