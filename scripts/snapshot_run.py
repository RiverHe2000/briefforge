"""Save a run's current audit evidence without creating model requests."""

import argparse
import json
import re
import time
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id")
    parser.add_argument("--wait", action="store_true")
    parser.add_argument("--label", default="snapshot")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-zA-Z0-9-]+", args.run_id + args.label):
        parser.error("Identifiers and labels must contain letters, numbers or hyphens.")
    folder = Path("artifacts/live")
    folder.mkdir(parents=True, exist_ok=True)
    with httpx.Client(base_url="http://127.0.0.1:8788", timeout=30) as client:

        def get(path):
            response = client.get(path)
            response.raise_for_status()
            return response.json()

        started = time.monotonic()
        run = get(f"/api/runs/{args.run_id}")
        while args.wait and run["status"] in {"queued", "running"}:
            if time.monotonic() - started > 1200:
                raise TimeoutError("Run is still active; no state was changed.")
            time.sleep(2)
            run = get(f"/api/runs/{args.run_id}")
        evidence = {"": run, "-events": get(f"/api/runs/{args.run_id}/event-log")}
        if run.get("report_id"):
            evidence["-report"] = get(f"/api/reports/{run['report_id']}")
        for suffix, payload in evidence.items():
            (folder / f"{args.run_id}-{args.label}{suffix}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        print(
            json.dumps(
                {
                    k: run.get(k)
                    for k in [
                        "id",
                        "status",
                        "report_id",
                        "error",
                        "request_count",
                        "spent_usd",
                        "reserved_usd",
                    ]
                },
                ensure_ascii=False,
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
