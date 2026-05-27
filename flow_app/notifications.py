from __future__ import annotations

from abc import ABC, abstractmethod

from sqlalchemy.orm import Session

from .models import Task


class NotificationProvider(ABC):
    @abstractmethod
    def send(self, db: Session, event: str, task: Task, changes: dict | None = None) -> None:
        ...


class WebhookNotificationProvider(NotificationProvider):
    """Creates pending WebhookDelivery records for matching webhook configs."""

    def send(self, db: Session, event: str, task: Task, changes: dict | None = None) -> None:
        from .webhooks import emit_event

        emit_event(db, event, task, changes)


_registry: dict[str, NotificationProvider] = {}


def register_notification_provider(channel: str, provider: NotificationProvider) -> None:
    """Register a notification provider for a given channel name."""
    _registry[channel] = provider


def get_notification_provider(channel: str) -> NotificationProvider | None:
    """Return the provider registered for the given channel, or None."""
    return _registry.get(channel)


def list_notification_channels() -> list[str]:
    """Return all registered channel names."""
    return sorted(_registry.keys())


class RulesNotifyProvider:
    """Resolves automation notify channels to registered notification providers."""

    def __init__(self, telegram_provider: NotificationProvider | None = None):
        if telegram_provider:
            register_notification_provider("telegram", telegram_provider)
            register_notification_provider("ops", telegram_provider)

    def send(self, session: Session, channel: str, event: str, task: Task, message: str) -> dict:
        """Send notification through the provider registered for the channel."""
        provider = get_notification_provider(channel)
        if provider is None:
            return {
                "provider": "note_fallback",
                "status": "no_provider",
                "details": {"reason": f"No provider registered for channel '{channel}'"},
            }
        try:
            provider.send(session, event, task, changes={"notify_message": message})
            return {"provider": channel, "status": "sent", "details": None}
        except Exception as exc:
            return {"provider": channel, "status": "failed", "details": {"error": str(exc)}}
