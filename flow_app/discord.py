from __future__ import annotations

from datetime import timedelta
import logging

import httpx
from sqlalchemy.orm import Session

from .config import FlowSettings, get_settings
from .models import Task, utcnow
from .notifications import NotificationProvider
from .repository import create_notification_delivery, update_notification_delivery
from .telegram import _human_event

logger = logging.getLogger("flow.discord")


class DiscordNotificationProvider(NotificationProvider):
    def __init__(self, settings: FlowSettings | None = None, max_retries: int = 3) -> None:
        self._settings = settings
        self.max_retries = max_retries

    def send(self, db: Session, event: str, task: Task, changes: dict | None = None) -> None:
        settings = self._settings or get_settings()
        webhook_url = settings.discord_webhook_url.strip()
        if not webhook_url:
            logger.debug("Skipping Discord notification because webhook URL is not configured.")
            return

        message = self.format_message(event, task, changes)
        delivery = create_notification_delivery(
            db,
            provider="discord",
            event=event,
            task_id=task.id,
            payload=message,
            max_retries=self.max_retries,
        )
        payload = {"content": message}

        try:
            response = httpx.post(webhook_url, json=payload, timeout=10.0)
        except httpx.HTTPError as exc:
            attempts = delivery.attempts + 1
            status = "retrying" if attempts < delivery.max_retries else "failed"
            next_attempt_at = utcnow() + timedelta(seconds=60 * (2 ** (attempts - 1))) if status == "retrying" else None
            update_notification_delivery(
                db,
                delivery,
                status=status,
                attempts=attempts,
                next_attempt_at=next_attempt_at,
                last_response_code=None,
                last_response_body=str(exc)[:2000],
            )
            return

        body = response.text[:2000]
        attempts = delivery.attempts + 1
        if 200 <= response.status_code < 300:
            update_notification_delivery(
                db,
                delivery,
                status="success",
                attempts=attempts,
                next_attempt_at=None,
                last_response_code=response.status_code,
                last_response_body=body,
            )
            return

        if response.status_code == 429 and attempts < delivery.max_retries:
            next_attempt_at = utcnow() + timedelta(seconds=_retry_after_seconds(response))
            update_notification_delivery(
                db,
                delivery,
                status="retrying",
                attempts=attempts,
                next_attempt_at=next_attempt_at,
                last_response_code=response.status_code,
                last_response_body=body,
            )
            return

        update_notification_delivery(
            db,
            delivery,
            status="failed",
            attempts=attempts,
            next_attempt_at=None,
            last_response_code=response.status_code,
            last_response_body=body,
        )

    @staticmethod
    def format_message(event: str, task: Task, changes: dict | None = None) -> str:
        lines = [
            f"**Task:** {_escape(task.title)}",
            f"**Status:** {_escape(task.status)}",
            f"**Project:** {_escape(task.project)}",
            f"**ID:** `{_escape(task.id)}`",
        ]
        header = f"**{_human_event(event)}**"
        changes_line = _format_changes_line(event, changes or {})
        return "\n".join([header, *lines, *([changes_line] if changes_line else [])])


def _escape(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace("*", "\\*").replace("_", "\\_").replace("`", "\\`")


def _format_changes_line(event: str, changes: dict) -> str:
    if event == "task_moved":
        return _status_change_line("Moved", changes)
    if event == "task_completed":
        return _status_change_line("Completed", changes)
    if event == "task_claimed" and changes.get("assignee"):
        return f"**Assigned to:** {_escape(changes['assignee'])}"
    if event == "task_blocked":
        reason = changes.get("blocker_reason") or "Human assistance required"
        return f"**Blocked:** {_escape(reason)}"
    return ""


def _status_change_line(label: str, changes: dict) -> str:
    status = changes.get("status")
    if isinstance(status, dict) and "from" in status and "to" in status:
        return f"**{label}:** {_escape(status['from'])} -> {_escape(status['to'])}"
    return ""


def _retry_after_seconds(response: httpx.Response) -> int:
    retry_after = response.headers.get("Retry-After", "").strip()
    try:
        return max(1, int(retry_after))
    except ValueError:
        return 60
