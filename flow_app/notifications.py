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
