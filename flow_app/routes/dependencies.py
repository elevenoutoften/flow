from __future__ import annotations

import logging

from fastapi import HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Task
from ..discord import DiscordNotificationProvider
from ..notifications import RulesNotifyProvider, WebhookNotificationProvider, register_notification_provider
from ..repository import require_task
from ..rules_engine import emit_event as emit_rule_event
from ..rules_engine import set_notify_provider
from ..services.task import TaskService
from ..services.webhook import WebhookNotFoundError, WebhookService
from ..telegram import TelegramNotificationProvider

_LOGGER = logging.getLogger(__name__)
_webhook_notifier = WebhookNotificationProvider()
_telegram_notifier = TelegramNotificationProvider()
_discord_notifier = DiscordNotificationProvider()
_notify_provider = RulesNotifyProvider(telegram_provider=_telegram_notifier)
register_notification_provider("discord", _discord_notifier)
set_notify_provider(_notify_provider)


def get_db(request: Request):
    db = request.app.state.SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _make_task_service(db: Session) -> TaskService:
    return TaskService(
        db=db,
        commit_fn=_commit,
        webhook_notifier=_webhook_notifier,
        telegram_notifier=_telegram_notifier,
        discord_notifier=_discord_notifier,
        rule_emitter=emit_rule_event,
    )


def _require_task(db: Session, task_id: str) -> Task:
    try:
        return require_task(db, task_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Task not found.")


def _require_webhook_delivery(db: Session, webhook_id: str, delivery_id: str):
    svc = WebhookService(db)
    try:
        svc.get_config(webhook_id)
    except WebhookNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    delivery = svc.get_delivery(delivery_id)
    if delivery is None or delivery.webhook_id != webhook_id:
        raise HTTPException(status_code=404, detail="Webhook delivery not found.")
    return delivery


def _commit(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        _LOGGER.warning("Integrity constraint violation: %s", exc.orig)
        raise HTTPException(
            status_code=409,
            detail="Database conflict: the record already exists or violates a constraint.",
        )
    except Exception as exc:
        db.rollback()
        _LOGGER.exception("Unexpected error during database commit")
        raise HTTPException(status_code=500, detail="Internal server error.") from exc
