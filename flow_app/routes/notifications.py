from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..discord import DiscordNotificationProvider
from ..notifications import get_notification_provider, list_notification_channels
from ..repository import (
    get_task,
    list_notification_deliveries,
    serialize_notification_delivery,
)
from ..schemas import (
    NotificationOverviewResponse,
    NotificationProviderStatus,
    NotificationTestRequest,
    NotificationTestResponse,
)
from ..security import Actor, Permission, require_permission
from ..telegram import TelegramNotificationProvider
from .dependencies import _commit, get_db


router = APIRouter()


@router.get("/notifications", response_model=NotificationOverviewResponse)
def api_notifications_overview(
    request: Request,
    db: Session = Depends(get_db),
    _actor: Actor = Depends(require_permission(Permission.RULES_READ)),
):
    settings = request.app.state.settings
    deliveries = [serialize_notification_delivery(item) for item in list_notification_deliveries(db, limit=20)]
    return NotificationOverviewResponse(
        providers=_provider_statuses(settings),
        deliveries=deliveries,
    )


@router.post("/notifications/test", response_model=NotificationTestResponse)
def api_test_notification(
    payload: NotificationTestRequest,
    request: Request,
    db: Session = Depends(get_db),
    _actor: Actor = Depends(require_permission(Permission.RULES_EVALUATE)),
):
    task = get_task(db, payload.task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found.")

    settings = request.app.state.settings
    provider_name = _provider_name(payload.channel)
    if not _is_configured(provider_name, settings):
        return NotificationTestResponse(
            channel=payload.channel,
            provider=provider_name,
            status="not_configured",
            message=f"{provider_name} notification settings are not configured.",
            delivery=None,
        )

    provider = _provider_for_channel(payload.channel, settings)
    if provider is None:
        return NotificationTestResponse(
            channel=payload.channel,
            provider=provider_name,
            status="no_provider",
            message=f"No notification provider is registered for '{payload.channel}'.",
            delivery=None,
        )

    try:
        provider.send(db, "automation_notify", task, {"notify_message": payload.message})
        _commit(db)
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=502, detail=f"Notification provider failed: {exc}") from exc

    delivery = next(
        (
            item
            for item in list_notification_deliveries(db, provider=provider_name, task_id=task.id, limit=5)
            if item.event == "automation_notify"
        ),
        None,
    )
    if delivery is None:
        return NotificationTestResponse(
            channel=payload.channel,
            provider=provider_name,
            status="no_delivery",
            message="Provider completed without creating a delivery row.",
            delivery=None,
        )
    serialized = serialize_notification_delivery(delivery)
    return NotificationTestResponse(
        channel=payload.channel,
        provider=provider_name,
        status=serialized.status,
        message=f"Notification test finished with status {serialized.status}.",
        delivery=serialized,
    )


def _provider_statuses(settings) -> list[NotificationProviderStatus]:
    registered = set(list_notification_channels())
    rows = [
        ("telegram", "telegram", "Telegram", ["FLOW_TELEGRAM_BOT_TOKEN", "FLOW_TELEGRAM_CHAT_ID"]),
        ("ops", "telegram", "Ops channel", ["FLOW_TELEGRAM_BOT_TOKEN", "FLOW_TELEGRAM_CHAT_ID"]),
        ("discord", "discord", "Discord", ["FLOW_DISCORD_WEBHOOK_URL"]),
    ]
    return [
        NotificationProviderStatus(
            channel=channel,
            provider=provider,
            label=label,
            configured=_is_configured(provider, settings),
            registered=channel in registered,
            config_keys=keys,
            detail=_provider_detail(provider, settings),
        )
        for channel, provider, label, keys in rows
    ]


def _provider_for_channel(channel: str, settings):
    provider_name = _provider_name(channel)
    if provider_name == "telegram":
        return TelegramNotificationProvider(settings=settings)
    if provider_name == "discord":
        return DiscordNotificationProvider(settings=settings)
    return get_notification_provider(channel)


def _provider_name(channel: str) -> str:
    cleaned = channel.strip().lower()
    if cleaned == "ops":
        return "telegram"
    return cleaned


def _is_configured(provider: str, settings) -> bool:
    if provider == "telegram":
        return bool(settings.telegram_bot_token.strip() and settings.telegram_chat_id.strip())
    if provider == "discord":
        return bool(settings.discord_webhook_url.strip())
    return get_notification_provider(provider) is not None


def _provider_detail(provider: str, settings) -> str:
    if provider == "telegram":
        return "configured" if _is_configured(provider, settings) else "bot token or chat id missing"
    if provider == "discord":
        return "configured" if _is_configured(provider, settings) else "webhook URL missing"
    return "registered" if get_notification_provider(provider) is not None else "not registered"
