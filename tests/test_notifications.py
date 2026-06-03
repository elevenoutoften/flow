from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from flow_app.models import NotificationDelivery
from sqlalchemy import select


def _deliveries(client):
    with client.app.state.SessionLocal() as db:
        return list(db.scalars(select(NotificationDelivery)).all())


def test_notifications_overview_lists_providers_without_secret_values(client):
    response = client.get("/api/notifications")

    assert response.status_code == 200, response.text
    data = response.json()
    channels = {provider["channel"] for provider in data["providers"]}
    assert {"telegram", "ops", "discord"} <= channels
    assert "FLOW_DISCORD_WEBHOOK_URL" in str(data["providers"])
    assert "https://discord.example" not in str(data)


def test_notification_test_requires_existing_task(client):
    response = client.post(
        "/api/notifications/test",
        json={"channel": "discord", "task_id": "flow_999999", "message": "Hello"},
    )

    assert response.status_code == 404


def test_notification_test_reports_not_configured_without_delivery(client):
    task = client.post("/api/tasks", json={"title": "Notify me", "project": "default"}).json()

    response = client.post(
        "/api/notifications/test",
        json={"channel": "discord", "task_id": task["id"], "message": "Hello"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "not_configured"
    assert _deliveries(client) == []


def test_notification_test_sends_configured_discord_delivery(client):
    task = client.post("/api/tasks", json={"title": "Notify Discord", "project": "default"}).json()
    client.app.state.settings = replace(client.app.state.settings, discord_webhook_url="https://discord.example/webhook")

    with patch("flow_app.discord.httpx.post", return_value=SimpleNamespace(status_code=204, text="", headers={})) as post:
        response = client.post(
            "/api/notifications/test",
            json={"channel": "discord", "task_id": task["id"], "message": "Ship it"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["provider"] == "discord"
    assert body["status"] == "success"
    assert body["delivery"]["task_id"] == task["id"]
    assert post.call_args.args[0] == "https://discord.example/webhook"
