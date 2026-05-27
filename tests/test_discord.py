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
from flow_app.discord import DiscordNotificationProvider


def _task(**overrides):
    values = {
        "id": "flow_000001",
        "title": "Write tests",
        "status": "todo",
        "project": "default",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _settings(webhook_url: str = "https://discord.example/webhook"):
    return replace(get_settings(), discord_webhook_url=webhook_url)


def _create_task(db, title: str = "Provider task"):
    return create_task(db, TaskCreate(title=title, project="default"))


def _deliveries(client) -> list[NotificationDelivery]:
    with client.app.state.SessionLocal() as db:
        return list(db.scalars(select(NotificationDelivery)).all())


def test_format_message_task_created():
    message = DiscordNotificationProvider.format_message("task_created", _task(), None)

    assert message == "\n".join(
        [
            "**Task Created**",
            "**Task:** Write tests",
            "**Status:** todo",
            "**Project:** default",
            "**ID:** `flow\\_000001`",
        ]
    )


def test_send_skips_when_webhook_url_missing(client):
    provider = DiscordNotificationProvider(settings=_settings(webhook_url=""))

    with client.app.state.SessionLocal() as db:
        task = _create_task(db)
        provider.send(db, "task_created", task)
        db.commit()

    assert _deliveries(client) == []


def test_send_creates_success_delivery(client):
    provider = DiscordNotificationProvider(settings=_settings())

    with client.app.state.SessionLocal() as db:
        task = _create_task(db)
        with patch("flow_app.discord.httpx.post", return_value=SimpleNamespace(status_code=204, text="", headers={})) as post:
            provider.send(db, "task_created", task)
        db.commit()

    deliveries = _deliveries(client)
    assert len(deliveries) == 1
    assert deliveries[0].provider == "discord"
    assert deliveries[0].event == "task_created"
    assert deliveries[0].status == "success"
    assert deliveries[0].attempts == 1
    assert deliveries[0].last_response_code == 204
    assert post.call_args.args[0] == "https://discord.example/webhook"
    assert post.call_args.kwargs["json"]["content"].startswith("**Task Created**")


def test_send_creates_failed_delivery(client):
    provider = DiscordNotificationProvider(settings=_settings())

    with client.app.state.SessionLocal() as db:
        task = _create_task(db)
        with patch(
            "flow_app.discord.httpx.post",
            return_value=SimpleNamespace(status_code=403, text="forbidden", headers={}),
        ):
            provider.send(db, "task_created", task)
        db.commit()

    deliveries = _deliveries(client)
    assert len(deliveries) == 1
    assert deliveries[0].status == "failed"
    assert deliveries[0].attempts == 1
    assert deliveries[0].last_response_code == 403
    assert deliveries[0].last_response_body == "forbidden"


def test_send_creates_retry_delivery_on_http_error(client):
    provider = DiscordNotificationProvider(settings=_settings())

    with client.app.state.SessionLocal() as db:
        task = _create_task(db)
        with patch("flow_app.discord.httpx.post", side_effect=httpx.ConnectError("boom")):
            provider.send(db, "task_created", task)
        db.commit()

    deliveries = _deliveries(client)
    assert len(deliveries) == 1
    assert deliveries[0].status == "retrying"
    assert deliveries[0].attempts == 1
    assert deliveries[0].last_response_code is None
    assert "boom" in deliveries[0].last_response_body
    assert deliveries[0].next_attempt_at is not None
