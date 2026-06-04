from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..config import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT
from ..metrics import metrics
from ..repository import count_audit_logs, list_audit_logs, serialize_audit_log
from ..schemas import PaginatedResponse
from ..security import Actor, Permission, require_permission
from .dependencies import get_db

router = APIRouter()


@router.get("/audit-log", response_model=PaginatedResponse)
def api_list_audit_log(
    db: Session = Depends(get_db),
    actor_id: str | None = Query(default=None),
    action: str | None = Query(default=None),
    target_type: str | None = Query(default=None),
    limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(default=0, ge=0),
    _actor: Actor = Depends(require_permission(Permission.AUDIT_READ)),
):
    items = [
        serialize_audit_log(entry)
        for entry in list_audit_logs(
            db,
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            limit=limit,
            offset=offset,
        )
    ]
    total = count_audit_logs(db, actor_id=actor_id, action=action, target_type=target_type)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/metrics")
def api_metrics(_actor: Actor = Depends(require_permission(Permission.AUDIT_READ))):
    return metrics.snapshot()
