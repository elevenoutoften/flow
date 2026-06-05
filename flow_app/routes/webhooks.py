from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..audit import actor_id_for, audit
from ..config import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT
from ..models import utcnow
from ..ratelimit import require_mutation_limit
from ..repository import (
    count_webhook_deliveries,
    serialize_webhook_config,
    serialize_webhook_delivery,
    update_webhook_delivery,
)
from ..schemas import (
    PaginatedResponse,
    WebhookConfigCreate,
    WebhookConfigCreateResponse,
    WebhookConfigResponse,
    WebhookConfigUpdate,
    WebhookDeliveryDetailResponse,
    WebhookDeliveryRetryResponse,
)
from ..security import Actor, Permission, require_permission
from ..services.webhook import WebhookError, WebhookNotFoundError, WebhookService
from ..webhooks import deliver_webhook
from .dependencies import _commit, _require_webhook_delivery, get_db


router = APIRouter()

@router.post(
        "/webhooks",
        response_model=WebhookConfigCreateResponse,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(require_mutation_limit)],
    )
def api_create_webhook(
        payload: WebhookConfigCreate,
        db: Session = Depends(get_db),
        actor: Actor = Depends(require_permission(Permission.WEBHOOK_MANAGE)),
    ):
        svc = WebhookService(db)
        try:
            config, raw_secret = svc.create_config(payload)
        except WebhookError as exc:
            status_code = 422 if exc.error_type == "invalid_event" else 400
            raise HTTPException(status_code=status_code, detail=exc.message) from exc
        audit(db, actor_id_for(actor), "webhook.create", "webhook", config.id)
        _commit(db)
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

@router.patch("/webhooks/{webhook_id}", response_model=WebhookConfigResponse, dependencies=[Depends(require_mutation_limit)])
def api_update_webhook(
        webhook_id: str,
        payload: WebhookConfigUpdate,
        db: Session = Depends(get_db),
        actor: Actor = Depends(require_permission(Permission.WEBHOOK_MANAGE)),
    ):
        svc = WebhookService(db)
        try:
            config = svc.update_config(webhook_id, payload)
        except WebhookNotFoundError as exc:
            raise HTTPException(status_code=404, detail=exc.message) from exc
        except WebhookError as exc:
            status_code = 422 if exc.error_type == "invalid_event" else 400
            raise HTTPException(status_code=status_code, detail=exc.message) from exc
        audit(db, actor_id_for(actor), "webhook.update", "webhook", config.id, {"name": config.name})
        _commit(db)
        return serialize_webhook_config(config)

@router.delete(
        "/webhooks/{webhook_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(require_mutation_limit)],
    )
def api_delete_webhook(
        webhook_id: str,
        db: Session = Depends(get_db),
        actor: Actor = Depends(require_permission(Permission.WEBHOOK_MANAGE)),
    ):
        svc = WebhookService(db)
        try:
            config = svc.get_config(webhook_id)
            svc.delete_config(webhook_id)
        except WebhookNotFoundError as exc:
            raise HTTPException(status_code=404, detail=exc.message) from exc
        audit(db, actor_id_for(actor), "webhook.delete", "webhook", webhook_id, {"name": config.name})
        _commit(db)
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
        dependencies=[Depends(require_mutation_limit)],
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
