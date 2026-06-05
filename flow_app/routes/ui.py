from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, Header, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..config import FLOW_VERSION
from ..ratelimit import auth_limiter, client_ip
from ..repository import batch_dependency_summaries, list_agent_api_keys, list_projects, list_tasks, serialize_idea
from ..schemas import STATUSES
from ..security import (
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE,
    Permission,
    has_permission,
    resolve_actor,
    sign_session,
    verify_bearer_token,
)
from ..services.idea import IdeaService
from .dependencies import get_db

PACKAGE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))

router = APIRouter()


def _resolve_web_actor(
    request: Request,
    db: Session,
    authorization: str | None,
    x_axis_admin: str | None,
    x_axis_user: str | None,
    x_axis_agent: str | None,
):
    return resolve_actor(
        db,
        authorization,
        x_axis_admin,
        x_axis_user,
        x_axis_agent,
        trusted_headers=request.app.state.settings.trusted_headers,
        session_cookie=request.cookies.get(SESSION_COOKIE_NAME),
        session_secret=request.app.state.settings.session_secret or None,
    )


def _maybe_set_session_cookie(request: Request, response, actor) -> None:
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
    human_required_count = sum(1 for task in tasks if task.human_required)
    unclaimed_count = sum(1 for task in tasks if not task.assignee)
    counts_by_project: dict[str, int] = {}
    for task in tasks:
        counts_by_project[task.project] = counts_by_project.get(task.project, 0) + 1
    dependencies_by_task = batch_dependency_summaries(db, [task.id for task in tasks])
    dependency_edges = sorted(
        {
            (parent.id, task_id)
            for task_id, summary in dependencies_by_task.items()
            for parent in summary.parent_tasks
        }
        | {
            (task_id, child.id)
            for task_id, summary in dependencies_by_task.items()
            for child in summary.child_tasks
        }
    )
    actor = _resolve_web_actor(request, db, authorization, x_axis_admin, x_axis_user, x_axis_agent)
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
            "dependency_edges": dependency_edges,
            "task_count": len(tasks),
            "human_required_count": human_required_count,
            "unclaimed_count": unclaimed_count,
            "counts_by_project": counts_by_project,
            "can_set_human_required": can_set_human_required,
            "actor": actor,
            "default_project": request.app.state.settings.default_project,
            "theme": request.app.state.settings.theme,
            "asset_version": FLOW_VERSION,
        },
    )
    _maybe_set_session_cookie(request, response, actor)
    return response


@router.get("/ideas.html", response_class=HTMLResponse)
def ideas_surface(
    request: Request,
    db: Session = Depends(get_db),
    project: str | None = Query(default=None),
    archived: bool = Query(default=False),
    authorization: str | None = Header(default=None),
    x_axis_admin: str | None = Header(default=None),
    x_axis_user: str | None = Header(default=None),
    x_axis_agent: str | None = Header(default=None),
):
    selected_project = project.strip() if project else ""
    projects = list_projects(db)
    actor = _resolve_web_actor(request, db, authorization, x_axis_admin, x_axis_user, x_axis_agent)
    ideas = []
    if actor is not None and has_permission(actor, Permission.TASKS_READ):
        svc = IdeaService(db)
        ideas = [
            serialize_idea(idea, db)
            for idea in svc.list_ideas(project=selected_project or None, archived=archived, limit=200, offset=0)
        ]
    response = templates.TemplateResponse(
        request,
        "ideas.html",
        {
            "ideas": ideas,
            "projects": projects,
            "selected_project": selected_project,
            "show_archived": archived,
            "can_edit_ideas": actor is not None and has_permission(actor, Permission.TASKS_EDIT),
            "can_create_ideas": actor is not None and has_permission(actor, Permission.TASKS_CREATE),
            "asset_version": FLOW_VERSION,
        },
    )
    _maybe_set_session_cookie(request, response, actor)
    return response


@router.get("/settings.html", response_class=HTMLResponse)
def settings_surface(
    request: Request,
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
    x_axis_admin: str | None = Header(default=None),
    x_axis_user: str | None = Header(default=None),
    x_axis_agent: str | None = Header(default=None),
):
    projects = list_projects(db)
    actor = _resolve_web_actor(request, db, authorization, x_axis_admin, x_axis_user, x_axis_agent)
    can_manage_api_keys = actor is not None and actor.role.value == "admin"
    response = templates.TemplateResponse(
        request,
        "settings.html",
        {
            "projects": projects,
            "can_manage_api_keys": can_manage_api_keys,
            "api_keys": list_agent_api_keys(db) if can_manage_api_keys else [],
            "theme": request.app.state.settings.theme,
            "available_themes": [
                {"value": "love", "label": "Axis Love", "desc": "Rose / pink accent."},
                {"value": "teal", "label": "Axis Teal", "desc": "Cyan / tiffany accent."},
                {"value": "leaf", "label": "Axis Leaf", "desc": "Green / chartreuse accent."},
                {"value": "neutral", "label": "Neutral", "desc": "Grey accent."},
            ],
            "default_project": request.app.state.settings.default_project,
            "asset_version": FLOW_VERSION,
        },
    )
    _maybe_set_session_cookie(request, response, actor)
    return response


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, error: str | None = Query(default=None)):
    settings = request.app.state.settings
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "session_enabled": bool(settings.session_secret),
            "error": error,
            "theme": settings.theme,
            "asset_version": FLOW_VERSION,
        },
    )


@router.post("/login")
def login_submit(
    request: Request,
    api_key: str = Form(...),
    db: Session = Depends(get_db),
):
    auth_limiter.check(client_ip(request))
    settings = request.app.state.settings
    if not settings.session_secret:
        # Session cookies can't be signed without a secret; flow-serve sets one automatically.
        return RedirectResponse("/login?error=disabled", status_code=303)

    actor = verify_bearer_token(db, api_key.strip())
    if actor is None:
        return RedirectResponse("/login?error=invalid", status_code=303)

    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        sign_session(actor, settings.session_secret),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="strict",
        secure=settings.session_cookie_secure,
    )
    return response


@router.get("/logout")
def logout(request: Request):
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response
