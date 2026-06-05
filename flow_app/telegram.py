from __future__ import annotations

from datetime import timedelta
from html import escape
import logging

import httpx
from sqlalchemy.orm import Session

from .config import FlowSettings, get_settings
from .metrics import metrics
from .models import Task, utcnow
from .notifications import NotificationProvider
from .repository import create_notification_delivery, update_notification_delivery
from .secrets_resolver import resolve_secret

logger = logging.getLogger("flow.telegram")


class TelegramNotificationProvider(NotificationProvider):
    def __init__(self, settings: FlowSettings | None = None, max_retries: int = 3) -> None:
        self._settings = settings
        self.max_retries = max_retries

    def send(self, db: Session, event: str, task: Task, changes: dict | None = None) -> None:
        settings = self._settings or get_settings()
        # Settings resolve secret references at load time; resolve here too for injected test/custom settings.
        bot_token = resolve_secret(settings.telegram_bot_token).strip()
        chat_id = settings.telegram_chat_id.strip()
        if not bot_token or not chat_id:
            logger.debug("Skipping Telegram notification because bot token or chat ID is not configured.")
            return

        message = self.format_message(event, task, changes)
        delivery = create_notification_delivery(
            db,
            provider="telegram",
            event=event,
            task_id=task.id,
            payload=message,
            max_retries=self.max_retries,
        )
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}

        try:
            response = httpx.post(url, data=payload, timeout=10.0)
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
            metrics.inc("notification.retrying" if status == "retrying" else "notification.failed")
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
            metrics.inc("notification.success")
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
        metrics.inc("notification.failed")

    @staticmethod
    def format_message(event: str, task: Task, changes: dict | None = None) -> str:
        lines = [
            f"<b>📋 {escape(_human_event(event))}</b>",
            f"<b>Task:</b> {escape(task.title)}",
            f"<b>Status:</b> {escape(task.status)}",
            f"<b>Project:</b> {escape(task.project)}",
            f"<b>ID:</b> {escape(task.id)}",
        ]
        changes_line = _format_changes_line(event, changes or {})
        if changes_line:
            lines.append(changes_line)
        return "\n".join(lines)


def _human_event(event: str) -> str:
    labels = {
        "task_created": "Task Created",
        "task_claimed": "Task Claimed",
        "task_moved": "Task Moved",
        "task_completed": "Task Completed",
        "task_blocked": "Task Blocked",
    }
    return labels.get(event, event.replace("_", " ").title())


def _format_changes_line(event: str, changes: dict) -> str:
    if event == "task_moved":
        return _status_change_line("Moved", changes)
    if event == "task_completed":
        return _status_change_line("Completed", changes)
    if event == "task_claimed" and changes.get("assignee"):
        return f"<b>Assigned to:</b> {escape(str(changes['assignee']))}"
    if event == "task_blocked":
        reason = changes.get("blocker_reason") or "Human assistance required"
        return f"<b>Blocked:</b> {escape(str(reason))}"
    return ""


def _status_change_line(label: str, changes: dict) -> str:
    status = changes.get("status")
    if isinstance(status, dict) and "from" in status and "to" in status:
        return f"<b>{label}:</b> {escape(str(status['from']))} → {escape(str(status['to']))}"
    return ""
