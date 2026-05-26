from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from ..config import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT
from ..repository import (
    count_ideas,
    serialize_idea,
)
from ..schemas import (
    IdeaCreate,
    IdeaResponse,
    IdeaUpdate,
    PaginatedResponse,
    PromoteTaskSpec,
)
from ..security import Actor, Permission, require_permission
from ..services.idea import IdeaNotFoundError, IdeaService
from .dependencies import _webhook_notifier, get_db


router = APIRouter()

@router.get("/ideas", response_model=PaginatedResponse)
def api_list_ideas(
        db: Session = Depends(get_db),
        project: str | None = Query(default=None),
        archived: bool = Query(default=False),
        limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
        offset: int = Query(default=0, ge=0),
        _actor: Actor = Depends(require_permission(Permission.TASKS_READ)),
    ):
        svc = IdeaService(db)
        items = [serialize_idea(idea, db) for idea in svc.list_ideas(project=project, archived=archived, limit=limit, offset=offset)]
        total = count_ideas(db, project=project, archived=archived)
        return {"items": items, "total": total, "limit": limit, "offset": offset}

@router.post("/ideas", response_model=IdeaResponse, status_code=status.HTTP_201_CREATED)
def api_create_idea(
        payload: IdeaCreate,
        db: Session = Depends(get_db),
        actor: Actor = Depends(require_permission(Permission.TASKS_CREATE)),
    ):
        if not payload.author and actor.name:
            payload.author = actor.name
        svc = IdeaService(db)
        idea = svc.create_idea(payload)
        return serialize_idea(idea, db)

@router.get("/ideas/{idea_id}", response_model=IdeaResponse)
def api_get_idea(
        idea_id: str,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.TASKS_READ)),
    ):
        svc = IdeaService(db)
        try:
            idea = svc.get_idea(idea_id)
        except IdeaNotFoundError as exc:
            raise HTTPException(status_code=404, detail=exc.message) from exc
        return serialize_idea(idea, db)

@router.patch("/ideas/{idea_id}", response_model=IdeaResponse)
def api_update_idea(
        idea_id: str,
        payload: IdeaUpdate,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.TASKS_EDIT)),
    ):
        svc = IdeaService(db)
        try:
            idea = svc.update_idea(idea_id, payload)
        except IdeaNotFoundError as exc:
            raise HTTPException(status_code=404, detail=exc.message) from exc
        return serialize_idea(idea, db)

@router.post("/ideas/{idea_id}/archive", response_model=IdeaResponse)
def api_archive_idea(
        idea_id: str,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.TASKS_EDIT)),
    ):
        svc = IdeaService(db)
        try:
            idea = svc.archive_idea(idea_id)
        except IdeaNotFoundError as exc:
            raise HTTPException(status_code=404, detail=exc.message) from exc
        return serialize_idea(idea, db)

@router.post("/ideas/{idea_id}/unarchive", response_model=IdeaResponse)
def api_unarchive_idea(
        idea_id: str,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.TASKS_EDIT)),
    ):
        svc = IdeaService(db)
        try:
            idea = svc.unarchive_idea(idea_id)
        except IdeaNotFoundError as exc:
            raise HTTPException(status_code=404, detail=exc.message) from exc
        return serialize_idea(idea, db)

@router.post("/ideas/{idea_id}/promote", response_model=IdeaResponse)
def api_promote_idea(
        idea_id: str,
        payload: list[PromoteTaskSpec],
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.TASKS_CREATE)),
    ):
        svc = IdeaService(db)
        try:
            idea = svc.promote_idea(idea_id, payload, _webhook_notifier, db)
        except IdeaNotFoundError as exc:
            raise HTTPException(status_code=404, detail=exc.message) from exc
        return serialize_idea(idea, db)
