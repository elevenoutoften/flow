from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import replace
import json
import logging
import os
from pathlib import Path

from fastapi import FastAPI, Header, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from .config import FlowSettings, get_settings
from .database import Base, build_engine, build_session_factory, default_database_url
from .dispatcher import set_session_factory
from .mcp import JsonRpcError, error_response, exception_response, handle_mcp_message
from .migration import ensure_compatible_schema
from .metrics import metrics
from .repository import ensure_project
from .routes.audit import router as audit_router
from .routes.agents import router as agents_router
from .routes.automation import router as automation_router
from .routes.dependencies import _commit
from .routes.ideas import router as ideas_router
from .routes.notifications import router as notifications_router
from .routes.packs import router as packs_router
from .routes.projects import router as projects_router
from .routes.realtime import router as realtime_router
from .routes.recurring_task_templates import router as recurring_templates_router
from .routes.runners import router as runners_router
from .routes.tasks import router as tasks_router
from .routes.ui import router as ui_router
from .routes.webhooks import router as webhooks_router
from .routes.workspace import router as workspace_router
from .security import SESSION_COOKIE_NAME, resolve_actor
from .secrets_resolver import SecretResolutionError, resolve_secret, secret_reference_type

PACKAGE_DIR = Path(__file__).resolve().parent
_LOGGER = logging.getLogger(__name__)

_REPO_DOCS = "https://github.com/elevenoutoften/flow/blob/main/docs"


def _render_llms_txt(base_url: str) -> str:
    """Render the deployment-aware agent onboarding doc served at GET /llms.txt.

    Follows the llmstxt.org convention. `base_url` is this server's external URL with no
    trailing slash; it is interpolated into a single copy-pasteable connect flow so an agent
    handed only this link can configure itself. Contains no secrets.
    """
    template = """# Flow — Agent Onboarding

> Flow is an agent-first Kanban board, live at {BASE}.
> Hand this URL to any LLM agent (Claude, Hermes, GPT) to connect and start working tasks.

## This server speaks

- Web board  — {BASE}/
- REST API   — {BASE}/api
- MCP (JSON-RPC 2.0) — {BASE}/mcp

## 1. Get an API key (role-scoped)

- Web UI: open {BASE}/ then "API keys" and create one (start with `admin` for setup).
- First run on the host: `flow-bootstrap` prints admin / implementer / reviewer keys once.

## 2. Connect your agent

### Claude Code

    claude mcp add --transport http flow {BASE}/mcp --header "Authorization: Bearer <YOUR_KEY>"

Then call a tool such as `flow_board_summary` to confirm the connection.

### Hermes / any HTTP client

Set these, then send `Authorization: Bearer <YOUR_KEY>` on every request:

    FLOW_BASE_URL={BASE}
    FLOW_API_KEY=<YOUR_KEY>
    FLOW_AGENT_NAME=<your-agent-name>

## 3. Core work loop

1. Next task:  GET  {BASE}/api/tasks/next?project=default
2. Claim it:   POST {BASE}/api/tasks/<id>/claim   body: {"agent_name":"<you>"}
3. Work, posting progress:
               POST {BASE}/api/tasks/<id>/note    body: {"note":"...","author":"<you>"}
4. Hand off:   POST {BASE}/api/tasks/<id>/move    body: {"status":"review"}
   (reviewers/admins move tasks to `done`)

## Full documentation

- Agent quickstart: {DOCS}/AGENT-QUICKSTART.md
- REST API:         {DOCS}/Modules/REST-API.md
- MCP tools:        {DOCS}/Modules/MCP.md
- Roles & keys:     {DOCS}/Modules/Security.md
"""
    return template.replace("{BASE}", base_url).replace("{DOCS}", _REPO_DOCS)


def _secret_health(value: str | None, *, ref: str | None = None) -> dict[str, object]:
    secret_type = secret_reference_type(value)
    configured = bool(value)
    if value and secret_type in {"env", "file"}:
        try:
            configured = bool(resolve_secret(value))
        except SecretResolutionError:
            configured = False
    payload: dict[str, object] = {"configured": configured, "type": secret_type}
    if ref is not None:
        payload["ref"] = ref
    return payload


def create_app(
    database_url: str | None = None,
    settings: FlowSettings | None = None,
    trusted_headers: bool | None = None,
    session_secret: str | None = None,
    session_cookie_secure: bool | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    if trusted_headers is not None:
        settings = replace(settings, trusted_headers=trusted_headers)
    if session_secret is not None:
        settings = replace(settings, session_secret=session_secret)
    if session_cookie_secure is not None:
        settings = replace(settings, session_cookie_secure=session_cookie_secure)
    engine = build_engine(database_url or default_database_url())
    session_factory = build_session_factory(engine)
    set_session_factory(session_factory)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        Base.metadata.create_all(bind=engine)
        ensure_compatible_schema(engine)
        db = session_factory()
        try:
            ensure_project(db, settings.default_project)
            _commit(db)
        finally:
            db.close()
        yield

    app = FastAPI(title="Flow", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.SessionLocal = session_factory
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(ui_router)
    app.mount("/static", StaticFiles(directory=str(PACKAGE_DIR / "static")), name="static")

    app.include_router(projects_router, prefix="/api")
    app.include_router(packs_router, prefix="/api")
    app.include_router(audit_router, prefix="/api")
    app.include_router(agents_router, prefix="/api")
    app.include_router(ideas_router, prefix="/api")
    app.include_router(webhooks_router, prefix="/api")
    app.include_router(automation_router, prefix="/api")
    app.include_router(notifications_router, prefix="/api")
    app.include_router(recurring_templates_router, prefix="/api")
    app.include_router(runners_router, prefix="/api")
    app.include_router(tasks_router, prefix="/api")
    app.include_router(workspace_router, prefix="/api")
    app.include_router(realtime_router, prefix="/api")

    @app.middleware("http")
    async def record_api_errors(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/") and response.status_code >= 500:
            metrics.inc("api.errors")
        return response

    @app.get("/healthz")
    def healthz(request: Request):
        try:
            with request.app.state.SessionLocal() as db:
                db.execute(text("SELECT 1"))
        except Exception:
            return JSONResponse({"ok": False, "database": False}, status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
        return {"ok": True, "database": True}

    @app.get("/healthz/config")
    def healthz_config(request: Request):
        settings = request.app.state.settings
        return {
            "trusted_headers": settings.trusted_headers,
            "session_auth_enabled": bool(settings.session_secret),
            "session_cookie_secure": settings.session_cookie_secure,
        }

    @app.get("/healthz/secrets")
    def healthz_secrets():
        return {
            "telegram_bot_token": _secret_health(os.environ.get("FLOW_TELEGRAM_BOT_TOKEN", "")),
            "discord_webhook_url": _secret_health(os.environ.get("FLOW_DISCORD_WEBHOOK_URL", "")),
            "webhook_encryption_key": _secret_health(os.environ.get("FLOW_WEBHOOK_ENCRYPTION_KEY", "")),
            "reviewer_api_key_ref": _secret_health("env:FLOW_REVIEWER_API_KEY", ref="env:FLOW_REVIEWER_API_KEY"),
        }

    @app.get("/llms.txt", response_class=PlainTextResponse)
    def llms_txt(request: Request):
        configured = request.app.state.settings.public_url
        base_url = configured or str(request.base_url).rstrip("/")
        return _render_llms_txt(base_url)

    @app.post("/mcp")
    async def mcp(
        request: Request,
        authorization: str | None = Header(default=None),
        x_axis_admin: str | None = Header(default=None),
        x_axis_user: str | None = Header(default=None),
        x_axis_agent: str | None = Header(default=None),
    ):
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return JSONResponse(exception_response(None, "Request body must be valid JSON."), status_code=400)

        request_id = payload.get("id") if isinstance(payload, dict) else None
        db = session_factory()
        try:
            actor = resolve_actor(
                db,
                authorization,
                x_axis_admin,
                x_axis_user,
                x_axis_agent,
                trusted_headers=request.app.state.settings.trusted_headers,
                session_cookie=request.cookies.get(SESSION_COOKIE_NAME),
                session_secret=request.app.state.settings.session_secret or None,
            )
            response_payload = handle_mcp_message(db, payload, actor)
        except JsonRpcError as exc:
            response_payload = error_response(request_id, exc)
        except Exception as exc:
            _LOGGER.exception("Unexpected error while handling MCP request")
            response_payload = exception_response(request_id, "Internal server error.")
        finally:
            db.close()

        if response_payload is None:
            return Response(status_code=202)
        return JSONResponse(jsonable_encoder(response_payload))

    return app


app = create_app()
