from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from ..config import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT
from ..schemas import PaginatedResponse, RecurringTaskTemplateCreate, RecurringTaskTemplateResponse, RecurringTaskTemplateUpdate
from ..security import Actor, Permission, require_permission
from ..services.recurring_task_template import RecurringTemplateNotFoundError, RecurringTemplateService
from .dependencies import get_db


router = APIRouter()


@router.get("/recurring-task-templates", response_model=PaginatedResponse)
def api_list_templates(
    project: str | None = Query(default=None),
    enabled: bool | None = Query(default=None),
    limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _actor: Actor = Depends(require_permission(Permission.RECURRING_TEMPLATES_READ)),
):
    svc = RecurringTemplateService(db)
    return svc.list(project=project, enabled_only=enabled is True, limit=limit, offset=offset)


@router.post(
    "/recurring-task-templates",
    response_model=RecurringTaskTemplateResponse,
    status_code=status.HTTP_201_CREATED,
)
def api_create_template(
    payload: RecurringTaskTemplateCreate,
    db: Session = Depends(get_db),
    _actor: Actor = Depends(require_permission(Permission.RECURRING_TEMPLATES_MANAGE)),
):
    svc = RecurringTemplateService(db)
    return svc.create(payload)


@router.post("/recurring-task-templates/materialize", response_model=dict)
def api_materialize_templates(
    dry_run: bool = Query(default=False),
    db: Session = Depends(get_db),
    _actor: Actor = Depends(require_permission(Permission.RECURRING_TEMPLATES_MANAGE)),
):
    """Materialize due recurring task templates. Use dry_run=true to preview without creating tasks."""
    from ..scheduler import materialize_due_templates

    result = materialize_due_templates(db, dry_run=dry_run)
    details = []
    for detail in result.details:
        detail_dict = {
            "template_id": detail.template_id,
            "task_id": detail.task_id,
            "skipped": detail.skipped,
            "skip_reason": detail.skip_reason,
        }
        if detail.task_preview:
            detail_dict["task_preview"] = detail.task_preview
        details.append(detail_dict)
    return {
        "materialized": result.materialized,
        "skipped": result.skipped,
        "dry_run": result.dry_run,
        "details": details,
    }


@router.get("/recurring-task-templates/{template_id}", response_model=RecurringTaskTemplateResponse)
def api_get_template(
    template_id: str,
    db: Session = Depends(get_db),
    _actor: Actor = Depends(require_permission(Permission.RECURRING_TEMPLATES_READ)),
):
    svc = RecurringTemplateService(db)
    try:
        return svc.get(template_id)
    except RecurringTemplateNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc


@router.patch("/recurring-task-templates/{template_id}", response_model=RecurringTaskTemplateResponse)
def api_update_template(
    template_id: str,
    payload: RecurringTaskTemplateUpdate,
    db: Session = Depends(get_db),
    _actor: Actor = Depends(require_permission(Permission.RECURRING_TEMPLATES_MANAGE)),
):
    svc = RecurringTemplateService(db)
    try:
        return svc.update(template_id, payload)
    except RecurringTemplateNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
