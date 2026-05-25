from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import replace
import json
from pathlib import Path

from fastapi import Depends, FastAPI, Header, Query, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.orm import Session

from .config import FLOW_VERSION, FlowSettings, get_settings
from .database import Base, build_engine, build_session_factory, default_database_url
from .dispatcher import set_session_factory
from .mcp import JsonRpcError, error_response, exception_response, handle_mcp_message
from .migration import ensure_compatible_schema
from .repository import batch_dependency_summaries, ensure_project, list_agent_api_keys, list_projects, list_tasks
from .routes.agents import router as agents_router
from .routes.automation import router as automation_router
from .routes.dependencies import _commit, get_db
from .routes.ideas import router as ideas_router
from .routes.projects import router as projects_router
from .routes.tasks import router as tasks_router
from .routes.webhooks import router as webhooks_router
from .routes.workspace import router as workspace_router
from .schemas import STATUSES
from .security import (
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE,
    Permission,
    has_permission,
    resolve_actor,
    sign_session,
)

PACKAGE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))


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
    app.mount("/static", StaticFiles(directory=str(PACKAGE_DIR / "static")), name="static")

    app.include_router(projects_router, prefix="/api")
    app.include_router(agents_router, prefix="/api")
    app.include_router(ideas_router, prefix="/api")
    app.include_router(webhooks_router, prefix="/api")
    app.include_router(automation_router, prefix="/api")
    app.include_router(tasks_router, prefix="/api")
    app.include_router(workspace_router, prefix="/api")

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
            response_payload = exception_response(request_id, str(exc))
        finally:
            db.close()

        if response_payload is None:
            return Response(status_code=202)
        return JSONResponse(jsonable_encoder(response_payload))

    @app.get("/", response_class=HTMLResponse)
    def board(
        request: Request,
        db: Session = Depends(get_db),
        project: str | None = Query(default=None),
        authorization: str | None = Header(default=None),
        x_axis_admin: str | None = Header(default=None),
        x_axis_user: str | None = Header(default=None),
        x_axis_agent: str | None = Header(default=None),
    ):
        projects = list_projects(db)
        selected_project = project.strip() if project else ""
        tasks = list_tasks(db, project=selected_project or None)
        tasks_by_status = {status_name: [] for status_name in STATUSES}
        for task in tasks:
            tasks_by_status.setdefault(task.status, []).append(task)
        dependencies_by_task = batch_dependency_summaries(db, [task.id for task in tasks])
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
        can_manage_api_keys = actor is not None and actor.role.value == "admin"
        can_set_human_required = actor is not None and has_permission(actor, Permission.TASKS_SET_HUMAN_REQUIRED)
        response = templates.TemplateResponse(
            request,
            "board.html",
            {
                "columns": STATUSES,
                "projects": projects,
                "selected_project": selected_project,
                "tasks_by_status": tasks_by_status,
                "dependencies_by_task": dependencies_by_task,
                "task_count": len(tasks),
                "can_manage_api_keys": can_manage_api_keys,
                "can_set_human_required": can_set_human_required,
                "api_keys": list_agent_api_keys(db) if can_manage_api_keys else [],
                "default_project": request.app.state.settings.default_project,
                "theme": request.app.state.settings.theme,
                "available_themes": [
                    {"value": "neutral", "label": "Neutral"},
                    {"value": "axis-love", "label": "Axis Love"},
                ],
                "asset_version": FLOW_VERSION,
            },
        )
        if (
            actor is not None
            and actor.source in {"admin_header", "browser", "session_cookie"}
            and request.app.state.settings.session_secret
        ):
            response.set_cookie(
                SESSION_COOKIE_NAME,
                sign_session(actor, request.app.state.settings.session_secret),
                max_age=SESSION_MAX_AGE,
                httponly=True,
                samesite="strict",
                secure=request.app.state.settings.session_cookie_secure,
            )
        return response

    return app


app = create_app()
