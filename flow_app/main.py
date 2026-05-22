from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import replace
import json
from pathlib import Path
from uuid import uuid4
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import FLOW_VERSION, FlowSettings, get_settings
from .database import Base, build_engine, build_session_factory, default_database_url
from .dispatcher import DispatchError, _next_capable_task, complete_run, dispatch_one, heartbeat_run, set_session_factory, stale_recovery
from .markdown_import import parse_markdown_tasks
from .mcp import JsonRpcError, error_response, exception_response, handle_mcp_message
from .models import Task, utcnow
from .repository import (
    add_note,
    archive_idea,
    auto_promote_unblocked_children,
    create_agent,
    create_agent_api_key,
    create_automation_rule,
    create_task_handoff,
    create_webhook_config,
    create_idea,
    create_project,
    create_task,
    create_task_link,
    create_workspace_config,
    delete_task_link,
    delete_webhook_config,
    evaluate_rules,
    ensure_project,
    find_import_duplicate,
    get_agent,
    get_agent_api_key,
    get_agent_run,
    get_automation_rule,
    get_idea,
    get_project,
    get_task,
    get_task_handoff,
    get_dependency_summary,
    get_webhook_config,
    get_webhook_delivery,
    get_workspace_config,
    list_agent_api_keys,
    list_agent_runs,
    list_agents,
    list_automation_rules,
    list_ideas,
    list_projects,
    list_task_links,
    list_task_handoffs,
    list_tasks,
    list_webhook_configs,
    list_webhook_deliveries,
    list_workspace_configs,
    next_task,
    promote_tasks,
    require_task,
    revoke_agent_api_key,
    serialize_agent_api_key,
    serialize_agent,
    serialize_agent_run,
    serialize_automation_rule,
    serialize_created_agent_api_key,
    serialize_idea,
    serialize_project,
    serialize_task,
    serialize_task_handoff,
    serialize_task_link,
    serialize_webhook_config,
    serialize_webhook_delivery,
    serialize_workspace_config,
    unarchive_idea,
    update_agent,
    update_automation_rule,
    update_idea,
    update_project,
    update_task,
    update_webhook_config,
    update_webhook_delivery,
    update_workspace_config,
)
from .notifications import WebhookNotificationProvider
from .rules_engine import emit_event as emit_rule_event
from .schemas import (
    AgentApiKeyCreate,
    AgentApiKeyCreateResponse,
    AgentApiKeyResponse,
    AgentCreate,
    AgentResponse,
    AgentRunResponse,
    AgentUpdate,
    AutomationEvent,
    AutomationRuleCreate,
    AutomationRuleResponse,
    AutomationRuleUpdate,
    ApiKeyRole,
    IdeaCreate,
    IdeaResponse,
    IdeaUpdate,
    MarkdownImportCommitRequest,
    MarkdownImportCommitResponse,
    MarkdownImportPreviewRequest,
    MarkdownImportPreviewResponse,
    MoveRequest,
    NoteRequest,
    PromoteTaskSpec,
    ProjectCreate,
    ProjectResponse,
    ProjectUpdate,
    STATUSES,
    ClaimRequest,
    DoneRequest,
    DependencySummary,
    HandoffRequest,
    HandoffResponse,
    TaskCreate,
    TaskLinkCreate,
    TaskLinkResponse,
    TaskResponse,
    TaskUpdate,
    WebhookConfigCreate,
    WebhookConfigCreateResponse,
    WebhookConfigResponse,
    WebhookConfigUpdate,
    WebhookDeliveryDetailResponse,
    WebhookDeliveryResponse,
    WebhookDeliveryRetryResponse,
    WorkspaceCleanupRequest,
    WorkspaceConfigCreate,
    WorkspaceConfigResponse,
    WorkspaceConfigUpdate,
)
from .security import (
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE,
    Actor,
    Permission,
    PermissionDenied,
    authorize_task_update,
    has_permission,
    is_valid_transition,
    require_permission,
    resolve_actor,
    sign_session,
)
from .webhooks import WEBHOOK_EVENTS, deliver_webhook
from .telegram import TelegramNotificationProvider
from .workspace import cleanup_workspace, provision_workspace

PACKAGE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))
_webhook_notifier = WebhookNotificationProvider()
_telegram_notifier = TelegramNotificationProvider()


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

    def get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

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
        dependencies_by_task = {task.id: get_dependency_summary(db, task.id) for task in tasks}
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

    @app.get("/api/projects", response_model=list[ProjectResponse])
    def api_list_projects(
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.TASKS_READ)),
    ):
        return [serialize_project(project) for project in list_projects(db)]

    @app.post("/api/projects", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
    def api_create_project(
        payload: ProjectCreate,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.TASKS_CREATE)),
    ):
        project = create_project(db, payload)
        _commit(db)
        return serialize_project(project)

    @app.get("/api/projects/{slug}", response_model=ProjectResponse)
    def api_get_project(
        slug: str,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.TASKS_READ)),
    ):
        project = get_project(db, slug)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found.")
        return serialize_project(project)

    @app.patch("/api/projects/{slug}", response_model=ProjectResponse)
    def api_update_project(
        slug: str,
        payload: ProjectUpdate,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.TASKS_EDIT)),
    ):
        project = get_project(db, slug)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found.")
        project = update_project(db, project, payload)
        _commit(db)
        return serialize_project(project)

    @app.get("/api/api-keys", response_model=list[AgentApiKeyResponse])
    def api_list_agent_api_keys(
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.KEY_MANAGE)),
    ):
        return [serialize_agent_api_key(api_key) for api_key in list_agent_api_keys(db)]

    @app.post("/api/api-keys", response_model=AgentApiKeyCreateResponse, status_code=status.HTTP_201_CREATED)
    def api_create_agent_api_key(
        payload: AgentApiKeyCreate,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.KEY_MANAGE)),
    ):
        api_key, raw_key = create_agent_api_key(db, payload)
        _commit(db)
        return serialize_created_agent_api_key(api_key, raw_key)

    @app.post("/api/api-keys/{api_key_id}/revoke", response_model=AgentApiKeyResponse)
    def api_revoke_agent_api_key(
        api_key_id: str,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.KEY_MANAGE)),
    ):
        api_key = get_agent_api_key(db, api_key_id)
        if api_key is None:
            raise HTTPException(status_code=404, detail="API key not found.")
        api_key = revoke_agent_api_key(db, api_key)
        _commit(db)
        return serialize_agent_api_key(api_key)

    @app.get("/api/agents", response_model=list[AgentResponse])
    def api_list_agents(
        db: Session = Depends(get_db),
        enabled_only: bool = Query(default=False),
        _actor: Actor = Depends(require_permission(Permission.AGENT_READ)),
    ):
        return [serialize_agent(agent) for agent in list_agents(db, enabled_only=enabled_only)]

    @app.get("/api/agents/{agent_id}", response_model=AgentResponse)
    def api_get_agent(
        agent_id: str,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.AGENT_READ)),
    ):
        agent = get_agent(db, agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="Agent not found.")
        return serialize_agent(agent)

    @app.post("/api/agents", response_model=AgentResponse, status_code=status.HTTP_201_CREATED)
    def api_create_agent(
        payload: AgentCreate,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.AGENT_MANAGE)),
    ):
        agent = create_agent(db, payload)
        _commit(db)
        return serialize_agent(agent)

    @app.patch("/api/agents/{agent_id}", response_model=AgentResponse)
    def api_update_agent(
        agent_id: str,
        payload: AgentUpdate,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.AGENT_MANAGE)),
    ):
        agent = get_agent(db, agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="Agent not found.")
        agent = update_agent(db, agent, payload)
        _commit(db)
        return serialize_agent(agent)

    @app.get("/api/workspace-configs", response_model=list[WorkspaceConfigResponse])
    def api_list_workspace_configs(
        db: Session = Depends(get_db),
        enabled_only: bool = Query(default=False),
        _actor: Actor = Depends(require_permission(Permission.WORKSPACE_READ)),
    ):
        return [
            serialize_workspace_config(config)
            for config in list_workspace_configs(db, enabled_only=enabled_only)
        ]

    @app.get("/api/workspace-configs/{config_id}", response_model=WorkspaceConfigResponse)
    def api_get_workspace_config(
        config_id: str,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.WORKSPACE_READ)),
    ):
        config = get_workspace_config(db, config_id)
        if config is None:
            raise HTTPException(status_code=404, detail="Workspace config not found.")
        return serialize_workspace_config(config)

    @app.post("/api/workspace-configs", response_model=WorkspaceConfigResponse, status_code=status.HTTP_201_CREATED)
    def api_create_workspace_config(
        payload: WorkspaceConfigCreate,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.WORKSPACE_MANAGE)),
    ):
        config = create_workspace_config(db, payload)
        _commit(db)
        return serialize_workspace_config(config)

    @app.patch("/api/workspace-configs/{config_id}", response_model=WorkspaceConfigResponse)
    def api_update_workspace_config(
        config_id: str,
        payload: WorkspaceConfigUpdate,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.WORKSPACE_MANAGE)),
    ):
        config = get_workspace_config(db, config_id)
        if config is None:
            raise HTTPException(status_code=404, detail="Workspace config not found.")
        config = update_workspace_config(db, config, payload)
        _commit(db)
        return serialize_workspace_config(config)

    @app.post("/api/workspace-configs/{config_id}/provision")
    async def api_provision_workspace(
        config_id: str,
        task_id: str = Query(...),
        repo_path: str | None = Query(default=None),
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.WORKSPACE_MANAGE)),
    ):
        config = get_workspace_config(db, config_id)
        if config is None:
            raise HTTPException(status_code=404, detail="Workspace config not found.")
        return provision_workspace(config, task_id, repo_path)

    @app.post("/api/workspace-configs/{config_id}/cleanup")
    async def api_cleanup_workspace(
        config_id: str,
        payload: WorkspaceCleanupRequest,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.WORKSPACE_MANAGE)),
    ):
        if get_workspace_config(db, config_id) is None:
            raise HTTPException(status_code=404, detail="Workspace config not found.")
        return {
            "workspace_id": config_id,
            "strategy": payload.strategy,
            "path": payload.path,
            "cleaned": cleanup_workspace(config_id, payload.strategy, payload.path),
        }

    @app.get("/api/automation-rules", response_model=list[AutomationRuleResponse])
    def api_list_automation_rules(
        db: Session = Depends(get_db),
        enabled_only: bool = Query(default=False),
        _actor: Actor = Depends(require_permission(Permission.RULES_READ)),
    ):
        return [serialize_automation_rule(rule) for rule in list_automation_rules(db, enabled_only=enabled_only)]

    @app.post("/api/automation-rules/evaluate")
    def api_evaluate_automation_rules(
        payload: AutomationEvent,
        db: Session = Depends(get_db),
        actor: Actor = Depends(require_permission(Permission.RULES_EVALUATE)),
    ):
        matches = evaluate_rules(db, payload, actor=actor)
        _commit(db)
        return {"matches": matches, "count": len(matches)}

    @app.get("/api/automation-rules/{rule_id}", response_model=AutomationRuleResponse)
    def api_get_automation_rule(
        rule_id: str,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.RULES_READ)),
    ):
        rule = get_automation_rule(db, rule_id)
        if rule is None:
            raise HTTPException(status_code=404, detail="Automation rule not found.")
        return serialize_automation_rule(rule)

    @app.post("/api/automation-rules", response_model=AutomationRuleResponse, status_code=status.HTTP_201_CREATED)
    def api_create_automation_rule(
        payload: AutomationRuleCreate,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.RULES_MANAGE)),
    ):
        rule = create_automation_rule(db, payload)
        _commit(db)
        return serialize_automation_rule(rule)

    @app.patch("/api/automation-rules/{rule_id}", response_model=AutomationRuleResponse)
    def api_update_automation_rule(
        rule_id: str,
        payload: AutomationRuleUpdate,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.RULES_MANAGE)),
    ):
        rule = get_automation_rule(db, rule_id)
        if rule is None:
            raise HTTPException(status_code=404, detail="Automation rule not found.")
        rule = update_automation_rule(db, rule, payload)
        _commit(db)
        return serialize_automation_rule(rule)

    @app.get("/api/agent-runs", response_model=list[AgentRunResponse])
    def api_list_agent_runs(
        db: Session = Depends(get_db),
        agent_id: str | None = Query(default=None),
        task_id: str | None = Query(default=None),
        status_filter: str | None = Query(default=None, alias="status"),
        _actor: Actor = Depends(require_permission(Permission.TASKS_READ)),
    ):
        return [
            serialize_agent_run(run)
            for run in list_agent_runs(db, agent_id=agent_id, task_id=task_id, status=status_filter)
        ]

    @app.get("/api/agent-runs/{run_id}", response_model=AgentRunResponse)
    def api_get_agent_run(
        run_id: str,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.TASKS_READ)),
    ):
        run = get_agent_run(db, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Agent run not found.")
        return serialize_agent_run(run)

    @app.post("/api/agents/{agent_id}/dispatch", response_model=AgentRunResponse)
    def api_dispatch_agent(
        agent_id: str,
        request: Request,
        db: Session = Depends(get_db),
        task_id: str | None = Query(default=None),
        actor: Actor = Depends(require_permission(Permission.DISPATCH)),
    ):
        agent = get_agent(db, agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="Agent not found.")
        if task_id:
            task = _require_task(db, task_id)
        else:
            task = _next_capable_task(db, agent)
        if task is None:
            raise HTTPException(status_code=404, detail="No eligible task found for agent dispatch_statuses.")
        try:
            run = dispatch_one(
                db,
                agent,
                task,
                api_key=request.headers.get("authorization", "").removeprefix("Bearer ").strip(),
                base_url=str(request.base_url).rstrip("/"),
            )
        except DispatchError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        _commit(db)
        return serialize_agent_run(run)

    @app.post("/api/agent-runs/{run_id}/heartbeat", response_model=AgentRunResponse)
    def api_heartbeat_agent_run(
        run_id: str,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.DISPATCH)),
    ):
        run = get_agent_run(db, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Agent run not found.")
        run = heartbeat_run(db, run)
        _commit(db)
        return serialize_agent_run(run)

    @app.post("/api/agent-runs/{run_id}/complete", response_model=AgentRunResponse)
    def api_complete_agent_run(
        run_id: str,
        db: Session = Depends(get_db),
        exit_code: int = Query(...),
        _actor: Actor = Depends(require_permission(Permission.DISPATCH)),
    ):
        run = get_agent_run(db, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Agent run not found.")
        run = complete_run(db, run, exit_code)
        _commit(db)
        return serialize_agent_run(run)

    @app.post("/api/agent-runs/stale-recovery")
    def api_stale_recovery(
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.TASKS_EDIT)),
    ):
        recovered = stale_recovery(db)
        _commit(db)
        return {"recovered_run_ids": recovered, "count": len(recovered)}

    @app.post("/api/import/markdown/preview", response_model=MarkdownImportPreviewResponse)
    def api_preview_markdown_import(
        payload: MarkdownImportPreviewRequest,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.TASKS_CREATE)),
    ):
        items = parse_markdown_tasks(
            payload.markdown,
            source_filename=payload.source_filename,
            default_project=payload.default_project,
            default_status=payload.default_status,
            default_priority=payload.default_priority,
        )
        for item in items:
            duplicate = find_import_duplicate(
                db,
                project=item.project,
                title=item.title,
                source_filename=item.source_filename,
                source_line=item.source_line,
            )
            if duplicate:
                item.duplicate = True
                item.duplicate_task_id = duplicate.id
        return MarkdownImportPreviewResponse(items=items)

    @app.post("/api/import/markdown/commit", response_model=MarkdownImportCommitResponse)
    def api_commit_markdown_import(
        payload: MarkdownImportCommitRequest,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.TASKS_CREATE)),
    ):
        batch_id = f"import_{uuid4().hex[:12]}"
        created = []
        skipped = []
        for item in payload.items:
            duplicate = find_import_duplicate(
                db,
                project=item.project,
                title=item.title,
                source_filename=item.source_filename,
                source_line=item.source_line,
            )
            if item.duplicate or duplicate:
                item.duplicate = True
                item.duplicate_task_id = duplicate.id if duplicate else item.duplicate_task_id
                skipped.append(item)
                continue

            task = create_task(
                db,
                TaskCreate(
                    title=item.title,
                    status=item.status,
                    priority=item.priority,
                    project=item.project,
                    assignee=item.assignee,
                    description=item.description,
                    acceptance_criteria=item.acceptance_criteria,
                    source_filename=item.source_filename,
                    source_line=item.source_line,
                    import_batch_id=batch_id,
                    source_title=item.source_title,
                ),
            )
            created.append(serialize_task(task))

        _commit(db)
        return MarkdownImportCommitResponse(import_batch_id=batch_id, created=created, skipped=skipped)

    @app.get("/api/tasks/next", response_model=TaskResponse)
    def api_next_task(
        db: Session = Depends(get_db),
        project: str | None = Query(default=None),
        _actor: Actor = Depends(require_permission(Permission.TASKS_READ)),
    ):
        task = next_task(db, project=project)
        if task is None:
            raise HTTPException(status_code=404, detail="No unclaimed task is available.")
        return serialize_task(task)

    @app.get("/api/tasks", response_model=list[TaskResponse])
    def api_list_tasks(
        db: Session = Depends(get_db),
        project: str | None = Query(default=None),
        status_filter: str | None = Query(default=None, alias="status"),
        assignee: str | None = Query(default=None),
        unclaimed: bool = Query(default=False),
        _actor: Actor = Depends(require_permission(Permission.TASKS_READ)),
    ):
        if status_filter and status_filter not in STATUSES:
            raise HTTPException(status_code=422, detail="Invalid task status.")
        return [
            serialize_task(task)
            for task in list_tasks(
                db,
                project=project,
                status=status_filter,
                assignee=assignee,
                unclaimed=unclaimed,
            )
        ]

    @app.post("/api/tasks", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
    def api_create_task(
        payload: TaskCreate,
        db: Session = Depends(get_db),
        actor: Actor = Depends(require_permission(Permission.TASKS_CREATE)),
    ):
        task = create_task(db, payload)
        emit_rule_event(db, "task_created", task_id=task.id, actor=actor)
        _commit(db)
        _webhook_notifier.send(db, "task_created", task)
        _commit(db)
        _telegram_notifier.send(db, "task_created", task)
        _commit(db)
        return serialize_task(task)

    @app.get("/api/tasks/{task_id}", response_model=TaskResponse)
    def api_get_task(
        task_id: str,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.TASKS_READ)),
    ):
        return serialize_task(_require_task(db, task_id))

    @app.get("/api/tasks/{task_id}/handoffs", response_model=list[HandoffResponse])
    def api_list_task_handoffs(
        task_id: str,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.TASKS_READ)),
    ):
        _require_task(db, task_id)
        return [serialize_task_handoff(handoff) for handoff in list_task_handoffs(db, task_id)]

    @app.get("/api/tasks/{task_id}/handoffs/{handoff_id}", response_model=HandoffResponse)
    def api_get_task_handoff(
        task_id: str,
        handoff_id: str,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.TASKS_READ)),
    ):
        _require_task(db, task_id)
        handoff = get_task_handoff(db, handoff_id)
        if handoff is None or handoff.task_id != task_id:
            raise HTTPException(status_code=404, detail="Task handoff not found.")
        return serialize_task_handoff(handoff)

    @app.post("/api/tasks/{task_id}/handoffs", response_model=HandoffResponse, status_code=status.HTTP_201_CREATED)
    @app.post("/api/tasks/{task_id}/handoff", response_model=HandoffResponse, status_code=status.HTTP_201_CREATED)
    def api_create_task_handoff(
        task_id: str,
        payload: HandoffRequest,
        db: Session = Depends(get_db),
        actor: Actor = Depends(require_permission(Permission.HANDOFF_CREATE)),
    ):
        _require_task(db, task_id)
        handoff = create_task_handoff(
            db,
            task_id,
            payload.author or actor.name,
            payload.summary,
            payload.changed_files,
            payload.commands_run,
            payload.tests_run,
            payload.artifacts,
            payload.attempted_but_failed,
            payload.remaining_work,
            payload.outcome,
            payload.next_recommended_agent,
            payload.capabilities,
        )
        _commit(db)
        return serialize_task_handoff(handoff)

    @app.get("/api/tasks/{task_id}/links", response_model=list[TaskLinkResponse])
    def api_list_task_links(
        task_id: str,
        db: Session = Depends(get_db),
        link_type: str | None = Query(default=None),
        _actor: Actor = Depends(require_permission(Permission.LINKS_READ)),
    ):
        _require_task(db, task_id)
        return [serialize_task_link(link) for link in list_task_links(db, task_id=task_id, link_type=link_type)]

    @app.get("/api/tasks/{task_id}/dependencies", response_model=DependencySummary)
    def api_get_dependencies(
        task_id: str,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.LINKS_READ)),
    ):
        try:
            return get_dependency_summary(db, task_id)
        except ValueError:
            raise HTTPException(status_code=404, detail="Task not found.")

    @app.post("/api/tasks/{task_id}/link", response_model=TaskLinkResponse, status_code=status.HTTP_201_CREATED)
    def api_link_task(
        task_id: str,
        payload: TaskLinkCreate,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.LINKS_MANAGE)),
    ):
        _require_task(db, task_id)
        if task_id not in {payload.parent_id, payload.child_id}:
            raise HTTPException(status_code=400, detail="Path task_id must be one endpoint of the link.")
        try:
            link = create_task_link(db, payload)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        _commit(db)
        return serialize_task_link(link)

    @app.delete("/api/tasks/{task_id}/link/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
    def api_unlink_task(
        task_id: str,
        link_id: str,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.LINKS_MANAGE)),
    ):
        _require_task(db, task_id)
        link = next((item for item in list_task_links(db, task_id=task_id) if item.id == link_id), None)
        if link is None:
            raise HTTPException(status_code=404, detail="Task link not found.")
        delete_task_link(db, link_id)
        _commit(db)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.patch("/api/tasks/{task_id}", response_model=TaskResponse)
    def api_update_task(
        task_id: str,
        payload: TaskUpdate,
        db: Session = Depends(get_db),
        actor: Actor = Depends(require_permission(Permission.TASKS_READ)),
    ):
        task = _require_task(db, task_id)
        try:
            authorize_task_update(actor, task, payload)
        except PermissionDenied as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=exc.detail)
        if payload.human_required is False:
            payload = payload.model_copy(update={"blocker_reason": ""})
        was_human_required = bool(task.human_required)
        task = update_task(db, task, payload)
        _commit(db)
        if not was_human_required and bool(task.human_required):
            emit_rule_event(db, "task_blocked", task_id=task.id, actor=actor)
            _webhook_notifier.send(
                db,
                "task_blocked",
                task,
                {"human_required": True, "blocker_reason": task.blocker_reason},
            )
            _commit(db)
            _telegram_notifier.send(
                db,
                "task_blocked",
                task,
                {"human_required": True, "blocker_reason": task.blocker_reason},
            )
            _commit(db)
        return serialize_task(task)

    @app.post("/api/tasks/{task_id}/claim", response_model=TaskResponse)
    def api_claim_task(
        task_id: str,
        payload: ClaimRequest,
        db: Session = Depends(get_db),
        actor: Actor = Depends(require_permission(Permission.TASKS_CLAIM)),
    ):
        task = _require_task(db, task_id)
        old_status = task.status
        assignee = payload.agent_name or actor.name
        if not assignee:
            raise HTTPException(status_code=400, detail="agent_name is required.")
        if task.assignee and task.assignee != assignee:
            raise HTTPException(status_code=409, detail=f"Task is already claimed by {task.assignee}.")
        task.assignee = assignee
        if task.status in {"backlog", "todo"}:
            if not is_valid_transition(actor, task.status, "doing"):
                raise HTTPException(
                    status_code=403,
                    detail=f"Role '{actor.role.value}' cannot move task from {task.status} to doing.",
                )
            task.status = "doing"
        update_task(db, task, TaskUpdate())
        emit_rule_event(db, "task_claimed", task_id=task_id, data={"assignee": assignee}, actor=actor)
        _commit(db)
        _webhook_notifier.send(
            db,
            "task_claimed",
            task,
            {"status": {"from": old_status, "to": task.status}, "assignee": task.assignee},
        )
        _commit(db)
        _telegram_notifier.send(
            db,
            "task_claimed",
            task,
            {"status": {"from": old_status, "to": task.status}, "assignee": task.assignee},
        )
        _commit(db)
        return serialize_task(task)

    @app.post("/api/tasks/{task_id}/release", response_model=TaskResponse)
    def api_release_task(
        task_id: str,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.TASKS_CLAIM)),
    ):
        task = _require_task(db, task_id)
        task.assignee = None
        if task.status == "doing":
            task.status = "todo"
        update_task(db, task, TaskUpdate())
        _commit(db)
        return serialize_task(task)

    @app.post("/api/tasks/{task_id}/move", response_model=TaskResponse)
    def api_move_task(
        task_id: str,
        payload: MoveRequest,
        db: Session = Depends(get_db),
        actor: Actor = Depends(require_permission(Permission.TASKS_MOVE)),
    ):
        task = _require_task(db, task_id)
        old_status = task.status
        if not is_valid_transition(actor, task.status, payload.status):
            raise HTTPException(
                status_code=403,
                detail=f"Role '{actor.role.value}' cannot move task from {task.status} to {payload.status}.",
            )
        task.status = payload.status
        update_task(db, task, TaskUpdate())
        if payload.status == "done":
            auto_promote_unblocked_children(db, task.id)
        emit_rule_event(
            db,
            "task_moved",
            task_id=task_id,
            data={"from_status": old_status, "to_status": payload.status},
            actor=actor,
        )
        _commit(db)
        _webhook_notifier.send(
            db,
            "task_moved",
            task,
            {"status": {"from": old_status, "to": task.status}},
        )
        _commit(db)
        _telegram_notifier.send(
            db,
            "task_moved",
            task,
            {"status": {"from": old_status, "to": task.status}},
        )
        _commit(db)
        return serialize_task(task)

    @app.post("/api/tasks/{task_id}/note", response_model=TaskResponse)
    def api_add_note(
        task_id: str,
        payload: NoteRequest,
        db: Session = Depends(get_db),
        actor: Actor = Depends(require_permission(Permission.TASKS_NOTE)),
    ):
        task = _require_task(db, task_id)
        author = payload.author or actor.name
        add_note(db, task, payload.note, author=author)
        _commit(db)
        task = _require_task(db, task_id)
        return serialize_task(task)

    @app.post("/api/tasks/{task_id}/done", response_model=TaskResponse)
    def api_done_task(
        task_id: str,
        payload: DoneRequest,
        db: Session = Depends(get_db),
        actor: Actor = Depends(require_permission(Permission.TASKS_DONE)),
    ):
        task = _require_task(db, task_id)
        old_status = task.status
        if not is_valid_transition(actor, task.status, "done"):
            raise HTTPException(
                status_code=403,
                detail=f"Role '{actor.role.value}' cannot mark task as done from {task.status}.",
            )
        task.status = "done"
        author = payload.author or actor.name
        if payload.handoff is not None:
            handoff_author = payload.handoff.author or author
            create_task_handoff(
                db,
                task.id,
                handoff_author,
                payload.handoff.summary,
                payload.handoff.changed_files,
                payload.handoff.commands_run,
                payload.handoff.tests_run,
                payload.handoff.artifacts,
                payload.handoff.attempted_but_failed,
                payload.handoff.remaining_work,
                payload.handoff.outcome,
                payload.handoff.next_recommended_agent,
                payload.handoff.capabilities,
            )
        add_note(db, task, payload.summary, author=author)
        update_task(db, task, TaskUpdate())
        auto_promote_unblocked_children(db, task.id)
        emit_rule_event(db, "task_completed", task_id=task_id, actor=actor)
        _commit(db)
        _webhook_notifier.send(
            db,
            "task_completed",
            task,
            {"status": {"from": old_status, "to": "done"}},
        )
        _commit(db)
        _telegram_notifier.send(
            db,
            "task_completed",
            task,
            {"status": {"from": old_status, "to": "done"}},
        )
        _commit(db)
        task = _require_task(db, task_id)
        return serialize_task(task)

    @app.get("/api/ideas", response_model=list[IdeaResponse])
    def api_list_ideas(
        db: Session = Depends(get_db),
        project: str | None = Query(default=None),
        archived: bool = Query(default=False),
        _actor: Actor = Depends(require_permission(Permission.TASKS_READ)),
    ):
        return [serialize_idea(idea, db) for idea in list_ideas(db, project=project, archived=archived)]

    @app.post("/api/ideas", response_model=IdeaResponse, status_code=status.HTTP_201_CREATED)
    def api_create_idea(
        payload: IdeaCreate,
        db: Session = Depends(get_db),
        actor: Actor = Depends(require_permission(Permission.TASKS_CREATE)),
    ):
        if not payload.author and actor.name:
            payload.author = actor.name
        idea = create_idea(db, payload)
        _commit(db)
        return serialize_idea(idea, db)

    @app.get("/api/ideas/{idea_id}", response_model=IdeaResponse)
    def api_get_idea(
        idea_id: str,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.TASKS_READ)),
    ):
        idea = get_idea(db, idea_id)
        if idea is None:
            raise HTTPException(status_code=404, detail="Idea not found.")
        return serialize_idea(idea, db)

    @app.patch("/api/ideas/{idea_id}", response_model=IdeaResponse)
    def api_update_idea(
        idea_id: str,
        payload: IdeaUpdate,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.TASKS_EDIT)),
    ):
        idea = get_idea(db, idea_id)
        if idea is None:
            raise HTTPException(status_code=404, detail="Idea not found.")
        idea = update_idea(db, idea, payload)
        _commit(db)
        return serialize_idea(idea, db)

    @app.post("/api/ideas/{idea_id}/archive", response_model=IdeaResponse)
    def api_archive_idea(
        idea_id: str,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.TASKS_EDIT)),
    ):
        idea = get_idea(db, idea_id)
        if idea is None:
            raise HTTPException(status_code=404, detail="Idea not found.")
        idea = archive_idea(db, idea)
        _commit(db)
        return serialize_idea(idea, db)

    @app.post("/api/ideas/{idea_id}/unarchive", response_model=IdeaResponse)
    def api_unarchive_idea(
        idea_id: str,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.TASKS_EDIT)),
    ):
        idea = get_idea(db, idea_id)
        if idea is None:
            raise HTTPException(status_code=404, detail="Idea not found.")
        idea = unarchive_idea(db, idea)
        _commit(db)
        return serialize_idea(idea, db)

    @app.post("/api/ideas/{idea_id}/promote", response_model=IdeaResponse)
    def api_promote_idea(
        idea_id: str,
        payload: list[PromoteTaskSpec],
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.TASKS_CREATE)),
    ):
        idea = get_idea(db, idea_id)
        if idea is None:
            raise HTTPException(status_code=404, detail="Idea not found.")
        tasks, idea = promote_tasks(db, idea, payload)
        _commit(db)
        for task in tasks:
            _webhook_notifier.send(db, "idea_promoted", task, {"idea_id": idea.id})
        _commit(db)
        return serialize_idea(idea, db)

    @app.post("/api/webhooks", response_model=WebhookConfigCreateResponse, status_code=status.HTTP_201_CREATED)
    def api_create_webhook(
        payload: WebhookConfigCreate,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.WEBHOOK_MANAGE)),
    ):
        _validate_webhook_events(payload.events)
        config, raw_secret = create_webhook_config(
            db,
            payload.name,
            payload.url,
            payload.events,
            payload.project,
            payload.max_retries,
            payload.retry_backoff_seconds,
        )
        _commit(db)
        data = serialize_webhook_config(config).model_dump()
        return WebhookConfigCreateResponse(**data, secret=raw_secret)

    @app.get("/api/webhooks", response_model=list[WebhookConfigResponse])
    def api_list_webhooks(
        db: Session = Depends(get_db),
        project: str | None = Query(default=None),
        _actor: Actor = Depends(require_permission(Permission.WEBHOOK_READ)),
    ):
        return [serialize_webhook_config(config) for config in list_webhook_configs(db, project=project)]

    @app.get("/api/webhooks/{webhook_id}", response_model=WebhookConfigResponse)
    def api_get_webhook(
        webhook_id: str,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.WEBHOOK_READ)),
    ):
        config = get_webhook_config(db, webhook_id)
        if config is None:
            raise HTTPException(status_code=404, detail="Webhook not found.")
        return serialize_webhook_config(config)

    @app.patch("/api/webhooks/{webhook_id}", response_model=WebhookConfigResponse)
    def api_update_webhook(
        webhook_id: str,
        payload: WebhookConfigUpdate,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.WEBHOOK_MANAGE)),
    ):
        config = get_webhook_config(db, webhook_id)
        if config is None:
            raise HTTPException(status_code=404, detail="Webhook not found.")
        updates = {key: value for key, value in payload.model_dump(exclude_unset=True).items() if value is not None}
        if "events" in updates:
            _validate_webhook_events(updates["events"])
        config = update_webhook_config(db, config, updates)
        _commit(db)
        return serialize_webhook_config(config)

    @app.delete("/api/webhooks/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
    def api_delete_webhook(
        webhook_id: str,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.WEBHOOK_MANAGE)),
    ):
        config = get_webhook_config(db, webhook_id)
        if config is None:
            raise HTTPException(status_code=404, detail="Webhook not found.")
        delete_webhook_config(db, config)
        _commit(db)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/api/webhooks/{webhook_id}/deliveries", response_model=list[WebhookDeliveryResponse])
    def api_list_webhook_deliveries(
        webhook_id: str,
        db: Session = Depends(get_db),
        status_filter: str | None = Query(default=None, alias="status"),
        _actor: Actor = Depends(require_permission(Permission.WEBHOOK_READ)),
    ):
        if get_webhook_config(db, webhook_id) is None:
            raise HTTPException(status_code=404, detail="Webhook not found.")
        if status_filter and status_filter not in {"pending", "success", "failed", "retrying"}:
            raise HTTPException(status_code=422, detail="Invalid delivery status.")
        return [
            serialize_webhook_delivery(delivery)
            for delivery in list_webhook_deliveries(db, webhook_id, status=status_filter)
        ]

    @app.get("/api/webhooks/{webhook_id}/deliveries/{delivery_id}", response_model=WebhookDeliveryDetailResponse)
    def api_get_webhook_delivery(
        webhook_id: str,
        delivery_id: str,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.WEBHOOK_READ)),
    ):
        delivery = _require_webhook_delivery(db, webhook_id, delivery_id)
        data = serialize_webhook_delivery(delivery).model_dump()
        return WebhookDeliveryDetailResponse(**data, payload=delivery.payload)

    @app.post(
        "/api/webhooks/{webhook_id}/deliveries/{delivery_id}/retry",
        response_model=WebhookDeliveryRetryResponse,
    )
    def api_retry_webhook_delivery(
        webhook_id: str,
        delivery_id: str,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.WEBHOOK_READ)),
    ):
        config = get_webhook_config(db, webhook_id)
        if config is None:
            raise HTTPException(status_code=404, detail="Webhook not found.")
        delivery = _require_webhook_delivery(db, webhook_id, delivery_id)
        if delivery.status != "failed":
            raise HTTPException(status_code=409, detail="Only failed deliveries can be retried.")
        update_webhook_delivery(db, delivery, status="pending", next_attempt_at=utcnow())
        deliver_webhook(db, delivery, config)
        _commit(db)
        return WebhookDeliveryRetryResponse(
            id=delivery.id,
            status=delivery.status,
            message=f"Delivery retry finished with status {delivery.status}.",
        )

    return app


def _require_task(db: Session, task_id: str) -> Task:
    try:
        return require_task(db, task_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Task not found.")


def _require_webhook_delivery(db: Session, webhook_id: str, delivery_id: str):
    delivery = get_webhook_delivery(db, delivery_id)
    if delivery is None or delivery.webhook_id != webhook_id:
        raise HTTPException(status_code=404, detail="Webhook delivery not found.")
    return delivery


def _validate_webhook_events(events: list[str]) -> None:
    invalid = [event for event in events if event not in WEBHOOK_EVENTS]
    if invalid:
        raise HTTPException(status_code=422, detail=f"Invalid webhook event: {invalid[0]}")


def _commit(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"Database conflict: {exc.orig}")
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Internal server error.")


def ensure_compatible_schema(engine) -> None:
    inspector = inspect(engine)
    table_names = inspector.get_table_names()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS agents (
                    id VARCHAR(32) NOT NULL PRIMARY KEY,
                    name VARCHAR(180) NOT NULL UNIQUE,
                    description TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    agent_type VARCHAR(24) NOT NULL DEFAULT 'cli',
                    capabilities TEXT NOT NULL DEFAULT '',
                    command TEXT NOT NULL DEFAULT '',
                    env_allowlist TEXT NOT NULL DEFAULT '',
                    working_directory VARCHAR(500) NOT NULL DEFAULT '',
                    max_concurrency INTEGER NOT NULL DEFAULT 1,
                    heartbeat_timeout_seconds INTEGER NOT NULL DEFAULT 300,
                    stale_claim_timeout_seconds INTEGER NOT NULL DEFAULT 600,
                    dispatch_statuses TEXT NOT NULL DEFAULT 'backlog,todo',
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS agent_runs (
                    id VARCHAR(32) NOT NULL PRIMARY KEY,
                    agent_id VARCHAR(32) NOT NULL,
                    task_id VARCHAR(32) NOT NULL,
                    status VARCHAR(24) NOT NULL DEFAULT 'pending',
                    pid INTEGER,
                    exit_code INTEGER,
                    started_at DATETIME,
                    finished_at DATETIME,
                    last_heartbeat_at DATETIME,
                    workspace_state TEXT NOT NULL DEFAULT '',
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE CASCADE,
                    FOREIGN KEY(task_id) REFERENCES tasks (id) ON DELETE CASCADE
                )
                """
            )
        )
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_agent_runs_agent_id ON agent_runs (agent_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_agent_runs_task_id ON agent_runs (task_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_agent_runs_status ON agent_runs (status)"))
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS automation_rules (
                    id VARCHAR(32) NOT NULL PRIMARY KEY,
                    name VARCHAR(180) NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    priority INTEGER NOT NULL DEFAULT 50,
                    trigger VARCHAR(60) NOT NULL,
                    trigger_config TEXT NOT NULL DEFAULT '',
                    conditions TEXT NOT NULL DEFAULT '',
                    actions TEXT NOT NULL DEFAULT '',
                    last_run_at DATETIME,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS task_links (
                    id VARCHAR(32) NOT NULL PRIMARY KEY,
                    parent_id VARCHAR(32) NOT NULL,
                    child_id VARCHAR(32) NOT NULL,
                    link_type VARCHAR(24) NOT NULL DEFAULT 'blocks',
                    created_at DATETIME NOT NULL,
                    FOREIGN KEY(parent_id) REFERENCES tasks (id) ON DELETE CASCADE,
                    FOREIGN KEY(child_id) REFERENCES tasks (id) ON DELETE CASCADE
                )
                """
            )
        )
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_task_links_parent_id ON task_links (parent_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_task_links_child_id ON task_links (child_id)"))
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS task_handoffs (
                    id VARCHAR(32) NOT NULL PRIMARY KEY,
                    task_id VARCHAR(32) NOT NULL,
                    author VARCHAR(120) NOT NULL,
                    summary TEXT NOT NULL,
                    changed_files TEXT NOT NULL DEFAULT '',
                    commands_run TEXT NOT NULL DEFAULT '',
                    tests_run TEXT NOT NULL DEFAULT '',
                    artifacts TEXT NOT NULL DEFAULT '',
                    attempted_but_failed TEXT NOT NULL DEFAULT '',
                    remaining_work TEXT NOT NULL DEFAULT '',
                    outcome VARCHAR(24) NOT NULL DEFAULT 'success',
                    next_recommended_agent VARCHAR(120),
                    capabilities TEXT NOT NULL DEFAULT '',
                    created_at DATETIME NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks (id) ON DELETE CASCADE
                )
                """
            )
        )
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_task_handoffs_task_id ON task_handoffs (task_id)"))
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS workspace_configs (
                    id VARCHAR(32) NOT NULL PRIMARY KEY,
                    name VARCHAR(180) NOT NULL UNIQUE,
                    strategy VARCHAR(24) NOT NULL DEFAULT 'git_worktree',
                    base_branch VARCHAR(240) NOT NULL DEFAULT 'main',
                    branch_prefix VARCHAR(120) NOT NULL DEFAULT 'task/',
                    root_dir VARCHAR(500) NOT NULL DEFAULT '',
                    scratch_root VARCHAR(500) NOT NULL DEFAULT '/tmp/flow-scratch',
                    description TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS webhook_configs (
                    id VARCHAR(32) NOT NULL PRIMARY KEY,
                    name VARCHAR(180) NOT NULL,
                    url VARCHAR(500) NOT NULL,
                    secret VARCHAR(128) NOT NULL,
                    events TEXT NOT NULL DEFAULT '',
                    active INTEGER NOT NULL DEFAULT 1,
                    max_retries INTEGER NOT NULL DEFAULT 3,
                    retry_backoff_seconds INTEGER NOT NULL DEFAULT 60,
                    project VARCHAR(120) NOT NULL DEFAULT '*',
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS webhook_deliveries (
                    id VARCHAR(32) NOT NULL PRIMARY KEY,
                    webhook_id VARCHAR(32) NOT NULL,
                    event VARCHAR(64) NOT NULL,
                    payload TEXT NOT NULL,
                    status VARCHAR(24) NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at DATETIME,
                    last_response_code INTEGER,
                    last_response_body TEXT,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    FOREIGN KEY(webhook_id) REFERENCES webhook_configs (id)
                )
                """
            )
        )
        connection.execute(
            text("CREATE INDEX IF NOT EXISTS ix_webhook_deliveries_webhook_id ON webhook_deliveries (webhook_id)")
        )
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS notification_deliveries (
                    id VARCHAR(32) NOT NULL PRIMARY KEY,
                    provider VARCHAR(24) NOT NULL,
                    event VARCHAR(64) NOT NULL,
                    task_id VARCHAR(32) NOT NULL,
                    payload TEXT NOT NULL,
                    status VARCHAR(24) NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_retries INTEGER NOT NULL DEFAULT 3,
                    next_attempt_at DATETIME,
                    last_response_code INTEGER,
                    last_response_body TEXT,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )
        connection.execute(
            text("CREATE INDEX IF NOT EXISTS ix_notification_deliveries_task_id ON notification_deliveries (task_id)")
        )
        connection.execute(text("INSERT OR IGNORE INTO flow_counters (name, value) VALUES ('webhook', 0)"))
        connection.execute(text("INSERT OR IGNORE INTO flow_counters (name, value) VALUES ('delivery', 0)"))
        connection.execute(
            text("INSERT OR IGNORE INTO flow_counters (name, value) VALUES ('notification_delivery', 0)")
        )
        connection.execute(text("INSERT OR IGNORE INTO flow_counters (name, value) VALUES ('handoff', 0)"))

    inspector = inspect(engine)
    table_names = inspector.get_table_names()
    if "agents" in table_names:
        existing_agent_columns = {column["name"] for column in inspector.get_columns("agents")}
        if "dispatch_statuses" not in existing_agent_columns:
            with engine.begin() as connection:
                connection.execute(
                    text("ALTER TABLE agents ADD COLUMN dispatch_statuses TEXT NOT NULL DEFAULT 'backlog,todo'")
                )

    if "tasks" not in table_names:
        return

    existing = {column["name"] for column in inspector.get_columns("tasks")}
    additions = {
        "source_filename": "VARCHAR(500)",
        "source_line": "INTEGER",
        "import_batch_id": "VARCHAR(64)",
        "source_title": "VARCHAR(240)",
        "human_required": "INTEGER NOT NULL DEFAULT 0",
        "assignee_type": "VARCHAR(24) NOT NULL DEFAULT 'agent'",
        "blocker_reason": "TEXT NOT NULL DEFAULT ''",
        "complexity": "VARCHAR(24) NOT NULL DEFAULT 'small'",
        "impact": "VARCHAR(24) NOT NULL DEFAULT 'medium'",
        "effort": "VARCHAR(24) NOT NULL DEFAULT 'medium'",
        "risk": "VARCHAR(24) NOT NULL DEFAULT 'low'",
    }

    with engine.begin() as connection:
        for column_name, column_type in additions.items():
            if column_name not in existing:
                connection.execute(text(f"ALTER TABLE tasks ADD COLUMN {column_name} {column_type}"))

    if "agent_runs" in inspector.get_table_names():
        existing_agent_run_columns = {column["name"] for column in inspector.get_columns("agent_runs")}
        if "workspace_state" not in existing_agent_run_columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE agent_runs ADD COLUMN workspace_state TEXT NOT NULL DEFAULT ''"))

    if "api_keys" not in inspector.get_table_names():
        return

    existing_api_key_columns = {column["name"] for column in inspector.get_columns("api_keys")}
    if "role" not in existing_api_key_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE api_keys ADD COLUMN role VARCHAR(32) NOT NULL DEFAULT 'read_only'"))


app = create_app()
