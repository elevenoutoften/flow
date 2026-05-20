from __future__ import annotations

import argparse

from .database import build_engine, build_session_factory, default_database_url
from .dispatcher import dispatch_loop, stale_recovery
from .main import ensure_compatible_schema


def main() -> int:
    parser = argparse.ArgumentParser(description="Flow agent dispatcher")
    parser.add_argument("--agent", help="Agent name to dispatch.")
    parser.add_argument("--continuous", action="store_true", help="Run continuously.")
    parser.add_argument("--interval", type=float, default=5.0, help="Polling interval in seconds.")
    parser.add_argument("--stale-recovery", action="store_true", help="Recover stale running agent runs.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Flow API base URL.")
    parser.add_argument("--api-key", default="", help="Flow API key passed to subprocesses.")
    args = parser.parse_args()

    engine = build_engine(default_database_url())
    ensure_compatible_schema(engine)
    SessionLocal = build_session_factory(engine)
    with SessionLocal() as session:
        if args.stale_recovery:
            recovered = stale_recovery(session)
            session.commit()
            print(f"Recovered {len(recovered)} stale run(s).")
            return 0
        if not args.agent:
            parser.error("--agent is required unless --stale-recovery is set")
        runs = dispatch_loop(
            session,
            args.agent,
            api_key=args.api_key,
            base_url=args.base_url,
            continuous=args.continuous,
            interval=args.interval,
            session_factory=SessionLocal,
        )
        print(f"Dispatched {len(runs)} run(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
