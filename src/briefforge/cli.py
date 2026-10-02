import argparse
import asyncio
import logging
import os
import sys


def main():
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    parser = argparse.ArgumentParser(prog="briefforge")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default=os.getenv("BRIEFFORGE_HOST", "127.0.0.1"))
    serve.add_argument("--port", type=int, default=int(os.getenv("BRIEFFORGE_PORT", "8788")))
    worker = sub.add_parser("worker")
    worker.add_argument("--once", action="store_true")
    sub.add_parser("demo")
    sub.add_parser("budget")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.command == "serve":
        import uvicorn

        uvicorn.run(
            "briefforge.api:create_app", factory=True, host=args.host, port=args.port, log_level="info"
        )
    elif args.command == "worker":
        from .worker import worker_loop

        asyncio.run(worker_loop(once=args.once))
    else:
        import json

        from .store import Store

        store = Store()
        if args.command == "demo":
            from .demo import seed_demo

            print(json.dumps(seed_demo(store), ensure_ascii=False, indent=2))
        else:
            print(json.dumps(store.budget_summary(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
