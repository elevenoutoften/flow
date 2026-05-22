from __future__ import annotations

import json
from types import SimpleNamespace

from flow_app.models import ApiKeyRole
from flow_app.repository import get_task
from flow_app.rules_engine import emit_event
from flow_app.rules_engine import evaluate_conditions
from flow_app.security import Actor


def rpc(client, method, params=None, request_id=1):
    return client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}},
    )


def create_task(client, **overrides):
    payload = {"title": "Automate backend work", "status": "todo", "priority": 75, "project": "default"}
    payload.update(overrides)
    response = client.post("/api/tasks", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def create_rule(client, **overrides):
    payload = {
        "name": "High priority created",
        "trigger": "task_created",
        "conditions": json.dumps([{"field": "priority", "operator": "gte", "value": 70}]),
        "actions": json.dumps([{"type": "notify", "channel": "ops"}]),
    }
    payload.update(overrides)
    response = client.post("/api/automation-rules", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def bearer_headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


def test_rule_crud_create_list_get_update_enable_disable(client):
    rule = create_rule(client, name="  Rule one  ", enabled=True)

    assert rule["id"] == "rule_000001"
    assert rule["name"] == "Rule one"
    assert rule["enabled"] is True

    listed = client.get("/api/automation-rules").json()
    assert [item["id"] for item in listed] == [rule["id"]]

    fetched = client.get(f"/api/automation-rules/{rule['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "Rule one"

    updated = client.patch(
        f"/api/automation-rules/{rule['id']}",
        json={"enabled": False, "priority": 10, "description": "disabled"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["enabled"] is False
    assert updated.json()["priority"] == 10
    assert client.get("/api/automation-rules?enabled_only=true").json() == []


def test_condition_evaluation_operators():
    data = {
        "status": "todo",
        "project": "default",
        "priority": 80,
        "title": "Ship API",
        "assignee": None,
        "latest_handoff": {"id": "handoff_000001"},
    }

    assert evaluate_conditions([{"field": "status", "operator": "eq", "value": "todo"}], data)
    assert evaluate_conditions([{"field": "status", "operator": "ne", "value": "done"}], data)
    assert evaluate_conditions([{"field": "status", "operator": "in", "value": ["todo", "doing"]}], data)
    assert evaluate_conditions([{"field": "title", "operator": "contains", "value": "api"}], data)
    assert evaluate_conditions([{"field": "priority", "operator": "gt", "value": 70}], data)
    assert evaluate_conditions([{"field": "priority", "operator": "lt", "value": 90}], data)
    assert evaluate_conditions([{"field": "priority", "operator": "gte", "value": 80}], data)
    assert evaluate_conditions([{"field": "priority", "operator": "lte", "value": 80}], data)
    assert evaluate_conditions([{"field": "latest_handoff", "operator": "exists"}], data)
    assert not evaluate_conditions([{"field": "assignee", "operator": "exists"}], data)
    assert not evaluate_conditions([{"field": "unknown", "operator": "eq", "value": "todo"}], data)
    assert not evaluate_conditions([{"field": "status", "operator": "unknown", "value": "todo"}], data)


def test_evaluate_endpoint_matches_rules_in_priority_order_and_updates_last_run(client):
    low = create_rule(client, name="Low", priority=10)
    high = create_rule(client, name="High", priority=90)
    task = create_task(client, priority=80)

    response = client.post("/api/automation-rules/evaluate", json={"trigger": "task_created", "task_id": task["id"]})

    assert response.status_code == 200, response.text
    matches = response.json()["matches"]
    assert [match["rule_id"] for match in matches] == [high["id"], low["id"]]
    assert client.get(f"/api/automation-rules/{high['id']}").json()["last_run_at"] is not None


def test_event_emission_respects_disabled_rules(client):
    rule = create_rule(client, enabled=False)

    create_task(client, priority=100)

    assert client.get(f"/api/automation-rules/{rule['id']}").json()["last_run_at"] is None


def test_task_lifecycle_events_update_matching_rule_last_run(client):
    claimed = create_rule(
        client,
        name="Claimed",
        trigger="task_claimed",
        conditions=json.dumps([{"field": "assignee", "operator": "eq", "value": "test-admin"}]),
    )
    blocked = create_rule(
        client,
        name="Blocked",
        trigger="task_blocked",
        conditions=json.dumps([{"field": "human_required", "operator": "eq", "value": True}]),
    )
    task = create_task(client, status="todo")

    assert client.post(f"/api/tasks/{task['id']}/claim", json={}).status_code == 200
    assert client.patch(f"/api/tasks/{task['id']}", json={"human_required": True}).status_code == 200

    assert client.get(f"/api/automation-rules/{claimed['id']}").json()["last_run_at"] is not None
    assert client.get(f"/api/automation-rules/{blocked['id']}").json()["last_run_at"] is not None


def test_rule_permissions_read_only_can_list_implementer_cannot_create(client, no_auth_client):
    create_rule(client)
    reader = client.post("/api/api-keys", json={"name": "reader", "role": "read_only"}).json()["api_key"]
    implementer = client.post("/api/api-keys", json={"name": "impl", "role": "implementer"}).json()["api_key"]

    read_response = no_auth_client.get("/api/automation-rules", headers=bearer_headers(reader))
    assert read_response.status_code == 200
    assert read_response.json()[0]["name"] == "High priority created"

    create_response = no_auth_client.post(
        "/api/automation-rules",
        json={"name": "Denied", "trigger": "task_created"},
        headers=bearer_headers(implementer),
    )
    assert create_response.status_code == 403


def test_mcp_automation_rule_tools(client):
    created = rpc(
        client,
        "tools/call",
        {
            "name": "flow_create_automation_rule",
            "arguments": {
                "name": "MCP rule",
                "trigger": "task_created",
                "conditions": json.dumps([{"field": "project", "operator": "eq", "value": "default"}]),
                "actions": json.dumps([{"type": "dispatch"}]),
            },
        },
    ).json()["result"]["structuredContent"]["rule"]

    listed = rpc(client, "tools/call", {"name": "flow_list_automation_rules", "arguments": {}}).json()
    assert listed["result"]["structuredContent"]["count"] == 1

    fetched = rpc(
        client,
        "tools/call",
        {"name": "flow_get_automation_rule", "arguments": {"rule_id": created["id"]}},
    ).json()["result"]["structuredContent"]["rule"]
    assert fetched["name"] == "MCP rule"

    updated = rpc(
        client,
        "tools/call",
        {"name": "flow_update_automation_rule", "arguments": {"rule_id": created["id"], "priority": 99}},
    ).json()["result"]["structuredContent"]["rule"]
    assert updated["priority"] == 99

    task = create_task(client)
    evaluated = rpc(
        client,
        "tools/call",
        {"name": "flow_evaluate_rules", "arguments": {"trigger": "task_created", "task_id": task["id"]}},
    ).json()["result"]["structuredContent"]
    assert evaluated["count"] == 1
    assert evaluated["matches"][0]["rule_id"] == created["id"]


def test_rule_claim_action_executes_and_is_idempotent(client):
    task = create_task(client, status="todo")
    create_rule(
        client,
        actions=json.dumps([{"type": "claim", "assignee": "automation-agent"}]),
    )

    evaluated = client.post("/api/automation-rules/evaluate", json={"trigger": "task_created", "task_id": task["id"]})
    assert evaluated.status_code == 200, evaluated.text
    result = evaluated.json()["matches"][0]["action_results"][0]
    assert result["success"] is True

    updated = client.get(f"/api/tasks/{task['id']}").json()
    assert updated["assignee"] == "automation-agent"
    assert updated["status"] == "doing"

    evaluated_again = client.post("/api/automation-rules/evaluate", json={"trigger": "task_created", "task_id": task["id"]})
    retry_result = evaluated_again.json()["matches"][0]["action_results"][0]
    assert retry_result["success"] is False
    assert "already claimed" in retry_result["message"]


def test_rule_move_action_executes_and_rejects_invalid_transition(client):
    task = create_task(client, status="todo")
    create_rule(client, actions=json.dumps([{"type": "move", "status": "review"}]))

    evaluated = client.post("/api/automation-rules/evaluate", json={"trigger": "task_created", "task_id": task["id"]})
    assert evaluated.status_code == 200, evaluated.text
    result = evaluated.json()["matches"][0]["action_results"][0]
    assert result["success"] is True
    assert client.get(f"/api/tasks/{task['id']}").json()["status"] == "review"

    with client.app.state.SessionLocal() as db:
        task_model = get_task(db, task["id"])
        assert task_model is not None
        task_model.status = "todo"
        db.commit()
        matches = emit_event(
            db,
            "task_created",
            task_id=task["id"],
            actor=Actor(name="impl", role=ApiKeyRole.implementer, source="test"),
        )
        db.commit()

    invalid_result = matches[0]["action_results"][0]
    assert invalid_result["success"] is False
    assert "cannot move task from todo to review" in invalid_result["message"]


def test_rule_add_note_and_notify_actions_execute_once(client):
    task = create_task(client)
    create_rule(
        client,
        actions=json.dumps(
            [
                {"type": "add_note", "text": "Escalated by automation."},
                {"type": "notify", "channel": "ops", "message": "High priority task."},
            ]
        ),
    )

    response = client.post("/api/automation-rules/evaluate", json={"trigger": "task_created", "task_id": task["id"]})
    assert response.status_code == 200, response.text
    results = response.json()["matches"][0]["action_results"]
    assert [result["success"] for result in results] == [True, True]

    response = client.post("/api/automation-rules/evaluate", json={"trigger": "task_created", "task_id": task["id"]})
    retry_results = response.json()["matches"][0]["action_results"]
    assert retry_results[0]["details"]["idempotent"] is True
    assert retry_results[1]["details"]["idempotent"] is True

    notes = client.get(f"/api/tasks/{task['id']}").json()["notes"]
    bodies = [note["body"] for note in notes]
    assert bodies.count("Escalated by automation.") == 1
    assert bodies.count("[notification:ops] High priority task.") == 1


def test_rule_spawn_action_dispatches_agent_and_reports_failures(client, monkeypatch):
    task = create_task(client)
    agent = client.post(
        "/api/agents",
        json={"name": "automation-agent", "capabilities": "backend", "command": "echo hello"},
    ).json()
    calls = []

    def fake_dispatch_one(db, agent_model, task_model, api_key, base_url):
        calls.append((agent_model.id, task_model.id, api_key, base_url))
        return SimpleNamespace(id="run_automation")

    monkeypatch.setattr("flow_app.dispatcher.dispatch_one", fake_dispatch_one)
    create_rule(client, actions=json.dumps([{"type": "spawn", "agent_id": agent["id"], "api_key": "key", "base_url": "url"}]))

    response = client.post("/api/automation-rules/evaluate", json={"trigger": "task_created", "task_id": task["id"]})
    result = response.json()["matches"][0]["action_results"][0]
    assert result["success"] is True
    assert result["details"]["run_id"] == "run_automation"
    assert calls == [(agent["id"], task["id"], "key", "url")]

    missing = create_task(client, title="No matching agent")
    client.patch(f"/api/automation-rules/{client.get('/api/automation-rules').json()[0]['id']}", json={"actions": json.dumps([{"type": "spawn", "agent_name": "missing"}])})
    failure = client.post("/api/automation-rules/evaluate", json={"trigger": "task_created", "task_id": missing["id"]})
    failure_result = failure.json()["matches"][0]["action_results"][0]
    assert failure_result["success"] is False
    assert "registered" in failure_result["message"]


def test_rule_webhook_action_creates_delivery(client):
    task = create_task(client)
    webhook = client.post(
        "/api/webhooks",
        json={"name": "Rule hook", "url": "https://example.com/hook", "events": ["task_created"], "project": "*"},
    ).json()
    create_rule(client, actions=json.dumps([{"type": "webhook", "event": "task_created"}]))

    response = client.post("/api/automation-rules/evaluate", json={"trigger": "task_created", "task_id": task["id"]})
    result = response.json()["matches"][0]["action_results"][0]
    assert result["success"] is True

    deliveries = client.get(f"/api/webhooks/{webhook['id']}/deliveries").json()
    assert len(deliveries) == 1
    assert deliveries[0]["event"] == "task_created"

    retry = client.post("/api/automation-rules/evaluate", json={"trigger": "task_created", "task_id": task["id"]})
    retry_result = retry.json()["matches"][0]["action_results"][0]
    assert retry_result["details"]["idempotent"] is True
    assert len(client.get(f"/api/webhooks/{webhook['id']}/deliveries").json()) == 1
