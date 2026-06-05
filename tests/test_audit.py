from __future__ import annotations

from dataclasses import replace
import json

import httpx

from flow_app.metrics import metrics
from flow_app.repository import create_task
from flow_app.ratelimit import key_creation_limiter
from flow_app.schemas import TaskCreate
from flow_app.telegram import TelegramNotificationProvider


def _audit_actions(client, action: str | None = None) -> list[str]:
    params = {"action": action} if action else None
    return [entry["action"] for entry in client.get("/api/audit-log", params=params).json()["items"]]


def test_audit_log_entries_for_key_task_move_and_dispatch(client, monkeypatch):
    monkeypatch.setattr("flow_app.dispatcher.subprocess.Popen", lambda *args, **kwargs: None)
    monkeypatch.setattr("flow_app.dispatcher.threading.Thread", lambda *args, **kwargs: type("T", (), {"start": lambda self: None})())

    key = client.post("/api/api-keys", json={"name": "impl", "role": "implementer"}).json()
    task = client.post("/api/tasks", json={"title": "Audited task", "status": "todo"}).json()
    agent = client.post(
        "/api/agents",
        json={
            "name": "audit-agent",
            "agent_type": "remote",
            "capabilities": "",
            "command": "",
            "enabled": True,
        },
    ).json()
    run = client.post(f"/api/agents/{agent['id']}/dispatch", params={"task_id": task["id"]}).json()
    moved = client.post(f"/api/tasks/{task['id']}/move", json={"status": "review"})

    assert run["task_id"] == task["id"]
    assert moved.status_code == 200
    actions = [entry["action"] for entry in client.get("/api/audit-log").json()["items"]]
    assert "api_key.create" in actions
    assert "agent.dispatch" in actions
    assert "task.move" in actions
    assert key["id"].startswith("key_")


def test_audit_detail_redacts_secrets(client):
    response = client.post(
        "/api/webhooks",
        json={"name": "Example", "url": "https://example.com/hook", "events": ["task_created"], "project": "*"},
    )
    assert response.status_code == 201
    entries = client.get("/api/audit-log", params={"action": "webhook.create"}).json()["items"]
    details = [entry["detail"] for entry in entries]
    assert all(response.json()["secret"] not in detail for detail in details)


def test_list_audit_log_filters_by_action_and_actor(client):
    client.post("/api/api-keys", json={"name": "one", "role": "reviewer"})
    client.post("/api/tasks", json={"title": "Filter me"})

    key_entries = client.get("/api/audit-log", params={"action": "api_key.create"}).json()
    actor_id = key_entries["items"][0]["actor_id"]
    actor_entries = client.get("/api/audit-log", params={"actor_id": actor_id}).json()

    assert key_entries["total"] == 1
    assert all(entry["actor_id"] == actor_id for entry in actor_entries["items"])


def test_metrics_snapshot_after_actions(client):
    client.post("/api/tasks", json={"title": "Metric task", "status": "todo"})
    task = client.get("/api/tasks").json()["items"][0]
    client.post(f"/api/tasks/{task['id']}/move", json={"status": "doing"})

    data = client.get("/api/metrics").json()
    assert data["counters"]["task.created"] == 1
    assert data["counters"]["task.moved"] == 1
    assert "timers" in data


def test_key_creation_limiter_blocks_after_threshold():
    key_creation_limiter.reset()
    for _ in range(10):
        key_creation_limiter.check("unit-test-key")
    try:
        key_creation_limiter.check("unit-test-key")
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 429
        assert "Rate limit exceeded" in exc.detail
    else:
        raise AssertionError("Expected rate limit exception.")


def test_key_creation_rate_limit_returns_429(client):
    for index in range(10):
        response = client.post("/api/api-keys", json={"name": f"key-{index}", "role": "read_only"})
        assert response.status_code == 201
    response = client.post("/api/api-keys", json={"name": "blocked", "role": "read_only"})
    assert response.status_code == 429
    assert response.json()["detail"] == "Rate limit exceeded. Try again later."


def test_audit_read_permission_admin_can_read_implementer_cannot(client):
    key = client.post("/api/api-keys", json={"name": "impl-reader", "role": "implementer"}).json()

    admin_response = client.get("/api/audit-log")
    assert admin_response.status_code == 200

    impl_response = client.get("/api/audit-log", headers={"Authorization": f"Bearer {key['api_key']}"})
    assert impl_response.status_code == 403


def test_audit_log_has_no_update_or_delete_endpoints(client):
    client.post("/api/tasks", json={"title": "Append only"})
    entry = client.get("/api/audit-log").json()["items"][0]

    assert client.patch(f"/api/audit-log/{entry['id']}", json={"detail": json.dumps({"x": 1})}).status_code in {404, 405}
    assert client.delete(f"/api/audit-log/{entry['id']}").status_code in {404, 405}


def test_audit_agent_create(client):
    response = client.post("/api/agents", json={"name": "audit-create-agent"})

    assert response.status_code == 201
    assert "agent.create" in _audit_actions(client, "agent.create")


def test_audit_agent_update(client):
    agent = client.post("/api/agents", json={"name": "audit-update-agent"}).json()
    response = client.patch(f"/api/agents/{agent['id']}", json={"description": "updated"})

    assert response.status_code == 200
    assert "agent.update" in _audit_actions(client, "agent.update")


def test_audit_runner_create(client):
    response = client.post("/api/runners", json={"name": "audit-create-runner"})

    assert response.status_code == 201
    assert "runner.create" in _audit_actions(client, "runner.create")


def test_audit_runner_delete(client):
    runner = client.post("/api/runners", json={"name": "audit-delete-runner"}).json()
    response = client.delete(f"/api/runners/{runner['id']}")

    assert response.status_code == 200
    assert "runner.delete" in _audit_actions(client, "runner.delete")


def test_audit_webhook_update(client):
    webhook = client.post(
        "/api/webhooks",
        json={"name": "Audit Hook", "url": "https://example.com/hook", "events": ["task_created"], "project": "*"},
    ).json()
    response = client.patch(f"/api/webhooks/{webhook['id']}", json={"name": "Audit Hook Updated"})

    assert response.status_code == 200
    assert "webhook.update" in _audit_actions(client, "webhook.update")


def test_audit_task_note(client):
    task = client.post("/api/tasks", json={"title": "Audited note", "status": "doing"}).json()
    response = client.post(f"/api/tasks/{task['id']}/note", json={"note": "progress"})

    assert response.status_code == 200
    assert "task.note" in _audit_actions(client, "task.note")


def test_audit_task_done(client):
    task = client.post("/api/tasks", json={"title": "Audited done", "status": "review"}).json()
    response = client.post(f"/api/tasks/{task['id']}/done", json={"summary": "finished"})

    assert response.status_code == 200
    assert "task.done" in _audit_actions(client, "task.done")


def test_audit_task_handoff(client):
    task = client.post("/api/tasks", json={"title": "Audited handoff"}).json()
    response = client.post(f"/api/tasks/{task['id']}/handoff", json={"summary": "handoff summary"})

    assert response.status_code == 201
    assert "task.handoff" in _audit_actions(client, "task.handoff")


def test_auth_rate_limit_blocks_after_threshold(client):
    for _ in range(20):
        response = client.post("/login", data={"api_key": "invalid"}, follow_redirects=False)
        assert response.status_code == 303

    response = client.post("/login", data={"api_key": "invalid"}, follow_redirects=False)
    assert response.status_code == 429


def test_notification_success_metric(client, monkeypatch):
    settings = replace(client.app.state.settings, telegram_bot_token="token", telegram_chat_id="chat")
    provider = TelegramNotificationProvider(settings=settings)
    monkeypatch.setattr("flow_app.telegram.httpx.post", lambda *args, **kwargs: httpx.Response(200, text="ok"))
    with client.app.state.SessionLocal() as db:
        task = create_task(db, TaskCreate(title="Notify success"))
        provider.send(db, "task_created", task)
        db.commit()

    assert metrics.snapshot()["counters"]["notification.success"] == 1


def test_notification_failed_metric(client, monkeypatch):
    settings = replace(client.app.state.settings, telegram_bot_token="token", telegram_chat_id="chat")
    provider = TelegramNotificationProvider(settings=settings)
    monkeypatch.setattr("flow_app.telegram.httpx.post", lambda *args, **kwargs: httpx.Response(500, text="failed"))
    with client.app.state.SessionLocal() as db:
        task = create_task(db, TaskCreate(title="Notify failed"))
        provider.send(db, "task_created", task)
        db.commit()

    assert metrics.snapshot()["counters"]["notification.failed"] == 1


def test_api_error_metrics_4xx(client):
    response = client.get("/api/tasks/missing-task")

    assert response.status_code == 404
    assert metrics.snapshot()["counters"]["api.errors.4xx"] == 1
