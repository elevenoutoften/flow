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

@router.post("/import/markdown/preview", response_model=MarkdownImportPreviewResponse)
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

@router.post("/import/markdown/commit", response_model=MarkdownImportCommitResponse)
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

@router.get("/tasks/next", response_model=TaskResponse)
def api_next_task(
        db: Session = Depends(get_db),
        project: str | None = Query(default=None),
        _actor: Actor = Depends(require_permission(Permission.TASKS_READ)),
    ):
        task = next_task(db, project=project)
        if task is None:
            raise HTTPException(status_code=404, detail="No unclaimed task is available.")
        return serialize_task(task)

@router.get("/tasks", response_model=PaginatedResponse)
def api_list_tasks(
        db: Session = Depends(get_db),
        project: str | None = Query(default=None),
        status_filter: str | None = Query(default=None, alias="status"),
        assignee: str | None = Query(default=None),
        unclaimed: bool = Query(default=False),
        limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
        offset: int = Query(default=0, ge=0),
        _actor: Actor = Depends(require_permission(Permission.TASKS_READ)),
    ):
        if status_filter and status_filter not in STATUSES:
            raise HTTPException(status_code=422, detail="Invalid task status.")
        items = [
            serialize_task_list(task)
            for task in _make_task_service(db).list_tasks(
                project=project,
                status=status_filter,
                assignee=assignee,
                unclaimed=unclaimed,
                limit=limit,
                offset=offset,
            )
        ]
        total = count_tasks(db, project=project, status=status_filter, assignee=assignee, unclaimed=unclaimed)
        return {"items": items, "total": total, "limit": limit, "offset": offset}

@router.post("/tasks", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
def api_create_task(
        payload: TaskCreate,
        db: Session = Depends(get_db),
        actor: Actor = Depends(require_permission(Permission.TASKS_CREATE)),
    ):
        task = _make_task_service(db).create_task(payload, actor)
        return serialize_task(task)

@router.get("/tasks/{task_id}", response_model=TaskResponse)
def api_get_task(
        task_id: str,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.TASKS_READ)),
    ):
        return serialize_task(_require_task(db, task_id))

@router.get("/tasks/{task_id}/handoffs", response_model=list[HandoffResponse])
def api_list_task_handoffs(
        task_id: str,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.TASKS_READ)),
    ):
        _require_task(db, task_id)
        return [serialize_task_handoff(handoff) for handoff in list_task_handoffs(db, task_id)]

@router.get("/tasks/{task_id}/handoffs/{handoff_id}", response_model=HandoffResponse)
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

@router.post("/tasks/{task_id}/handoffs", response_model=HandoffResponse, status_code=status.HTTP_201_CREATED)
@router.post("/tasks/{task_id}/handoff", response_model=HandoffResponse, status_code=status.HTTP_201_CREATED)
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
            author_key_id=actor.key_id,
        )
        _commit(db)
        return serialize_task_handoff(handoff)

@router.get("/tasks/{task_id}/links", response_model=list[TaskLinkResponse])
def api_list_task_links(
        task_id: str,
        db: Session = Depends(get_db),
        link_type: str | None = Query(default=None),
        _actor: Actor = Depends(require_permission(Permission.LINKS_READ)),
    ):
        _require_task(db, task_id)
        return [serialize_task_link(link) for link in list_task_links(db, task_id=task_id, link_type=link_type)]

@router.get("/tasks/{task_id}/dependencies", response_model=DependencySummary)
def api_get_dependencies(
        task_id: str,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.LINKS_READ)),
    ):
        try:
            return get_dependency_summary(db, task_id)
        except ValueError:
            raise HTTPException(status_code=404, detail="Task not found.")

@router.post("/tasks/{task_id}/link", response_model=TaskLinkResponse, status_code=status.HTTP_201_CREATED)
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

@router.delete("/tasks/{task_id}/link/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
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

@router.patch("/tasks/{task_id}", response_model=TaskResponse)
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
        try:
            task = _make_task_service(db).update_task(task_id, payload, actor)
        except TaskConcurrentModificationError as exc:
            raise HTTPException(status_code=409, detail=exc.message)
        return serialize_task(task)

@router.post("/tasks/{task_id}/claim", response_model=TaskResponse)
def api_claim_task(
        task_id: str,
        payload: ClaimRequest,
        db: Session = Depends(get_db),
        actor: Actor = Depends(require_permission(Permission.TASKS_CLAIM)),
    ):
        try:
            task = _make_task_service(db).claim_task(task_id, actor, agent_name=payload.agent_name)
        except TaskNotFoundError:
            raise HTTPException(status_code=404, detail="Task not found.")
        except (TaskAlreadyClaimedError, TaskClaimKeyConflictError) as exc:
            raise HTTPException(status_code=409, detail=exc.message)
        except TaskConcurrentModificationError as exc:
            raise HTTPException(status_code=409, detail=exc.message)
        except InvalidTransitionError as exc:
            raise HTTPException(status_code=403, detail=exc.message)
        except MissingAssigneeError as exc:
            raise HTTPException(status_code=400, detail=exc.message)
        return serialize_task(task)

@router.post("/tasks/{task_id}/release", response_model=TaskResponse)
def api_release_task(
        task_id: str,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.TASKS_CLAIM)),
    ):
        try:
            task = _make_task_service(db).release_task(task_id)
        except TaskNotFoundError:
            raise HTTPException(status_code=404, detail="Task not found.")
        except TaskConcurrentModificationError as exc:
            raise HTTPException(status_code=409, detail=exc.message)
        return serialize_task(task)

@router.post("/tasks/{task_id}/move", response_model=TaskResponse)
def api_move_task(
        task_id: str,
        payload: MoveRequest,
        db: Session = Depends(get_db),
        actor: Actor = Depends(require_permission(Permission.TASKS_MOVE)),
    ):
        try:
            task = _make_task_service(db).move_task(task_id, payload.status, actor)
        except TaskNotFoundError:
            raise HTTPException(status_code=404, detail="Task not found.")
        except InvalidTransitionError as exc:
            raise HTTPException(status_code=403, detail=exc.message)
        except SendbackContractError as exc:
            raise HTTPException(status_code=409, detail=exc.message)
        except TaskConcurrentModificationError as exc:
            raise HTTPException(status_code=409, detail=exc.message)
        return serialize_task(task)

@router.post("/tasks/{task_id}/note", response_model=TaskResponse)
def api_add_note(
        task_id: str,
        payload: NoteRequest,
        db: Session = Depends(get_db),
        actor: Actor = Depends(require_permission(Permission.TASKS_NOTE)),
    ):
        try:
            task = _make_task_service(db).add_note(task_id, payload.note, actor, author=payload.author)
        except TaskNotFoundError:
            raise HTTPException(status_code=404, detail="Task not found.")
        except NotePermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=exc.message)
        return serialize_task(task)

@router.post("/tasks/{task_id}/done", response_model=TaskResponse)
def api_done_task(
        task_id: str,
        payload: DoneRequest,
        db: Session = Depends(get_db),
        actor: Actor = Depends(require_permission(Permission.TASKS_DONE)),
    ):
        try:
            task = _make_task_service(db).done_task(
                task_id,
                actor,
                payload.summary,
                author=payload.author,
                handoff=payload.handoff,
            )
        except TaskNotFoundError:
            raise HTTPException(status_code=404, detail="Task not found.")
        except InvalidTransitionError as exc:
            raise HTTPException(status_code=403, detail=exc.message)
        except TaskConcurrentModificationError as exc:
            raise HTTPException(status_code=409, detail=exc.message)
        return serialize_task(task)
