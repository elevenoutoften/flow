from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..config import FLOW_VERSION
from ..repository import batch_dependency_summaries, list_agent_api_keys, list_projects, list_tasks
from ..schemas import STATUSES
from ..security import (
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE,
    Permission,
    has_permission,
    resolve_actor,
    sign_session,
)
from .dependencies import get_db

PACKAGE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
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
