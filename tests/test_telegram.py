from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from sqlalchemy import select

from flow_app.config import get_settings
from flow_app.models import NotificationDelivery
from flow_app.repository import create_task
from flow_app.schemas import TaskCreate
from flow_app.telegram import TelegramNotificationProvider


def _task(**overrides):
    values = {
        "id": "flow_000001",
        "title": "Write tests",
        "status": "todo",
        "project": "default",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _settings(bot_token: str = "123456:ABC", chat_id: str = "-1001234567890"):
    return replace(get_settings(), telegram_bot_token=bot_token, telegram_chat_id=chat_id)


def _create_task(db, title: str = "Provider task"):
    return create_task(db, TaskCreate(title=title, project="default"))


def _deliveries(client) -> list[NotificationDelivery]:
    with client.app.state.SessionLocal() as db:
        return list(db.scalars(select(NotificationDelivery)).all())


def test_format_message_task_created():
    message = TelegramNotificationProvider.format_message("task_created", _task(), None)

    assert message == "\n".join(
        [
            "<b>📋 Task Created</b>",
            "<b>Task:</b> Write tests",
            "<b>Status:</b> todo",
            "<b>Project:</b> default",
            "<b>ID:</b> flow_000001",
        ]
    )


def test_format_message_task_moved():
    message = TelegramNotificationProvider.format_message(
        "task_moved",
        _task(status="review"),
        {"status": {"from": "todo", "to": "review"}},
    )

    assert "<b>Moved:</b> todo → review" in message


def test_format_message_task_claimed():
    message = TelegramNotificationProvider.format_message("task_claimed", _task(), {"assignee": "nyx"})

    assert "<b>Assigned to:</b> nyx" in message


def test_format_message_task_blocked():
    message = TelegramNotificationProvider.format_message(
        "task_blocked",
        _task(),
        {"human_required": True, "blocker_reason": "Need credentials"},
    )

    assert "<b>Blocked:</b> Need credentials" in message


def test_send_skips_when_token_missing(client):
    provider = TelegramNotificationProvider(settings=_settings(bot_token=""))

    with client.app.state.SessionLocal() as db:
        task = _create_task(db)
        provider.send(db, "task_created", task)
        db.commit()

    assert _deliveries(client) == []


def test_send_creates_success_delivery(client):
    provider = TelegramNotificationProvider(settings=_settings())

    with client.app.state.SessionLocal() as db:
        task = _create_task(db)
        with patch("flow_app.telegram.httpx.post", return_value=SimpleNamespace(status_code=200, text="ok")) as post:
            provider.send(db, "task_created", task)
        db.commit()

    deliveries = _deliveries(client)
    assert len(deliveries) == 1
    assert deliveries[0].provider == "telegram"
    assert deliveries[0].event == "task_created"
    assert deliveries[0].status == "success"
    assert deliveries[0].attempts == 1
    assert deliveries[0].last_response_code == 200
    assert post.call_args.args[0] == "https://api.telegram.org/bot123456:ABC/sendMessage"
    assert post.call_args.kwargs["data"]["chat_id"] == "-1001234567890"
    assert post.call_args.kwargs["data"]["parse_mode"] == "HTML"


def test_send_creates_failed_delivery(client):
    provider = TelegramNotificationProvider(settings=_settings())

    with client.app.state.SessionLocal() as db:
        task = _create_task(db)
        with patch("flow_app.telegram.httpx.post", return_value=SimpleNamespace(status_code=400, text="bad request")):
            provider.send(db, "task_created", task)
        db.commit()

    deliveries = _deliveries(client)
    assert len(deliveries) == 1
    assert deliveries[0].status == "failed"
    assert deliveries[0].attempts == 1
    assert deliveries[0].last_response_code == 400
    assert deliveries[0].last_response_body == "bad request"


def test_send_creates_retry_delivery(client):
    provider = TelegramNotificationProvider(settings=_settings())

    with client.app.state.SessionLocal() as db:
        task = _create_task(db)
        with patch("flow_app.telegram.httpx.post", side_effect=httpx.ConnectError("boom")):
            provider.send(db, "task_created", task)
        db.commit()

    deliveries = _deliveries(client)
    assert len(deliveries) == 1
    assert deliveries[0].status == "retrying"
    assert deliveries[0].attempts == 1
    assert deliveries[0].last_response_code is None
    assert "boom" in deliveries[0].last_response_body
    assert deliveries[0].next_attempt_at is not None
