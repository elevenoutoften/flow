"""`flow-serve` — start the Flow web server with sensible defaults.

A thin, friendly wrapper around uvicorn so the run command is just `flow-serve`
instead of a uvicorn invocation with host/port flags. It also makes browser login
work out of the box by ensuring a stable session secret exists (see
``ensure_session_secret``).
"""
from __future__ import annotations

import argparse
import os
import secrets
from pathlib import Path

SESSION_SECRET_FILENAME = "session_secret"


def ensure_session_secret(data_dir: Path) -> str:
    """Return a stable session secret, generating and persisting one if absent.

    This lets the browser login flow work with zero configuration. The secret is
    written to ``<data_dir>/session_secret`` (the data dir is git-ignored) so sessions
    survive restarts. An explicit ``FLOW_SESSION_SECRET`` always takes precedence and is
    never overwritten.
    """
    existing = os.environ.get("FLOW_SESSION_SECRET", "").strip()
    if existing:
        return existing

    data_dir.mkdir(parents=True, exist_ok=True)
    secret_path = data_dir / SESSION_SECRET_FILENAME
    if secret_path.exists():
        stored = secret_path.read_text(encoding="utf-8").strip()
        if stored:
            return stored

    secret = secrets.token_urlsafe(32)
    secret_path.write_text(secret, encoding="utf-8")
    try:
        secret_path.chmod(0o600)
    except OSError:
        pass  # best-effort; not all platforms honor POSIX modes
    return secret


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="flow-serve", description="Run the Flow web server.")
    parser.add_argument("--host", default=None, help="Bind host (default: FLOW_HOST or 0.0.0.0).")
    parser.add_argument("--port", type=int, default=None, help="Bind port (default: FLOW_PORT or 8100).")
    parser.add_argument(
        "--bootstrap",
        action="store_true",
        help="Seed the default project, first API keys, agents, and rules before serving.",
    )
    parser.add_argument("--reload", action="store_true", help="Auto-reload on code changes (development).")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    import uvicorn

    from .config import get_settings, reset_settings_cache

    args = _parse_args(argv)

    # Persist a session secret and expose it *before* the app builds its settings,
    # so the browser login flow works without any configuration.
    settings = get_settings()
    os.environ["FLOW_SESSION_SECRET"] = ensure_session_secret(settings.data_dir)
    reset_settings_cache()
    settings = get_settings()

    if args.bootstrap:
        from .bootstrap import main as bootstrap_main

        bootstrap_result = bootstrap_main(
            ["--database-url", settings.database_url, "--project", settings.default_project]
        )
        if bootstrap_result != 0:
            return bootstrap_result

    host = args.host or settings.host
    port = args.port or settings.port

    print(f"Flow listening on http://{host}:{port}")
    print("  Board UI        /            (sign in at /login with an API key)")
    print("  REST API        /api")
    print("  MCP (agents)    /mcp")
    print("  Agent onboarding /llms.txt")
    uvicorn.run("flow_app.main:app", host=host, port=port, reload=args.reload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
