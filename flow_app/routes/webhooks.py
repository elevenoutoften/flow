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

@router.post("/webhooks", response_model=WebhookConfigCreateResponse, status_code=status.HTTP_201_CREATED)
def api_create_webhook(
        payload: WebhookConfigCreate,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.WEBHOOK_MANAGE)),
    ):
        svc = WebhookService(db)
        try:
            config, raw_secret = svc.create_config(payload)
        except WebhookError as exc:
            status_code = 422 if exc.error_type == "invalid_event" else 400
            raise HTTPException(status_code=status_code, detail=exc.message) from exc
        data = serialize_webhook_config(config).model_dump()
        return WebhookConfigCreateResponse(**data, secret=raw_secret)

@router.get("/webhooks", response_model=list[WebhookConfigResponse])
def api_list_webhooks(
        db: Session = Depends(get_db),
        project: str | None = Query(default=None),
        _actor: Actor = Depends(require_permission(Permission.WEBHOOK_READ)),
    ):
        svc = WebhookService(db)
        return [serialize_webhook_config(config) for config in svc.list_configs(project=project)]

@router.get("/webhooks/{webhook_id}", response_model=WebhookConfigResponse)
def api_get_webhook(
        webhook_id: str,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.WEBHOOK_READ)),
    ):
        svc = WebhookService(db)
        try:
            config = svc.get_config(webhook_id)
        except WebhookNotFoundError as exc:
            raise HTTPException(status_code=404, detail=exc.message) from exc
        return serialize_webhook_config(config)

@router.patch("/webhooks/{webhook_id}", response_model=WebhookConfigResponse)
def api_update_webhook(
        webhook_id: str,
        payload: WebhookConfigUpdate,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.WEBHOOK_MANAGE)),
    ):
        svc = WebhookService(db)
        try:
            config = svc.update_config(webhook_id, payload)
        except WebhookNotFoundError as exc:
            raise HTTPException(status_code=404, detail=exc.message) from exc
        except WebhookError as exc:
            status_code = 422 if exc.error_type == "invalid_event" else 400
            raise HTTPException(status_code=status_code, detail=exc.message) from exc
        return serialize_webhook_config(config)

@router.delete("/webhooks/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
def api_delete_webhook(
        webhook_id: str,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.WEBHOOK_MANAGE)),
    ):
        svc = WebhookService(db)
        try:
            svc.delete_config(webhook_id)
        except WebhookNotFoundError as exc:
            raise HTTPException(status_code=404, detail=exc.message) from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)

@router.get("/webhooks/{webhook_id}/deliveries", response_model=PaginatedResponse)
def api_list_webhook_deliveries(
        webhook_id: str,
        db: Session = Depends(get_db),
        status_filter: str | None = Query(default=None, alias="status"),
        limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
        offset: int = Query(default=0, ge=0),
        _actor: Actor = Depends(require_permission(Permission.WEBHOOK_READ)),
    ):
        if status_filter and status_filter not in {"pending", "success", "failed", "retrying"}:
            raise HTTPException(status_code=422, detail="Invalid delivery status.")
        svc = WebhookService(db)
        try:
            deliveries = svc.list_deliveries(webhook_id, status=status_filter, limit=limit, offset=offset)
        except WebhookNotFoundError as exc:
            raise HTTPException(status_code=404, detail=exc.message) from exc
        items = [serialize_webhook_delivery(delivery) for delivery in deliveries]
        total = count_webhook_deliveries(db, webhook_id, status=status_filter)
        return {"items": items, "total": total, "limit": limit, "offset": offset}

@router.get("/webhooks/{webhook_id}/deliveries/{delivery_id}", response_model=WebhookDeliveryDetailResponse)
def api_get_webhook_delivery(
        webhook_id: str,
        delivery_id: str,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.WEBHOOK_READ)),
    ):
        delivery = _require_webhook_delivery(db, webhook_id, delivery_id)
        data = serialize_webhook_delivery(delivery).model_dump()
        return WebhookDeliveryDetailResponse(**data, payload=delivery.payload)

@router.post(
        "/webhooks/{webhook_id}/deliveries/{delivery_id}/retry",
        response_model=WebhookDeliveryRetryResponse,
    )
def api_retry_webhook_delivery(
        webhook_id: str,
        delivery_id: str,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.WEBHOOK_READ)),
    ):
        svc = WebhookService(db)
        try:
            config = svc.get_config(webhook_id)
        except WebhookNotFoundError as exc:
            raise HTTPException(status_code=404, detail=exc.message) from exc
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

