from __future__ import annotations

from datetime import timedelta
from hmac import compare_digest

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT
from ..dispatcher import complete_run
from ..models import Agent, AgentRun, RunnerLease, Task, utcnow
from ..repository import (
    count_runners,
    create_agent_run,
    create_runner,
    create_runner_lease,
    get_active_leases_for_runner,
    get_agent_by_name,
    get_runner,
    get_runner_lease,
    list_runners,
    runner_lease_to_json,
    serialize_runner_lease,
    serialize_runner,
    update_runner_lease,
    update_runner,
)
from ..realtime import publish_board_event
from ..schemas import (
    LeaseCompleteRequest,
    LeaseHeartbeatRequest,
    LeaseResponse,
    PaginatedResponse,
    RunnerCreate,
    RunnerResponse,
    RunnerUpdate,
)
from ..security import Actor, Permission, require_permission
from ..secrets_resolver import SecretResolutionError, resolve_secret
from ..storage_helpers import get_agent_dispatch_statuses, get_comma_list
from .dependencies import _commit, get_db


router = APIRouter()


def _handle_runner_conflict(db: Session, exc: IntegrityError) -> None:
    db.rollback()
    raise HTTPException(
        status_code=409,
        detail="Database conflict: the record already exists or violates a constraint.",
    ) from exc


def _bearer_token(request: Request) -> str:
    value = request.headers.get("authorization", "")
    scheme, _, token = value.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=403, detail="Runner authentication failed.")
    return token.strip()


def _require_runner_token(runner, request: Request) -> None:
    if not runner.api_key_ref:
        raise HTTPException(status_code=403, detail="Runner API key is not configured.")
    try:
        expected = resolve_secret(runner.api_key_ref)
    except SecretResolutionError as exc:
        raise HTTPException(status_code=403, detail="Runner API key could not be resolved.") from exc
    if not expected or not compare_digest(_bearer_token(request), expected):
        raise HTTPException(status_code=403, detail="Runner authentication failed.")


def _require_polling_runner(db: Session, runner_id: str, request: Request):
    runner = get_runner(db, runner_id)
    if runner is None:
        raise HTTPException(status_code=404, detail="Runner not found.")
    _require_runner_token(runner, request)
    if not runner.enabled:
        raise HTTPException(status_code=403, detail="Runner is disabled.")
    return runner


def _active_lease_response(db: Session, lease):
    run = db.get(AgentRun, lease.agent_run_id)
    task = db.get(Task, run.task_id) if run is not None else None
    agent = db.get(Agent, run.agent_id) if run is not None else None
    return runner_lease_to_json(lease, task, agent)


def _eligible_task_for_runner(db: Session, runner):
    for agent_name in get_comma_list(runner.agent_names):
        agent = get_agent_by_name(db, agent_name)
        if agent is None or not agent.enabled:
            continue
        statuses = get_agent_dispatch_statuses(agent) if agent.dispatch_statuses else ["backlog", "todo"]
        stmt = (
            select(Task)
            .where(
                Task.status.in_(statuses),
                Task.human_required == 0,
                (Task.assignee.is_(None)) | (Task.assignee == "") | (Task.assignee == agent.name),
            )
            .order_by(Task.priority.desc(), Task.created_at, Task.id)
        )
        task = db.scalars(stmt).first()
        if task is not None:
            return agent, task
    return None, None


def _pending_remote_run_for_runner(db: Session, runner):
    agent_names = get_comma_list(runner.agent_names)
    if not agent_names:
        return None, None, None
    stmt = (
        select(AgentRun, Agent, Task)
        .join(Agent, Agent.id == AgentRun.agent_id)
        .join(Task, Task.id == AgentRun.task_id)
        .outerjoin(
            RunnerLease,
            (RunnerLease.agent_run_id == AgentRun.id) & (RunnerLease.status == "active"),
        )
        .where(
            AgentRun.status == "running",
            Agent.agent_type == "remote",
            Agent.enabled == 1,
            Agent.name.in_(agent_names),
            Task.status == "doing",
            Task.assignee == Agent.name,
            RunnerLease.id.is_(None),
        )
        .order_by(AgentRun.started_at, AgentRun.created_at, AgentRun.id)
    )
    row = db.execute(stmt).first()
    if row is None:
        return None, None, None
    return row


@router.get("/runners", response_model=PaginatedResponse)
def api_list_runners(
    db: Session = Depends(get_db),
    enabled_only: bool = Query(default=False),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(default=0, ge=0),
    _actor: Actor = Depends(require_permission(Permission.RUNNER_READ)),
):
    items = [
        serialize_runner(runner)
        for runner in list_runners(
            db,
            enabled_only=enabled_only,
            status=status_filter,
            limit=limit,
            offset=offset,
        )
    ]
    total = count_runners(db, enabled_only=enabled_only, status=status_filter)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/runners/{runner_id}", response_model=RunnerResponse)
def api_get_runner(
    runner_id: str,
    db: Session = Depends(get_db),
    _actor: Actor = Depends(require_permission(Permission.RUNNER_READ)),
):
    runner = get_runner(db, runner_id)
    if runner is None:
        raise HTTPException(status_code=404, detail="Runner not found.")
    return serialize_runner(runner)


@router.post("/runners", response_model=RunnerResponse, status_code=status.HTTP_201_CREATED)
def api_create_runner(
    payload: RunnerCreate,
    db: Session = Depends(get_db),
    _actor: Actor = Depends(require_permission(Permission.RUNNER_MANAGE)),
):
    try:
        runner, _secret = create_runner(db, payload)
    except IntegrityError as exc:
        _handle_runner_conflict(db, exc)
    _commit(db)
    return serialize_runner(runner)


@router.post("/runners/{runner_id}/poll", response_model=LeaseResponse | list[LeaseResponse] | None)
def api_poll_runner(
    runner_id: str,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    runner = _require_polling_runner(db, runner_id, request)
    now = utcnow()
    runner.last_seen_at = now
    if runner.status == "offline":
        runner.status = "online"
    runner.updated_at = now
    db.add(runner)
    db.flush()

    active_leases = get_active_leases_for_runner(db, runner.id)
    if len(active_leases) >= runner.max_concurrent_leases:
        _commit(db)
        return [_active_lease_response(db, lease) for lease in active_leases]

    run, agent, task = _pending_remote_run_for_runner(db, runner)
    if run is not None and agent is not None and task is not None:
        lease = create_runner_lease(db, runner.id, run.id, now + timedelta(seconds=runner.lease_duration_seconds))
        publish_board_event("task_claimed", task, run_id=run.id, runner_id=runner.id, lease_id=lease.id)
        _commit(db)
        return serialize_runner_lease(lease, task, agent)

    agent, task = _eligible_task_for_runner(db, runner)
    if agent is None or task is None:
        _commit(db)
        response.status_code = status.HTTP_204_NO_CONTENT
        return None

    task.assignee = agent.name
    task.status = "doing"
    task.updated_at = now
    db.add(task)
    run = create_agent_run(db, agent_id=agent.id, task_id=task.id, status="running")
    run.started_at = now
    run.last_heartbeat_at = now
    run.updated_at = now
    db.add(run)
    lease = create_runner_lease(db, runner.id, run.id, now + timedelta(seconds=runner.lease_duration_seconds))
    publish_board_event("task_claimed", task, run_id=run.id, runner_id=runner.id, lease_id=lease.id)
    _commit(db)
    return serialize_runner_lease(lease, task, agent)


@router.post("/runners/{runner_id}/leases/{lease_id}/heartbeat", response_model=LeaseResponse)
def api_heartbeat_runner_lease(
    runner_id: str,
    lease_id: str,
    payload: LeaseHeartbeatRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    runner = _require_polling_runner(db, runner_id, request)
    lease = get_runner_lease(db, lease_id)
    if lease is None:
        raise HTTPException(status_code=404, detail="Runner lease not found.")
    if lease.runner_id != runner.id:
        raise HTTPException(status_code=403, detail="Runner lease belongs to a different runner.")
    if lease.status != "active":
        raise HTTPException(status_code=409, detail="Runner lease is not active.")
    now = utcnow()
    expires_at = lease.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=now.tzinfo)
    if expires_at < now:
        raise HTTPException(status_code=410, detail="Runner lease has expired.")

    updates = {"last_heartbeat_at": now}
    if payload.runner_pid is not None:
        updates["runner_pid"] = payload.runner_pid
    if "message" in payload.model_fields_set:
        updates["runner_message"] = payload.message
    lease = update_runner_lease(db, lease, updates)
    run = db.get(AgentRun, lease.agent_run_id)
    if run is not None:
        run.last_heartbeat_at = now
        run.updated_at = now
        db.add(run)
    _commit(db)
    return serialize_runner_lease(lease)


@router.post("/runners/{runner_id}/leases/{lease_id}/complete", response_model=LeaseResponse)
def api_complete_runner_lease(
    runner_id: str,
    lease_id: str,
    payload: LeaseCompleteRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    runner = _require_polling_runner(db, runner_id, request)
    lease = get_runner_lease(db, lease_id)
    if lease is None:
        raise HTTPException(status_code=404, detail="Runner lease not found.")
    if lease.runner_id != runner.id:
        raise HTTPException(status_code=403, detail="Runner lease belongs to a different runner.")
    if lease.status != "active":
        raise HTTPException(status_code=409, detail="Runner lease is not active.")

    run = db.get(AgentRun, lease.agent_run_id)
    task = db.get(Task, run.task_id) if run is not None else None
    agent = db.get(Agent, run.agent_id) if run is not None else None
    now = utcnow()
    lease = update_runner_lease(
        db,
        lease,
        {"status": "completed", "completed_at": now, "runner_message": payload.message},
    )
    if run is not None:
        complete_run(db, run, payload.exit_code)
    _commit(db)
    return serialize_runner_lease(lease, task, agent)


@router.patch("/runners/{runner_id}", response_model=RunnerResponse)
def api_update_runner(
    runner_id: str,
    payload: RunnerUpdate,
    db: Session = Depends(get_db),
    _actor: Actor = Depends(require_permission(Permission.RUNNER_MANAGE)),
):
    runner = get_runner(db, runner_id)
    if runner is None:
        raise HTTPException(status_code=404, detail="Runner not found.")
    try:
        runner = update_runner(db, runner, payload)
    except IntegrityError as exc:
        _handle_runner_conflict(db, exc)
    _commit(db)
    return serialize_runner(runner)


@router.delete("/runners/{runner_id}", response_model=RunnerResponse)
def api_delete_runner(
    runner_id: str,
    db: Session = Depends(get_db),
    _actor: Actor = Depends(require_permission(Permission.RUNNER_MANAGE)),
):
    runner = get_runner(db, runner_id)
    if runner is None:
        raise HTTPException(status_code=404, detail="Runner not found.")
    try:
        runner = update_runner(db, runner, RunnerUpdate(enabled=False, status="offline"))
    except IntegrityError as exc:
        _handle_runner_conflict(db, exc)
    _commit(db)
    return serialize_runner(runner)
