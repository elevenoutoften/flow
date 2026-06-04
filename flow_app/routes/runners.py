from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT
from ..repository import (
    count_runners,
    create_runner,
    get_runner,
    list_runners,
    serialize_runner,
    update_runner,
)
from ..schemas import PaginatedResponse, RunnerCreate, RunnerResponse, RunnerUpdate
from ..security import Actor, Permission, require_permission
from .dependencies import _commit, get_db


router = APIRouter()


def _handle_runner_conflict(db: Session, exc: IntegrityError) -> None:
    db.rollback()
    raise HTTPException(
        status_code=409,
        detail="Database conflict: the record already exists or violates a constraint.",
    ) from exc


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
