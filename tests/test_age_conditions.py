from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from flow_app.repository import get_task
from flow_app.rules_engine import emit_event, evaluate_conditions


def create_task(client, **overrides):
    payload = {"title": "Stale work", "status": "todo", "priority": 50, "project": "default"}
    payload.update(overrides)
    response = client.post("/api/tasks", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def create_rule(client, **overrides):
    payload = {
        "name": "Stale rule",
        "trigger": "cron",
        "conditions": "[]",
        "actions": json.dumps([{"type": "add_note", "text": "stale task"}]),
    }
    payload.update(overrides)
    response = client.post("/api/automation-rules", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_age_since_updated_gt():
    now = datetime(2026, 6, 4, 12, 0, tzinfo=timezone.utc)
    task_data = {"updated_at": now - timedelta(days=2)}

    assert evaluate_conditions(
        [{"field": "age_since_updated", "operator": "gt", "value": 86400}],
        task_data,
        now=now,
    )


def test_age_since_updated_lt():
    now = datetime(2026, 6, 4, 12, 0, tzinfo=timezone.utc)
    task_data = {"updated_at": now - timedelta(hours=1)}

    assert not evaluate_conditions(
        [{"field": "age_since_updated", "operator": "lt", "value": 3600}],
        task_data,
        now=now,
    )


def test_age_since_created_gte():
    now = datetime(2026, 6, 4, 12, 0, tzinfo=timezone.utc)
    task_data = {"created_at": now - timedelta(hours=3)}

    assert evaluate_conditions(
        [{"field": "age_since_created", "operator": "gte", "value": 7200}],
        task_data,
        now=now,
    )


def test_age_since_claimed_null():
    assert not evaluate_conditions(
        [{"field": "age_since_claimed", "operator": "gt", "value": 0}],
        {"claimed_at": None},
    )


def test_age_since_claimed_gt():
    now = datetime(2026, 6, 4, 12, 0, tzinfo=timezone.utc)
    task_data = {"claimed_at": now - timedelta(days=5, seconds=1)}

    assert evaluate_conditions(
        [{"field": "age_since_claimed", "operator": "gt", "value": 432000}],
        task_data,
        now=now,
    )


def test_stale_doing_policy_example(client):
    now = datetime(2026, 6, 4, 12, 0, tzinfo=timezone.utc)
    task = create_task(client, status="doing")
    rule = create_rule(
        client,
        name="Stale-doing-warn",
        conditions=json.dumps(
            [
                {"field": "status", "operator": "eq", "value": "doing"},
                {"field": "age_since_updated", "operator": "gt", "value": "259200"},
            ]
        ),
        actions=json.dumps(
            [
                {
                    "type": "add_note",
                    "note": "\u26a0\ufe0f This task has been in doing for 3+ days without update.",
                }
            ]
        ),
    )

    with client.app.state.SessionLocal() as db:
        task_model = get_task(db, task["id"])
        task_model.updated_at = now - timedelta(days=4)
        db.commit()

        result = emit_event(
            db,
            "cron",
            data={"rule_id": rule["id"], "rule_name": rule["name"]},
            rule_id=rule["id"],
            dry_run=True,
        )

    assert result["dry_run"] is True
    assert [match["task_id"] for match in result["matches"]] == [task["id"]]


def test_dry_run_emit_event(client):
    task = create_task(client, priority=100)
    rule = create_rule(
        client,
        trigger="task_created",
        conditions=json.dumps([{"field": "priority", "operator": "gte", "value": 90}]),
        actions=json.dumps([{"type": "add_note", "text": "would add note"}]),
    )

    with client.app.state.SessionLocal() as db:
        result = emit_event(db, "task_created", task_id=task["id"], rule_id=rule["id"], dry_run=True)
        db.commit()

    assert result["dry_run"] is True
    assert result["matches"][0]["actions"] == [{"type": "add_note", "text": "would add note"}]

    updated_task = client.get(f"/api/tasks/{task['id']}").json()
    assert updated_task["notes"] == []
    assert client.get(f"/api/automation-rules/{rule['id']}").json()["last_run_at"] is None


def test_dry_run_rest_endpoint(client):
    now = datetime(2026, 6, 4, 12, 0, tzinfo=timezone.utc)
    task = create_task(client, status="doing")
    rule = create_rule(
        client,
        conditions=json.dumps(
            [
                {"field": "status", "operator": "eq", "value": "doing"},
                {"field": "age_since_updated", "operator": "gt", "value": 86400},
            ]
        ),
        actions=json.dumps([{"type": "add_note", "text": "REST dry-run note"}]),
    )

    with client.app.state.SessionLocal() as db:
        task_model = get_task(db, task["id"])
        task_model.updated_at = now - timedelta(days=2)
        db.commit()

    response = client.post(f"/api/automation-rules/{rule['id']}/dry-run")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["dry_run"] is True
    assert body["matches"][0]["task_id"] == task["id"]
    assert body["matches"][0]["actions"] == [{"type": "add_note", "text": "REST dry-run note"}]
    assert client.get(f"/api/tasks/{task['id']}").json()["notes"] == []


def test_age_condition_with_status():
    now = datetime(2026, 6, 4, 12, 0, tzinfo=timezone.utc)
    task_data = {"status": "doing", "updated_at": now - timedelta(days=2)}

    assert evaluate_conditions(
        [
            {"field": "status", "operator": "eq", "value": "doing"},
            {"field": "age_since_updated", "operator": "gt", "value": 86400},
        ],
        task_data,
        now=now,
    )


def test_age_unknown_field_ignored():
    assert not evaluate_conditions(
        [{"field": "age_since_unknown", "operator": "gt", "value": 0}],
        {"updated_at": datetime.now(timezone.utc)},
    )
