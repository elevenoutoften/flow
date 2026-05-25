from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import replace
import json
from pathlib import Path
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT, FLOW_VERSION, FlowSettings, get_settings
from ..database import Base, build_engine, build_session_factory, default_database_url
from ..dispatcher import DispatchError, set_session_factory
from ..markdown_import import parse_markdown_tasks
from ..mcp import JsonRpcError, error_response, exception_response, handle_mcp_message
from ..models import Task, utcnow
from ..repository import (
    add_note,
    archive_idea,
    auto_promote_unblocked_children,
    batch_dependency_summaries,
    count_agent_runs,
    count_automation_rules,
    count_ideas,
    count_tasks,
    count_webhook_deliveries,
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
    serialize_task_list,
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
from ..notifications import WebhookNotificationProvider
from ..rules_engine import emit_event as emit_rule_event
from ..schemas import (
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
    PaginatedResponse,
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
    TaskListResponse,
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
from ..security import (
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE,
    Actor,
    Permission,
    PermissionDenied,
    authorize_task_update,
    can_note_task,
    has_permission,
    is_valid_transition,
    require_permission,
    resolve_actor,
    sign_session,
)
from ..services.task import (
    InvalidTransitionError,
    MissingAssigneeError,
    NotePermissionError,
    SendbackContractError,
    TaskAlreadyClaimedError,
    TaskClaimKeyConflictError,
    TaskConcurrentModificationError,
    TaskNotFoundError,
    TaskService,
)
from ..services.agent import AgentError, AgentNotFoundError, AgentRunNotFoundError, AgentService
from ..services.automation import AutomationRuleNotFoundError, AutomationService
from ..services.idea import IdeaNotFoundError, IdeaService
from ..services.webhook import WebhookError, WebhookNotFoundError, WebhookService
from ..services.workspace import WorkspaceConfigNotFoundError, WorkspaceService
from ..webhooks import WEBHOOK_EVENTS, deliver_webhook
from ..telegram import TelegramNotificationProvider
from .dependencies import get_db, _commit, _make_task_service, _require_task, _require_webhook_delivery, _webhook_notifier


router = APIRouter()

@router.get("/api-keys", response_model=list[AgentApiKeyResponse])
def api_list_agent_api_keys(
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.KEY_MANAGE)),
    ):
        return [serialize_agent_api_key(api_key) for api_key in list_agent_api_keys(db)]

@router.post("/api-keys", response_model=AgentApiKeyCreateResponse, status_code=status.HTTP_201_CREATED)
def api_create_agent_api_key(
        payload: AgentApiKeyCreate,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.KEY_MANAGE)),
    ):
        api_key, raw_key = create_agent_api_key(db, payload)
        _commit(db)
        return serialize_created_agent_api_key(api_key, raw_key)

@router.post("/api-keys/{api_key_id}/revoke", response_model=AgentApiKeyResponse)
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

@router.get("/agents", response_model=list[AgentResponse])
def api_list_agents(
        db: Session = Depends(get_db),
        enabled_only: bool = Query(default=False),
        _actor: Actor = Depends(require_permission(Permission.AGENT_READ)),
    ):
        svc = AgentService(db)
        return [serialize_agent(agent) for agent in svc.list_agents(enabled_only=enabled_only)]

@router.get("/agents/{agent_id}", response_model=AgentResponse)
def api_get_agent(
        agent_id: str,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.AGENT_READ)),
    ):
        svc = AgentService(db)
        try:
            agent = svc.get_agent(agent_id)
        except AgentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=exc.message) from exc
        return serialize_agent(agent)

@router.post("/agents", response_model=AgentResponse, status_code=status.HTTP_201_CREATED)
def api_create_agent(
        payload: AgentCreate,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.AGENT_MANAGE)),
    ):
        svc = AgentService(db)
        agent = svc.create_agent(payload)
        return serialize_agent(agent)

@router.patch("/agents/{agent_id}", response_model=AgentResponse)
def api_update_agent(
        agent_id: str,
        payload: AgentUpdate,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.AGENT_MANAGE)),
    ):
        svc = AgentService(db)
        try:
            agent = svc.update_agent(agent_id, payload)
        except AgentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=exc.message) from exc
        return serialize_agent(agent)

@router.get("/agent-runs", response_model=PaginatedResponse)
def api_list_agent_runs(
        db: Session = Depends(get_db),
        agent_id: str | None = Query(default=None),
        task_id: str | None = Query(default=None),
        status_filter: str | None = Query(default=None, alias="status"),
        limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
        offset: int = Query(default=0, ge=0),
        _actor: Actor = Depends(require_permission(Permission.TASKS_READ)),
    ):
        svc = AgentService(db)
        items = [
            serialize_agent_run(run)
            for run in svc.list_runs(agent_id=agent_id, task_id=task_id, status=status_filter, limit=limit, offset=offset)
        ]
        total = count_agent_runs(db, agent_id=agent_id, task_id=task_id, status=status_filter)
        return {"items": items, "total": total, "limit": limit, "offset": offset}

@router.get("/agent-runs/{run_id}", response_model=AgentRunResponse)
def api_get_agent_run(
        run_id: str,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.TASKS_READ)),
    ):
        svc = AgentService(db)
        try:
            run = svc.get_run(run_id)
        except AgentRunNotFoundError as exc:
            raise HTTPException(status_code=404, detail=exc.message) from exc
        return serialize_agent_run(run)

@router.post("/agents/{agent_id}/dispatch", response_model=AgentRunResponse)
def api_dispatch_agent(
        agent_id: str,
        request: Request,
        db: Session = Depends(get_db),
        task_id: str | None = Query(default=None),
        actor: Actor = Depends(require_permission(Permission.DISPATCH)),
    ):
        svc = AgentService(db)
        try:
            run = svc.dispatch(
                agent_id,
                task_id,
                base_url=str(request.base_url).rstrip("/"),
                api_key_value=request.headers.get("authorization", "").removeprefix("Bearer ").strip(),
            )
        except AgentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=exc.message) from exc
        except AgentError as exc:
            status_code = 404 if exc.error_type == "not_found" else 400
            raise HTTPException(status_code=status_code, detail=exc.message) from exc
        except DispatchError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return serialize_agent_run(run)

@router.post("/agent-runs/{run_id}/heartbeat", response_model=AgentRunResponse)
def api_heartbeat_agent_run(
        run_id: str,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.DISPATCH)),
    ):
        svc = AgentService(db)
        try:
            run = svc.heartbeat(run_id)
        except AgentRunNotFoundError as exc:
            raise HTTPException(status_code=404, detail=exc.message) from exc
        return serialize_agent_run(run)

@router.post("/agent-runs/{run_id}/complete", response_model=AgentRunResponse)
def api_complete_agent_run(
        run_id: str,
        db: Session = Depends(get_db),
        exit_code: int = Query(...),
        _actor: Actor = Depends(require_permission(Permission.DISPATCH)),
    ):
        svc = AgentService(db)
        try:
            run = svc.complete_run(run_id, exit_code)
        except AgentRunNotFoundError as exc:
            raise HTTPException(status_code=404, detail=exc.message) from exc
        return serialize_agent_run(run)

@router.post("/agent-runs/stale-recovery")
def api_stale_recovery(
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.TASKS_EDIT)),
    ):
        svc = AgentService(db)
        recovered = svc.stale_recovery()
        return {"recovered_run_ids": recovered, "count": len(recovered)}
