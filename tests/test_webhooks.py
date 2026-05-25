from __future__ import annotations

import hashlib
import hmac
import json
import socket
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from flow_app import main as main_module
from flow_app.models import Task, WebhookDelivery, utcnow
from flow_app.notifications import WebhookNotificationProvider
from flow_app.repository import (
    create_task as repo_create_task,
    create_webhook_delivery,
    get_webhook_config,
    get_webhook_delivery,
    update_webhook_delivery,
)
from flow_app.schemas import TaskCreate
from flow_app.ssrf import SSRF_ERROR_MSG, resolve_webhook_target, validate_webhook_url
from flow_app.webhooks import deliver_webhook, sign_payload


@pytest.fixture(autouse=True)
def deterministic_webhook_dns(monkeypatch):
    def fake_getaddrinfo(host, port, *args, **kwargs):
        if host == "example.com":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]
        if host == "localhost":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]
        try:
            socket.inet_pton(socket.AF_INET, host)
        except OSError:
            pass
        else:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (host, port))]
        try:
            socket.inet_pton(socket.AF_INET6, host)
        except OSError:
            pass
        else:
            return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", (host, port, 0, 0))]
        raise socket.gaierror(f"Could not resolve {host}")

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


def bearer_headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


def create_role_headers(client, role: str, name: str | None = None) -> dict[str, str]:
    created = client.post("/api/api-keys", json={"name": name or f"{role}-webhook", "role": role})
    assert created.status_code == 201, created.text
    return bearer_headers(created.json()["api_key"])


def create_webhook(client, **overrides):
    payload = {
        "name": "Task events",
        "url": "https://example.com/webhook",
        "events": ["task_created"],
        "project": "*",
        "max_retries": 3,
        "retry_backoff_seconds": 60,
    }
    payload.update(overrides)
    response = client.post("/api/webhooks", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def create_task(client, **overrides):
    payload = {
        "title": "Webhook task",
        "status": "todo",
        "priority": 50,
        "project": "default",
    }
    payload.update(overrides)
    response = client.post("/api/tasks", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def list_deliveries(client, webhook_id: str):
    response = client.get(f"/api/webhooks/{webhook_id}/deliveries")
    assert response.status_code == 200, response.text
    return response.json()["items"]


def assert_single_delivery(client, webhook_id: str, event: str, task_id: str):
    deliveries = list_deliveries(client, webhook_id)
    assert len(deliveries) == 1
    assert deliveries[0]["event"] == event
    assert deliveries[0]["status"] == "pending"
    detail = client.get(f"/api/webhooks/{webhook_id}/deliveries/{deliveries[0]['id']}")
    assert detail.status_code == 200, detail.text
    payload = json.loads(detail.json()["payload"])
    assert payload["event"] == event
    assert payload["task_id"] == task_id
    return deliveries[0]


def test_validate_webhook_url_accepts_valid_public_url(monkeypatch):
    url = "https://example.com/webhook"

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", args[1]))],
    )

    assert validate_webhook_url(url) == url


def test_validate_webhook_url_rejects_dns_rebinding(monkeypatch):
    calls = iter(
        [
            [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
            [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))],
        ]
    )
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: next(calls))

    resolved_ip, url = resolve_webhook_target("https://example.com/webhook")

    assert resolved_ip == "93.184.216.34"
    assert url == "https://example.com/webhook"


def test_validate_webhook_url_rejects_invalid_scheme():
    with pytest.raises(ValueError, match="http or https"):
        validate_webhook_url("ftp://example.com")


@pytest.mark.parametrize("url", ["http://localhost/test", "http://127.0.0.1/test"])
def test_validate_webhook_url_rejects_localhost(url):
    with pytest.raises(ValueError, match=SSRF_ERROR_MSG):
        validate_webhook_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://10.0.0.1/test",
        "http://172.16.0.1/test",
        "http://192.168.1.1/test",
        "http://169.254.169.254/test",
    ],
)
def test_validate_webhook_url_rejects_private_ips(url):
    with pytest.raises(ValueError, match=SSRF_ERROR_MSG):
        validate_webhook_url(url)


def test_validate_webhook_url_rejects_ipv6_loopback():
    with pytest.raises(ValueError, match=SSRF_ERROR_MSG):
        validate_webhook_url("http://[::1]/test")


def test_resolve_webhook_target_accepts_ipv6_bracket_hostname():
    resolved_ip, url = resolve_webhook_target("https://[2606:2800:220:1:248:1893:25c8:1946]/webhook")

    assert resolved_ip == "2606:2800:220:1:248:1893:25c8:1946"
    assert url == "https://[2606:2800:220:1:248:1893:25c8:1946]/webhook"


def test_resolve_webhook_target_uses_specified_port(monkeypatch):
    seen_ports = []

    def fake_getaddrinfo(host, port, *args, **kwargs):
        seen_ports.append(port)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    resolved_ip, url = resolve_webhook_target("https://example.com:8443/webhook")

    assert resolved_ip == "93.184.216.34"
    assert url == "https://example.com:8443/webhook"
    assert seen_ports == [8443]


def test_resolve_webhook_target_rejects_unresolved_hostname(monkeypatch):
    def fake_getaddrinfo(host, port, *args, **kwargs):
        raise socket.gaierror("no address")

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    with pytest.raises(ValueError, match="Could not resolve hostname"):
        resolve_webhook_target("https://does-not-resolve.invalid/webhook")


def test_webhook_config_create(client):
    config = create_webhook(client, name="Build hooks", events=["task_created", "task_completed"], project="default")

    assert config["id"] == "webhook_000001"
    assert config["name"] == "Build hooks"
    assert config["events"] == ["task_created", "task_completed"]
    assert config["project"] == "default"
    assert config["active"] == 1
    assert config["secret"]

    fetched = client.get(f"/api/webhooks/{config['id']}").json()
    assert "secret" not in fetched


def test_webhook_config_list(client):
    config = create_webhook(client)

    response = client.get("/api/webhooks")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [config["id"]]


def test_webhook_config_list_filter_by_project(client):
    default_config = create_webhook(client, name="Default", project="default")
    create_webhook(client, name="Other", project="other")

    response = client.get("/api/webhooks?project=default")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [default_config["id"]]


def test_webhook_config_get(client):
    config = create_webhook(client, name="Single")

    response = client.get(f"/api/webhooks/{config['id']}")

    assert response.status_code == 200
    assert response.json()["name"] == "Single"


def test_webhook_config_get_not_found(client):
    response = client.get("/api/webhooks/webhook_nonexistent")

    assert response.status_code == 404


def test_webhook_config_update(client):
    config = create_webhook(client)

    response = client.patch(
        f"/api/webhooks/{config['id']}",
        json={
            "name": "Updated",
            "url": "https://example.com/updated",
            "events": ["task_moved", "task_blocked"],
            "active": 0,
        },
    )

    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["name"] == "Updated"
    assert updated["url"] == "https://example.com/updated"
    assert updated["events"] == ["task_moved", "task_blocked"]
    assert updated["active"] == 0


def test_webhook_config_delete(client):
    config = create_webhook(client)

    response = client.delete(f"/api/webhooks/{config['id']}")

    assert response.status_code == 204
    assert client.get(f"/api/webhooks/{config['id']}").status_code == 404


def test_webhook_config_create_invalid_events(client):
    response = client.post(
        "/api/webhooks",
        json={"name": "Bad", "url": "https://example.com/webhook", "events": ["task_renamed"]},
    )

    assert response.status_code == 422


def test_webhook_config_create_rejects_unsafe_url(client):
    response = client.post(
        "/api/webhooks",
        json={"name": "Bad", "url": "http://127.0.0.1/", "events": ["task_created"]},
    )

    assert response.status_code == 422


def test_webhook_config_update_rejects_unsafe_url(client):
    config = create_webhook(client)

    response = client.patch(f"/api/webhooks/{config['id']}", json={"url": "http://127.0.0.1/"})

    assert response.status_code == 422


def test_webhook_config_create_requires_manage(client, no_auth_client):
    headers = create_role_headers(client, "implementer", "webhook-implementer")

    response = no_auth_client.post(
        "/api/webhooks",
        json={"name": "Denied", "url": "https://example.com/webhook", "events": ["task_created"]},
        headers=headers,
    )

    assert response.status_code == 403


def test_webhook_config_list_requires_read(no_auth_client):
    assert no_auth_client.get("/api/webhooks").status_code == 401


def test_webhook_config_delete_requires_manage(client, no_auth_client):
    config = create_webhook(client)
    headers = create_role_headers(client, "read_only", "webhook-reader")

    response = no_auth_client.delete(f"/api/webhooks/{config['id']}", headers=headers)

    assert response.status_code == 403


def test_webhook_delivery_list_requires_read(no_auth_client):
    assert no_auth_client.get("/api/webhooks/webhook_000001/deliveries").status_code == 401


def test_webhook_emits_on_task_created(client):
    config = create_webhook(client, events=["task_created"])
    task = create_task(client)

    assert_single_delivery(client, config["id"], "task_created", task["id"])


def test_webhook_emits_on_task_claimed(client):
    config = create_webhook(client, events=["task_claimed"])
    task = create_task(client)

    response = client.post(f"/api/tasks/{task['id']}/claim", json={"agent_name": "codex"})

    assert response.status_code == 200, response.text
    assert_single_delivery(client, config["id"], "task_claimed", task["id"])


def test_webhook_emits_on_task_moved(client):
    config = create_webhook(client, events=["task_moved"])
    task = create_task(client, status="doing")

    response = client.post(f"/api/tasks/{task['id']}/move", json={"status": "review"})

    assert response.status_code == 200, response.text
    assert_single_delivery(client, config["id"], "task_moved", task["id"])


def test_webhook_emits_on_task_completed(client):
    config = create_webhook(client, events=["task_completed"])
    task = create_task(client, status="review")

    response = client.post(f"/api/tasks/{task['id']}/done", json={"summary": "Finished"})

    assert response.status_code == 200, response.text
    assert_single_delivery(client, config["id"], "task_completed", task["id"])


def test_task_mutations_commit_with_webhook_outbox_rows(client):
    create_webhook(
        client,
        events=["task_created", "task_claimed", "task_moved", "task_completed"],
    )

    task = create_task(client)
    claim_response = client.post(f"/api/tasks/{task['id']}/claim", json={"agent_name": "codex"})
    move_response = client.post(f"/api/tasks/{task['id']}/move", json={"status": "review"})
    done_response = client.post(f"/api/tasks/{task['id']}/done", json={"summary": "Finished"})

    assert claim_response.status_code == 200, claim_response.text
    assert move_response.status_code == 200, move_response.text
    assert done_response.status_code == 200, done_response.text

    with client.app.state.SessionLocal() as db:
        saved_task = db.get(Task, task["id"])
        deliveries = list(
            db.scalars(
                select(WebhookDelivery)
                .where(WebhookDelivery.webhook_id == "webhook_000001")
                .order_by(WebhookDelivery.id)
            ).all()
        )

    assert saved_task is not None
    assert saved_task.status == "done"
    assert [delivery.event for delivery in deliveries] == [
        "task_created",
        "task_claimed",
        "task_moved",
        "task_completed",
    ]


def test_task_create_rolls_back_when_webhook_outbox_write_fails(client):
    create_webhook(client, events=["task_created"])

    with patch.object(main_module._webhook_notifier, "send", side_effect=RuntimeError("outbox write failed")):
        with TestClient(client.app, raise_server_exceptions=False) as failing_client:
            failing_client.headers.update(client.headers)
            response = failing_client.post(
                "/api/tasks",
                json={
                    "title": "Rollback task",
                    "status": "todo",
                    "priority": 50,
                    "project": "default",
                },
            )

    assert response.status_code == 500
    with client.app.state.SessionLocal() as db:
        task_count = len(db.scalars(select(Task).where(Task.title == "Rollback task")).all())
        delivery_count = len(db.scalars(select(WebhookDelivery)).all())

    assert task_count == 0
    assert delivery_count == 0


def test_webhook_emits_on_task_blocked(client):
    config = create_webhook(client, events=["task_blocked"])
    task = create_task(client)

    response = client.patch(
        f"/api/tasks/{task['id']}",
        json={"human_required": True, "blocker_reason": "Needs product decision"},
    )

    assert response.status_code == 200, response.text
    delivery = assert_single_delivery(client, config["id"], "task_blocked", task["id"])
    detail = client.get(f"/api/webhooks/{config['id']}/deliveries/{delivery['id']}").json()
    assert json.loads(detail["payload"])["changes"]["blocker_reason"] == "Needs product decision"


def test_webhook_emits_on_idea_promoted(client):
    config = create_webhook(client, events=["idea_promoted"])
    idea_response = client.post("/api/ideas", json={"title": "Ship webhooks", "project": "default"})
    assert idea_response.status_code == 201, idea_response.text
    idea = idea_response.json()

    promoted = client.post(
        f"/api/ideas/{idea['id']}/promote",
        json=[{"title": "First task"}, {"title": "Second task", "status": "todo"}],
    )

    assert promoted.status_code == 200, promoted.text
    deliveries = list_deliveries(client, config["id"])
    assert len(deliveries) == 2
    assert {delivery["event"] for delivery in deliveries} == {"idea_promoted"}
    payloads = [
        json.loads(client.get(f"/api/webhooks/{config['id']}/deliveries/{delivery['id']}").json()["payload"])
        for delivery in deliveries
    ]
    assert {payload["changes"]["idea_id"] for payload in payloads} == {idea["id"]}
    assert {payload["task_id"] for payload in payloads} == set(promoted.json()["promoted_task_ids"])


def test_inactive_webhook_skipped(client):
    config = create_webhook(client, events=["task_created"])
    response = client.patch(f"/api/webhooks/{config['id']}", json={"active": 0})
    assert response.status_code == 200, response.text

    create_task(client)

    assert list_deliveries(client, config["id"]) == []


def test_webhook_project_scoped(client):
    config = create_webhook(client, events=["task_created"], project="default")

    create_task(client, project="other")

    assert list_deliveries(client, config["id"]) == []


def test_webhook_global_project(client):
    config = create_webhook(client, events=["task_created"], project="*")
    task = create_task(client, project="other")

    assert_single_delivery(client, config["id"], "task_created", task["id"])


def test_webhook_delivery_list(client):
    config = create_webhook(client, events=["task_created"])
    task = create_task(client)

    response = client.get(f"/api/webhooks/{config['id']}/deliveries")

    assert response.status_code == 200
    deliveries = response.json()["items"]
    assert len(deliveries) == 1
    assert deliveries[0]["webhook_id"] == config["id"]
    assert deliveries[0]["event"] == "task_created"
    assert deliveries[0]["status"] == "pending"
    assert deliveries[0]["id"]
    assert_single_delivery(client, config["id"], "task_created", task["id"])


def test_webhook_delivery_detail(client):
    config = create_webhook(client, events=["task_created"])
    task = create_task(client)
    delivery = list_deliveries(client, config["id"])[0]

    response = client.get(f"/api/webhooks/{config['id']}/deliveries/{delivery['id']}")

    assert response.status_code == 200
    detail = response.json()
    assert detail["id"] == delivery["id"]
    assert json.loads(detail["payload"])["task_id"] == task["id"]


def test_webhook_delivery_retry(client):
    config = create_webhook(client)
    with client.app.state.SessionLocal() as db:
        delivery = create_webhook_delivery(db, config["id"], "task_created", json.dumps({"event": "task_created"}))
        update_webhook_delivery(db, delivery, status="failed", attempts=3, next_attempt_at=None)
        db.commit()
        delivery_id = delivery.id

    with patch("flow_app.webhooks.httpx.post", return_value=SimpleNamespace(status_code=200, text="ok")):
        response = client.post(f"/api/webhooks/{config['id']}/deliveries/{delivery_id}/retry")

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "success"
    detail = client.get(f"/api/webhooks/{config['id']}/deliveries/{delivery_id}").json()
    assert detail["status"] == "success"
    assert detail["last_response_code"] == 200


def test_sign_payload():
    payload = b'{"event":"task_created"}'
    expected = hmac.new(b"secret", payload, hashlib.sha256).hexdigest()

    assert sign_payload("secret", payload) == expected
    assert sign_payload("secret", payload) == sign_payload("secret", payload)


def test_webhook_notification_provider_send(client):
    config = create_webhook(client, events=["task_created"])
    with client.app.state.SessionLocal() as db:
        task = repo_create_task(db, TaskCreate(title="Provider task", project="default"))
        WebhookNotificationProvider().send(db, "task_created", task)
        db.commit()

    deliveries = list_deliveries(client, config["id"])
    assert len(deliveries) == 1
    assert deliveries[0]["event"] == "task_created"


def test_deliver_webhook_success(client):
    config_data = create_webhook(client)
    with client.app.state.SessionLocal() as db:
        config = get_webhook_config(db, config_data["id"])
        delivery = create_webhook_delivery(db, config.id, "task_created", '{"ok":true}')
        db.commit()
        delivery_id = delivery.id

        with patch("flow_app.webhooks.httpx.post", return_value=SimpleNamespace(status_code=200, text="accepted")) as post:
            deliver_webhook(db, delivery, config)
            db.commit()

        saved = get_webhook_delivery(db, delivery_id)
        assert saved.status == "success"
        assert saved.attempts == 0
        assert saved.next_attempt_at is None
        assert saved.last_response_code == 200
        assert saved.last_response_body == "accepted"
        assert post.call_args.kwargs["headers"]["X-Flow-Signature"] == sign_payload(config.secret, b'{"ok":true}')


def test_delivery_uses_resolved_ip(client, monkeypatch):
    config_data = create_webhook(client, url="https://example.com:8443/webhook?source=flow")
    with client.app.state.SessionLocal() as db:
        config = get_webhook_config(db, config_data["id"])
        delivery = create_webhook_delivery(db, config.id, "task_created", '{"ok":true}')
        db.commit()

        monkeypatch.setattr(
            "flow_app.webhooks.resolve_webhook_target",
            lambda url: ("93.184.216.34", url),
        )
        with patch("flow_app.webhooks.httpx.post", return_value=SimpleNamespace(status_code=200, text="accepted")) as post:
            deliver_webhook(db, delivery, config)
            db.commit()

        assert post.call_args.args[0] == "https://93.184.216.34:8443/webhook?source=flow"
        assert post.call_args.kwargs["headers"]["Host"] == "example.com"


def test_deliver_webhook_failure_retries(client):
    config_data = create_webhook(client, max_retries=3, retry_backoff_seconds=30)
    with client.app.state.SessionLocal() as db:
        config = get_webhook_config(db, config_data["id"])
        delivery = create_webhook_delivery(db, config.id, "task_created", '{"ok":false}')
        db.commit()
        delivery_id = delivery.id

        with patch("flow_app.webhooks.httpx.post", return_value=SimpleNamespace(status_code=500, text="nope")):
            deliver_webhook(db, delivery, config)
            db.commit()

        saved = get_webhook_delivery(db, delivery_id)
        assert saved.status == "retrying"
        assert saved.attempts == 1
        assert saved.next_attempt_at is not None
        assert saved.next_attempt_at > utcnow()
        assert saved.last_response_code == 500
        assert saved.last_response_body == "nope"


def test_deliver_webhook_skips_unsafe_url(client):
    config_data = create_webhook(client, max_retries=1)
    with client.app.state.SessionLocal() as db:
        config = get_webhook_config(db, config_data["id"])
        config.url = "http://127.0.0.1/webhook"
        delivery = create_webhook_delivery(db, config.id, "task_created", '{"ok":false}')
        db.commit()
        delivery_id = delivery.id

        with patch("flow_app.webhooks.httpx.post") as post:
            deliver_webhook(db, delivery, config)
            db.commit()

        saved = get_webhook_delivery(db, delivery_id)
        assert saved.status == "failed"
        assert saved.attempts == 1
        assert saved.last_response_code is None
        assert saved.last_response_body == "Webhook URL targets unacceptable address."
        post.assert_not_called()
