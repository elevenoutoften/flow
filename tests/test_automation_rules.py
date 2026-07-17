from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy.orm import Session

from flow_app.models import ApiKeyRole, AutomationRule
from flow_app.models import Task, utcnow
from flow_app.notifications import NotificationProvider, RulesNotifyProvider, register_notification_provider
from flow_app.notifications import _registry as notification_registry
from flow_app.repository import add_note, get_task
from flow_app.runner import _cron_config_matches, _run_cron_rules
from flow_app.rules_engine import emit_event
from flow_app.rules_engine import evaluate_conditions
from flow_app.rules_engine import set_notify_provider
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


class RecordingNotificationProvider(NotificationProvider):
    def __init__(self) -> None:
        self.calls = []

    def send(self, db: Session, event: str, task: Task, changes: dict | None = None) -> None:
        self.calls.append({"event": event, "task_id": task.id, "changes": changes})


class FailingNotificationProvider(NotificationProvider):
    def send(self, db: Session, event: str, task: Task, changes: dict | None = None) -> None:
        raise RuntimeError("provider unavailable")


def reset_notify_provider() -> None:
    set_notify_provider(None)
    notification_registry.clear()


def test_rule_crud_create_list_get_update_enable_disable(client):
    rule = create_rule(client, name="  Rule one  ", enabled=True)

    assert rule["id"] == "rule_000001"
    assert rule["name"] == "Rule one"
    assert rule["enabled"] is True

    listed = client.get("/api/automation-rules").json()["items"]
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
    assert client.get("/api/automation-rules?enabled_only=true").json()["items"] == []


def test_rule_api_redacts_secret_action_values(client):
    rule = create_rule(
        client,
        actions=json.dumps(
            [
                {
                    "type": "dispatch",
                    "api_key": "flow_raw_secret",
                    "secret": "raw-secret",
                    "token": "env:FLOW_SAFE_TOKEN_REF",
                    "nested": {"secret": "nested-secret"},
                }
            ]
        ),
    )

    actions = json.loads(rule["actions"])
    assert actions[0]["api_key"] == "***"
    assert actions[0]["secret"] == "***"
    assert actions[0]["token"] == "env:FLOW_SAFE_TOKEN_REF"
    assert actions[0]["nested"]["secret"] == "***"


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


def test_create_rule_with_unknown_field(client):
    response = client.post(
        "/api/automation-rules",
        json={
            "name": "Bad field",
            "trigger": "cron",
            "conditions": json.dumps([{"field": "unknown_field", "operator": "eq", "value": "todo"}]),
            "actions": json.dumps([{"type": "add_note", "body": "bad"}]),
        },
    )

    assert response.status_code == 422
    assert "unknown condition field" in response.text.lower()


def test_create_rule_with_unknown_operator(client):
    response = client.post(
        "/api/automation-rules",
        json={
            "name": "Bad operator",
            "trigger": "cron",
            "conditions": json.dumps([{"field": "status", "operator": "matches", "value": "todo"}]),
            "actions": json.dumps([{"type": "add_note", "body": "bad"}]),
        },
    )

    assert response.status_code == 422
    assert "unknown operator" in response.text.lower()


def test_create_rule_with_age_non_numeric(client):
    response = client.post(
        "/api/automation-rules",
        json={
            "name": "Bad age",
            "trigger": "cron",
            "conditions": json.dumps([{"field": "age_since_updated", "operator": "gt", "value": "old"}]),
            "actions": json.dumps([{"type": "add_note", "body": "bad"}]),
        },
    )

    assert response.status_code == 422
    assert "age" in response.text.lower() or "value" in response.text.lower()


def test_create_rule_with_valid_conditions(client):
    response = client.post(
        "/api/automation-rules",
        json={
            "name": "Valid conditions",
            "trigger": "cron",
            "conditions": json.dumps(
                [
                    {"field": "status", "operator": "eq", "value": "todo"},
                    {"field": "age_since_updated", "operator": "gt", "value": 60},
                ]
            ),
            "actions": json.dumps([{"type": "add_note", "body": "valid"}]),
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["name"] == "Valid conditions"


def test_update_rule_with_invalid_conditions(client):
    rule = create_rule(client)

    response = client.patch(
        f"/api/automation-rules/{rule['id']}",
        json={"conditions": json.dumps([{"field": "unknown_field", "operator": "eq", "value": "todo"}])},
    )

    assert response.status_code == 422
    assert "unknown condition field" in response.text.lower()


def test_dry_run_with_invalid_conditions_in_db(client):
    now = utcnow()
    with client.app.state.SessionLocal() as db:
        db.add(
            AutomationRule(
                id="rule_bad_conditions",
                name="Bad stored conditions",
                description="",
                enabled=1,
                priority=50,
                trigger="cron",
                trigger_config="",
                conditions=json.dumps([{"field": "unknown_field", "operator": "eq", "value": "todo"}]),
                actions=json.dumps([{"type": "add_note", "body": "bad"}]),
                last_run_at=None,
                created_at=now,
                updated_at=now,
            )
        )
        db.commit()

    response = client.post("/api/automation-rules/dry-run", json={"trigger": "cron"})

    assert response.status_code == 200, response.text
    invalid_conditions = response.json()["invalid_conditions"]
    assert invalid_conditions[0]["rule_id"] == "rule_bad_conditions"
    assert invalid_conditions[0]["errors"][0]["field"] == "field"
    assert "unknown condition field" in invalid_conditions[0]["errors"][0]["message"].lower()


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


def test_match_rules_respects_trigger_config_project_filter(client):
    default_task = create_task(client, priority=100, project="default")
    other_task = create_task(client, priority=100, project="other")
    rule = create_rule(
        client,
        trigger_config=json.dumps({"project": "other"}),
        actions=json.dumps([{"type": "add_note", "text": "project matched"}]),
    )

    with client.app.state.SessionLocal() as db:
        default_matches = emit_event(db, "task_created", task_id=default_task["id"])
        other_matches = emit_event(db, "task_created", task_id=other_task["id"])
        db.commit()

    assert default_matches == []
    assert [match["rule_id"] for match in other_matches] == [rule["id"]]


def test_match_rules_respects_trigger_config_from_status_filter(client):
    """from_status is checked against the event's previous status (data),
    not the task's current status (which has already changed)."""
    task = create_task(client, priority=100, status="todo")
    rule = create_rule(
        client,
        trigger="task_moved",
        trigger_config=json.dumps({"from_status": "todo"}),
        actions=json.dumps([{"type": "add_note", "text": "from status matched"}]),
    )

    with client.app.state.SessionLocal() as db:
        # Simulate a move from todo → review: event data carries from_status=todo.
        matches = emit_event(
            db,
            "task_moved",
            task_id=task["id"],
            data={"from_status": "todo", "to_status": "review"},
        )
        db.commit()

    assert [match["rule_id"] for match in matches] == [rule["id"]]


def test_match_rules_from_status_non_matching(client):
    """A rule with from_status=doing must not fire for a todo→review transition."""
    task = create_task(client, priority=100, status="todo")
    create_rule(
        client,
        trigger="task_moved",
        trigger_config=json.dumps({"from_status": "doing"}),
        actions=json.dumps([{"type": "add_note", "text": "should not match"}]),
    )

    with client.app.state.SessionLocal() as db:
        matches = emit_event(
            db,
            "task_moved",
            task_id=task["id"],
            data={"from_status": "todo", "to_status": "review"},
        )
        db.commit()

    assert matches == []


def test_api_move_triggers_from_status_rule(client):
    """End-to-end: moving a task via the API triggers a from_status rule.

    Creates a rule that only fires on todo→review transitions, moves a task
    from todo to review via PATCH, and verifies the rule action (add_note)
    is executed against the task.
    """
    task = create_task(client, priority=100, status="todo")
    rule = create_rule(
        client,
        name="Todo to review alert",
        trigger="task_moved",
        trigger_config=json.dumps({"from_status": "todo", "to_status": "review"}),
        conditions="[]",
        actions=json.dumps([{"type": "add_note", "text": "Moved from todo to review"}]),
    )

    # Move via the API POST /move (uses TaskService.move_task which emits
    # from_status/to_status in event data).
    response = client.post(
        f"/api/tasks/{task['id']}/move",
        json={"status": "review"},
    )
    assert response.status_code == 200, response.text

    # Verify the rule fired and the note was added.
    notes = client.get(f"/api/tasks/{task['id']}").json()["notes"]
    assert any("Moved from todo to review" in n["body"] for n in notes)

    # Moving a different task from doing→review must NOT trigger the rule.
    other = create_task(client, priority=100, status="doing")
    client.post(f"/api/tasks/{other['id']}/move", json={"status": "review"})
    other_notes = client.get(f"/api/tasks/{other['id']}").json()["notes"]
    assert not any("Moved from todo to review" in n["body"] for n in other_notes)


def test_cron_config_matches_standard_cron_string():
    assert _cron_config_matches(
        json.dumps({"cron": "0 9 * * *"}),
        datetime(2026, 5, 25, 9, 0, tzinfo=timezone.utc),
    )
    assert not _cron_config_matches(
        json.dumps({"cron": "0 9 * * *"}),
        datetime(2026, 5, 25, 9, 1, tzinfo=timezone.utc),
    )


def test_cron_config_matches_every_five_minutes_cron_string():
    assert _cron_config_matches(
        json.dumps({"cron": "*/5 * * * *"}),
        datetime(2026, 5, 25, 9, 10, tzinfo=timezone.utc),
    )
    assert not _cron_config_matches(
        json.dumps({"cron": "*/5 * * * *"}),
        datetime(2026, 5, 25, 9, 11, tzinfo=timezone.utc),
    )


def test_cron_config_invalid_cron_string_fails_closed():
    """An invalid cron string (wrong number of fields) must fail closed —
    the rule does not match (is skipped), not block other rules."""
    assert not _cron_config_matches(
        json.dumps({"cron": "0 9 *"}),
        datetime(2026, 5, 25, 9, 11, tzinfo=timezone.utc),
    )


def test_cron_weekday_sunday_zero():
    """Standard cron: 0 = Sunday. 2026-05-31 is a Sunday."""
    sunday = datetime(2026, 5, 31, 9, 0, tzinfo=timezone.utc)
    assert _cron_config_matches(json.dumps({"cron": "0 9 * * 0"}), sunday)
    assert not _cron_config_matches(json.dumps({"cron": "0 9 * * 1"}), sunday)


def test_cron_weekday_sunday_seven():
    """Standard cron: 7 is also Sunday."""
    sunday = datetime(2026, 5, 31, 9, 0, tzinfo=timezone.utc)
    assert _cron_config_matches(json.dumps({"cron": "0 9 * * 7"}), sunday)


def test_cron_weekday_monday_one():
    """Standard cron: 1 = Monday. 2026-05-25 is a Monday."""
    monday = datetime(2026, 5, 25, 9, 0, tzinfo=timezone.utc)
    assert _cron_config_matches(json.dumps({"cron": "0 9 * * 1"}), monday)
    assert not _cron_config_matches(json.dumps({"cron": "0 9 * * 0"}), monday)


def test_cron_weekday_saturday_six():
    """Standard cron: 6 = Saturday. 2026-05-30 is a Saturday."""
    saturday = datetime(2026, 5, 30, 9, 0, tzinfo=timezone.utc)
    assert _cron_config_matches(json.dumps({"cron": "0 9 * * 6"}), saturday)
    assert not _cron_config_matches(json.dumps({"cron": "0 9 * * 0"}), saturday)


def test_cron_weekday_legacy_format_sunday():
    """Legacy {day_of_week} format also uses standard cron numbering (0=Sunday)."""
    sunday = datetime(2026, 5, 31, 9, 0, tzinfo=timezone.utc)
    assert _cron_config_matches(
        json.dumps({"minute": "0", "hour": "9", "day_of_week": "0"}),
        sunday,
    )
    assert not _cron_config_matches(
        json.dumps({"minute": "0", "hour": "9", "day_of_week": "1"}),
        sunday,
    )


def test_cron_validate_rejects_day_of_week_8():
    """Day-of-week 8 is invalid (valid range 0-7)."""
    from flow_app.cron import validate_cron_string
    error = validate_cron_string("0 9 * * 8")
    assert error is not None
    assert "day_of_week" in error.lower()


def test_cron_validate_accepts_day_of_week_7():
    """Day-of-week 7 is valid (equivalent to 0=Sunday)."""
    from flow_app.cron import validate_cron_string
    error = validate_cron_string("0 9 * * 7")
    assert error is None


def test_cron_weekday_range_monday_through_friday():
    """AC #1-3: validate_cron_string('0 9 * * 1-5') is None; matches Monday,
    does NOT match Saturday.  Exercises the production cron_string_matches, not
    a field-level helper."""
    from flow_app.cron import cron_string_matches, validate_cron_string

    assert validate_cron_string("0 9 * * 1-5") is None
    monday = datetime(2026, 5, 25, 9, 0)  # Monday
    saturday = datetime(2026, 5, 30, 9, 0)  # Saturday
    assert cron_string_matches("0 9 * * 1-5", monday) is True
    assert cron_string_matches("0 9 * * 1-5", saturday) is False


def test_cron_weekday_comma_list_through_cron_string_matches():
    """AC #4: Exercise day-of-week comma lists through cron_string_matches,
    not cron_field_matches.  '1,3,5' matches Mon/Wed/Fri but not Tue/Thu."""
    from flow_app.cron import cron_string_matches

    monday = datetime(2026, 5, 25, 9, 0)    # Monday
    tuesday = datetime(2026, 5, 26, 9, 0)   # Tuesday
    wednesday = datetime(2026, 5, 27, 9, 0) # Wednesday
    friday = datetime(2026, 5, 29, 9, 0)    # Friday
    assert cron_string_matches("0 9 * * 1,3,5", monday) is True
    assert cron_string_matches("0 9 * * 1,3,5", wednesday) is True
    assert cron_string_matches("0 9 * * 1,3,5", friday) is True
    assert cron_string_matches("0 9 * * 1,3,5", tuesday) is False


def test_cron_weekday_both_zero_and_seven_match_sunday():
    """AC #5: Assert both 0 and 7 match Sunday through cron_string_matches."""
    from flow_app.cron import cron_string_matches

    sunday = datetime(2026, 5, 31, 9, 0)  # Sunday
    assert cron_string_matches("0 9 * * 0", sunday) is True
    assert cron_string_matches("0 9 * * 7", sunday) is True


def test_cron_config_matches_empty_cron_string_is_false():
    """AC #6: trigger_config with json.dumps({'cron': ''}) must return False
    from _cron_config_matches.  Malformed and wrong-field-count remain fail-closed."""
    from flow_app.runner import _cron_config_matches

    now = datetime(2026, 5, 25, 9, 0)
    assert _cron_config_matches(json.dumps({"cron": ""}), now) is False
    # Malformed JSON fail-closed
    assert _cron_config_matches("not json", now) is False
    # Non-dict fail-closed
    assert _cron_config_matches(json.dumps(["cron", "0 9 * * *"]), now) is False
    # Wrong field count fail-closed
    assert _cron_config_matches(json.dumps({"cron": "0 9 *"}), now) is False


def test_cron_weekday_range_5_to_7_matches_fri_sat_sun():
    """AC #1-2: validate_cron_string('0 9 * * 5-7') returns no error;
    cron_string_matches for 5-7 is true on Friday, Saturday, and Sunday and
    false on Thursday."""
    from flow_app.cron import cron_string_matches, validate_cron_string

    assert validate_cron_string("0 9 * * 5-7") is None
    friday = datetime(2026, 5, 29, 9, 0)    # Friday
    saturday = datetime(2026, 5, 30, 9, 0)  # Saturday
    sunday = datetime(2026, 5, 31, 9, 0)    # Sunday
    thursday = datetime(2026, 5, 28, 9, 0) # Thursday
    assert cron_string_matches("0 9 * * 5-7", friday) is True
    assert cron_string_matches("0 9 * * 5-7", saturday) is True
    assert cron_string_matches("0 9 * * 5-7", sunday) is True
    assert cron_string_matches("0 9 * * 5-7", thursday) is False


def test_cron_weekday_range_0_to_7_matches_every_day():
    """AC #3: cron_string_matches for 0-7 is true on every weekday."""
    from flow_app.cron import cron_string_matches

    for day in range(7):
        # 2026-05-25 is Monday (weekday=0), so day N = May 25 + N
        dt = datetime(2026, 5, 25 + day, 9, 0)
        assert cron_string_matches("0 9 * * 0-7", dt) is True, (
            f"0-7 should match every day; failed for day offset {day}"
        )


def test_cron_weekday_range_6_to_7_matches_sat_and_sun():
    """AC #4: cron_string_matches for 6-7 is true on Saturday and Sunday."""
    from flow_app.cron import cron_string_matches

    saturday = datetime(2026, 5, 30, 9, 0)  # Saturday
    sunday = datetime(2026, 5, 31, 9, 0)    # Sunday
    friday = datetime(2026, 5, 29, 9, 0)    # Friday
    assert cron_string_matches("0 9 * * 6-7", saturday) is True
    assert cron_string_matches("0 9 * * 6-7", sunday) is True
    assert cron_string_matches("0 9 * * 6-7", friday) is False


def test_create_cron_rule_rejects_invalid_cron_expression(client):
    response = client.post(
        "/api/automation-rules",
        json={
            "name": "Bad cron",
            "trigger": "cron",
            "trigger_config": json.dumps({"cron": "0 9 *"}),
            "conditions": "[]",
            "actions": "[]",
        },
    )

    assert response.status_code == 422
    assert "5 fields" in response.text


def test_update_cron_rule_rejects_invalid_cron_expression(client):
    rule = create_rule(
        client,
        name="Cron rule",
        trigger="cron",
        trigger_config=json.dumps({"cron": "0 9 * * *"}),
        conditions="[]",
        actions="[]",
    )

    response = client.patch(
        f"/api/automation-rules/{rule['id']}",
        json={"trigger_config": json.dumps({"cron": "0 9 *"})},
    )

    assert response.status_code == 422
    assert "5 fields" in response.text


def test_cron_rule_does_not_fire_twice_in_same_minute(client, monkeypatch):
    now = datetime(2026, 5, 25, 14, 30, 15, tzinfo=timezone.utc)
    rule = create_rule(client, name="Cron once", trigger="cron", conditions="[]", actions="[]")
    monkeypatch.setattr("flow_app.runner.utcnow", lambda: now)

    with client.app.state.SessionLocal() as db:
        rule_model = db.get(AutomationRule, rule["id"])
        last_run_at = now.replace(second=1)
        rule_model.last_run_at = last_run_at
        db.commit()

        assert _run_cron_rules(db, dry_run=False) == 0

        db.refresh(rule_model)
        assert rule_model.last_run_at.replace(tzinfo=timezone.utc) == last_run_at


def test_cron_rule_fires_when_last_run_was_previous_minute(client, monkeypatch):
    now = datetime(2026, 5, 25, 14, 30, 15, tzinfo=timezone.utc)
    rule = create_rule(client, name="Cron next minute", trigger="cron", conditions="[]", actions="[]")
    monkeypatch.setattr("flow_app.runner.utcnow", lambda: now)

    with client.app.state.SessionLocal() as db:
        rule_model = db.get(AutomationRule, rule["id"])
        previous_run_at = now - timedelta(minutes=1)
        rule_model.last_run_at = previous_run_at
        db.commit()

        assert _run_cron_rules(db, dry_run=False) == 1

        db.refresh(rule_model)
        assert rule_model.last_run_at is not None
        assert rule_model.last_run_at.replace(tzinfo=timezone.utc) != previous_run_at


def test_age_condition_updated_at(client, monkeypatch):
    now = datetime(2026, 5, 25, 14, 30, 15, tzinfo=timezone.utc)
    monkeypatch.setattr("flow_app.runner.utcnow", lambda: now)
    monkeypatch.setattr("flow_app.rules_engine.utcnow", lambda: now)
    task = create_task(client, status="todo")
    rule = create_rule(
        client,
        name="Stale updated",
        trigger="cron",
        conditions=json.dumps([{"field": "age_since_updated", "operator": "gt", "value": 604800}]),
        actions=json.dumps([{"type": "add_note", "body": "stale"}]),
    )

    with client.app.state.SessionLocal() as db:
        task_model = get_task(db, task["id"])
        task_model.updated_at = now - timedelta(days=8)
        db.commit()

    response = client.post("/api/automation-rules/dry-run", json={"rule_id": rule["id"]})
    assert response.status_code == 200, response.text
    matches = response.json()["matches"]
    assert [match["task_id"] for match in matches] == [task["id"]]


def test_age_condition_created_at(client, monkeypatch):
    now = datetime(2026, 5, 25, 14, 30, 15, tzinfo=timezone.utc)
    monkeypatch.setattr("flow_app.runner.utcnow", lambda: now)
    monkeypatch.setattr("flow_app.rules_engine.utcnow", lambda: now)
    stale = create_task(client, status="todo", title="old")
    fresh = create_task(client, status="todo", title="new")
    create_rule(
        client,
        name="Old created",
        trigger="cron",
        conditions=json.dumps([{"field": "age_since_created", "operator": "gt", "value": 172800}]),
        actions=json.dumps([{"type": "add_note", "body": "old"}]),
    )

    with client.app.state.SessionLocal() as db:
        get_task(db, stale["id"]).created_at = now - timedelta(days=3)
        get_task(db, fresh["id"]).created_at = now - timedelta(hours=1)
        db.commit()

    response = client.post("/api/automation-rules/dry-run", json={"trigger": "cron"})
    assert response.status_code == 200, response.text
    assert [match["task_id"] for match in response.json()["matches"]] == [stale["id"]]


def test_age_condition_claimed_at(client, monkeypatch):
    now = datetime(2026, 5, 25, 14, 30, 15, tzinfo=timezone.utc)
    monkeypatch.setattr("flow_app.runner.utcnow", lambda: now)
    monkeypatch.setattr("flow_app.rules_engine.utcnow", lambda: now)
    task = create_task(client, status="doing", assignee="agent-one")
    create_rule(
        client,
        name="Old claim",
        trigger="cron",
        conditions=json.dumps([{"field": "age_since_claimed", "operator": "gt", "value": 172800}]),
        actions=json.dumps([{"type": "add_note", "body": "old claim"}]),
    )

    with client.app.state.SessionLocal() as db:
        task_model = get_task(db, task["id"])
        note = add_note(db, task_model, "claimed by agent-one", author="agent-one")
        note.created_at = now - timedelta(days=3)
        db.commit()

    response = client.post("/api/automation-rules/dry-run", json={"trigger": "cron"})
    assert response.status_code == 200, response.text
    assert [match["task_id"] for match in response.json()["matches"]] == [task["id"]]


def test_age_condition_not_claimed(client, monkeypatch):
    now = datetime(2026, 5, 25, 14, 30, 15, tzinfo=timezone.utc)
    monkeypatch.setattr("flow_app.runner.utcnow", lambda: now)
    monkeypatch.setattr("flow_app.rules_engine.utcnow", lambda: now)
    create_task(client, status="todo")
    create_rule(
        client,
        name="No claim",
        trigger="cron",
        conditions=json.dumps([{"field": "age_since_claimed", "operator": "gt", "value": 1}]),
        actions=json.dumps([{"type": "add_note", "body": "old claim"}]),
    )

    response = client.post("/api/automation-rules/dry-run", json={"trigger": "cron"})
    assert response.status_code == 200, response.text
    assert response.json()["matches"] == []


def test_age_condition_malformed(client):
    """Creating a rule with a non-numeric age value should return a validation error."""
    response = client.post(
        "/api/automation-rules",
        json={
            "name": "Bad age",
            "trigger": "cron",
            "conditions": json.dumps([{"field": "age_since_updated", "operator": "gt", "value": "old"}]),
            "actions": json.dumps([{"type": "add_note", "body": "bad"}]),
        },
    )

    assert response.status_code == 422
    assert "age" in response.text.lower() or "value" in response.text.lower()


def test_cron_task_scanning(client, monkeypatch):
    now = datetime(2026, 5, 25, 14, 30, 15, tzinfo=timezone.utc)
    monkeypatch.setattr("flow_app.runner.utcnow", lambda: now)
    monkeypatch.setattr("flow_app.rules_engine.utcnow", lambda: now)
    stale_todo = create_task(client, status="todo", title="stale todo")
    stale_done = create_task(client, status="done", title="stale done")
    fresh_todo = create_task(client, status="todo", title="fresh todo")
    create_rule(
        client,
        name="Scan stale todos",
        trigger="cron",
        conditions=json.dumps(
            [
                {"field": "status", "operator": "eq", "value": "todo"},
                {"field": "age_since_updated", "operator": "gt", "value": 604800},
            ]
        ),
        actions=json.dumps([{"type": "add_note", "body": "stale todo note"}]),
    )

    with client.app.state.SessionLocal() as db:
        get_task(db, stale_todo["id"]).updated_at = now - timedelta(days=8)
        get_task(db, stale_done["id"]).updated_at = now - timedelta(days=8)
        get_task(db, fresh_todo["id"]).updated_at = now - timedelta(hours=1)
        db.commit()
        assert _run_cron_rules(db, dry_run=False) == 1
        db.commit()

    stale_notes = [note["body"] for note in client.get(f"/api/tasks/{stale_todo['id']}").json()["notes"]]
    done_notes = [note["body"] for note in client.get(f"/api/tasks/{stale_done['id']}").json()["notes"]]
    fresh_notes = [note["body"] for note in client.get(f"/api/tasks/{fresh_todo['id']}").json()["notes"]]
    assert "stale todo note" in stale_notes
    assert "stale todo note" not in done_notes
    assert "stale todo note" not in fresh_notes


def test_cron_dry_run(client, monkeypatch):
    now = datetime(2026, 5, 25, 14, 30, 15, tzinfo=timezone.utc)
    monkeypatch.setattr("flow_app.runner.utcnow", lambda: now)
    monkeypatch.setattr("flow_app.rules_engine.utcnow", lambda: now)
    task = create_task(client, status="todo")
    rule = create_rule(
        client,
        name="Dry run stale",
        trigger="cron",
        conditions=json.dumps([{"field": "age_since_updated", "operator": "gt", "value": 604800}]),
        actions=json.dumps([{"type": "add_note", "body": "dry run should not write"}]),
    )

    with client.app.state.SessionLocal() as db:
        get_task(db, task["id"]).updated_at = now - timedelta(days=8)
        db.commit()

    response = client.post("/api/automation-rules/dry-run", json={"trigger": "cron"})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["evaluated_rules"] == 1
    assert payload["matches"][0]["rule_id"] == rule["id"]
    assert payload["matches"][0]["task_id"] == task["id"]

    notes = client.get(f"/api/tasks/{task['id']}").json()["notes"]
    assert "dry run should not write" not in [note["body"] for note in notes]
    assert client.get(f"/api/automation-rules/{rule['id']}").json()["last_run_at"] is None


def test_emit_event_with_rule_id_only_executes_that_rule(client):
    task = create_task(client, priority=100)
    first = create_rule(
        client,
        name="First matching rule",
        actions=json.dumps([{"type": "add_note", "text": "first rule ran"}]),
    )
    second = create_rule(
        client,
        name="Second matching rule",
        actions=json.dumps([{"type": "add_note", "text": "second rule ran"}]),
    )

    with client.app.state.SessionLocal() as db:
        matches = emit_event(db, "task_created", task_id=task["id"], rule_id=second["id"])
        db.commit()

    assert [match["rule_id"] for match in matches] == [second["id"]]

    notes = client.get(f"/api/tasks/{task['id']}").json()["notes"]
    bodies = [note["body"] for note in notes]
    assert "first rule ran" not in bodies
    assert bodies.count("second rule ran") == 1
    assert client.get(f"/api/automation-rules/{first['id']}").json()["last_run_at"] is None
    assert client.get(f"/api/automation-rules/{second['id']}").json()["last_run_at"] is not None


def test_emit_event_without_rule_id_executes_all_matching_rules(client):
    task = create_task(client, priority=100)
    first = create_rule(
        client,
        name="First matching rule",
        actions=json.dumps([{"type": "add_note", "text": "first rule ran"}]),
    )
    second = create_rule(
        client,
        name="Second matching rule",
        actions=json.dumps([{"type": "add_note", "text": "second rule ran"}]),
    )

    with client.app.state.SessionLocal() as db:
        matches = emit_event(db, "task_created", task_id=task["id"])
        db.commit()

    assert {match["rule_id"] for match in matches} == {first["id"], second["id"]}

    notes = client.get(f"/api/tasks/{task['id']}").json()["notes"]
    bodies = [note["body"] for note in notes]
    assert bodies.count("first rule ran") == 1
    assert bodies.count("second rule ran") == 1


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
    assert read_response.json()["items"][0]["name"] == "High priority created"

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


def test_notify_action_routes_through_telegram_provider(client):
    reset_notify_provider()
    provider = RecordingNotificationProvider()
    set_notify_provider(RulesNotifyProvider(telegram_provider=provider))
    task = create_task(client)
    create_rule(client, actions=json.dumps([{"type": "notify", "channel": "telegram", "message": "Alert!"}]))

    try:
        response = client.post("/api/automation-rules/evaluate", json={"trigger": "task_created", "task_id": task["id"]})
    finally:
        reset_notify_provider()

    result = response.json()["matches"][0]["action_results"][0]
    provider_result = result["details"]["provider_result"]
    assert provider.calls == [
        {"event": "automation_notify", "task_id": task["id"], "changes": {"notify_message": "Alert!"}}
    ]
    assert provider_result["provider"] == "telegram"
    assert provider_result["status"] == "sent"


def test_notify_action_routes_through_discord_provider(client):
    reset_notify_provider()
    set_notify_provider(RulesNotifyProvider(telegram_provider=RecordingNotificationProvider()))
    discord_provider = RecordingNotificationProvider()
    register_notification_provider("discord", discord_provider)
    task = create_task(client)
    create_rule(client, actions=json.dumps([{"type": "notify", "channel": "discord", "message": "Deploy done!"}]))

    try:
        response = client.post("/api/automation-rules/evaluate", json={"trigger": "task_created", "task_id": task["id"]})
    finally:
        reset_notify_provider()

    result = response.json()["matches"][0]["action_results"][0]
    provider_result = result["details"]["provider_result"]
    assert discord_provider.calls == [
        {"event": "automation_notify", "task_id": task["id"], "changes": {"notify_message": "Deploy done!"}}
    ]
    assert provider_result["provider"] == "discord"
    assert provider_result["status"] == "sent"


def test_notify_action_falls_back_to_note_when_no_provider(client):
    reset_notify_provider()
    set_notify_provider(RulesNotifyProvider())
    task = create_task(client)
    create_rule(client, actions=json.dumps([{"type": "notify", "channel": "slack", "message": "Ping"}]))

    try:
        response = client.post("/api/automation-rules/evaluate", json={"trigger": "task_created", "task_id": task["id"]})
    finally:
        reset_notify_provider()

    result = response.json()["matches"][0]["action_results"][0]
    provider_result = result["details"]["provider_result"]
    assert provider_result["provider"] == "note_fallback"
    assert provider_result["status"] == "no_provider"

    notes = client.get(f"/api/tasks/{task['id']}").json()["notes"]
    assert "[notification:slack] Ping" in [note["body"] for note in notes]


def test_notify_action_handles_provider_failure(client):
    reset_notify_provider()
    set_notify_provider(RulesNotifyProvider(telegram_provider=FailingNotificationProvider()))
    task = create_task(client)
    create_rule(client, actions=json.dumps([{"type": "notify", "channel": "telegram", "message": "Oops"}]))

    try:
        response = client.post("/api/automation-rules/evaluate", json={"trigger": "task_created", "task_id": task["id"]})
    finally:
        reset_notify_provider()

    result = response.json()["matches"][0]["action_results"][0]
    provider_result = result["details"]["provider_result"]
    assert result["success"] is True
    assert provider_result["status"] == "failed"
    assert provider_result["details"]["error"] == "provider unavailable"

    notes = client.get(f"/api/tasks/{task['id']}").json()["notes"]
    assert "[notification:telegram] Oops" in [note["body"] for note in notes]


def test_notify_action_idempotent_does_not_recall_provider(client):
    reset_notify_provider()
    provider = RecordingNotificationProvider()
    set_notify_provider(RulesNotifyProvider(telegram_provider=provider))
    task = create_task(client)
    create_rule(client, actions=json.dumps([{"type": "notify", "channel": "telegram", "message": "Alert!"}]))

    try:
        first = client.post("/api/automation-rules/evaluate", json={"trigger": "task_created", "task_id": task["id"]})
        second = client.post("/api/automation-rules/evaluate", json={"trigger": "task_created", "task_id": task["id"]})
    finally:
        reset_notify_provider()

    first_result = first.json()["matches"][0]["action_results"][0]
    second_result = second.json()["matches"][0]["action_results"][0]
    assert first_result["details"]["provider_result"]["status"] == "sent"
    assert second_result["details"]["idempotent"] is True
    assert len(provider.calls) == 1


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
    rule_id = client.get("/api/automation-rules").json()["items"][0]["id"]
    client.patch(f"/api/automation-rules/{rule_id}", json={"actions": json.dumps([{"type": "spawn", "agent_name": "missing"}])})
    failure = client.post("/api/automation-rules/evaluate", json={"trigger": "task_created", "task_id": missing["id"]})
    failure_result = failure.json()["matches"][0]["action_results"][0]
    assert failure_result["success"] is False
    assert "registered" in failure_result["message"]


def test_rule_spawn_dispatch_without_api_key_mints_scoped_key(client, monkeypatch):
    task = create_task(client)
    agent = client.post(
        "/api/agents",
        json={"name": "automation-agent", "capabilities": "backend", "command": "echo hello"},
    ).json()
    captured = {}

    def fake_popen(args, **kwargs):
        captured["env"] = kwargs["env"]
        return SimpleNamespace(pid=12345)

    class NoopThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

    monkeypatch.setattr("flow_app.dispatcher.subprocess.Popen", fake_popen)
    monkeypatch.setattr("flow_app.dispatcher.threading.Thread", NoopThread)
    create_rule(client, actions=json.dumps([{"type": "spawn", "agent_id": agent["id"]}]))

    response = client.post("/api/automation-rules/evaluate", json={"trigger": "task_created", "task_id": task["id"]})

    result = response.json()["matches"][0]["action_results"][0]
    api_keys = client.get("/api/api-keys").json()
    assert result["success"] is True
    assert captured["env"]["FLOW_API_KEY"].startswith("flow_")
    assert captured["env"]["FLOW_BASE_URL"] == "http://0.0.0.0:8100"
    assert any(key["name"].startswith("dispatch-run_") and key["role"] == "implementer" for key in api_keys)


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

    deliveries = client.get(f"/api/webhooks/{webhook['id']}/deliveries").json()["items"]
    assert len(deliveries) == 1
    assert deliveries[0]["event"] == "task_created"

    retry = client.post("/api/automation-rules/evaluate", json={"trigger": "task_created", "task_id": task["id"]})
    retry_result = retry.json()["matches"][0]["action_results"][0]
    assert retry_result["details"]["idempotent"] is True
    assert len(client.get(f"/api/webhooks/{webhook['id']}/deliveries").json()["items"]) == 1


# ---------- REL-01: last_run_at and retry tests ----------


def test_failed_action_does_not_stamp_last_run(client):
    """When an action fails, last_run_at must NOT be stamped so the rule can retry."""
    task = create_task(client, status="todo")
    # A 'move' action with an invalid target status will fail.
    rule = create_rule(
        client,
        name="Failing move rule",
        trigger="task_created",
        conditions="[]",
        actions=json.dumps([{"type": "move", "status": "invalid_status"}]),
    )

    # Emit event — the action should fail, last_run_at should stay None
    with client.app.state.SessionLocal() as db:
        results = emit_event(db, "task_created", task_id=task["id"])
        db.commit()

    assert results[0]["action_results"][0]["success"] is False
    rule_data = client.get(f"/api/automation-rules/{rule['id']}").json()
    assert rule_data["last_run_at"] is None


def test_successful_action_stamps_last_run(client):
    """When all actions succeed, last_run_at IS stamped."""
    task = create_task(client, status="todo")
    rule = create_rule(
        client,
        name="Success rule",
        trigger="task_created",
        conditions="[]",
        actions=json.dumps([{"type": "add_note", "text": "hello"}]),
    )

    with client.app.state.SessionLocal() as db:
        results = emit_event(db, "task_created", task_id=task["id"])
        db.commit()

    assert results[0]["action_results"][0]["success"] is True
    rule_data = client.get(f"/api/automation-rules/{rule['id']}").json()
    assert rule_data["last_run_at"] is not None


def test_failed_action_retries_on_next_pass(client):
    """A failed action doesn't stamp last_run_at, so the next emit_event still fires it."""
    task = create_task(client, status="todo")
    # 'move' to 'doing' will succeed the first time (todo→doing),
    # then a second emit will try doing→doing which is a no-op success.
    # Use a rule that will fail: move to invalid status.
    rule = create_rule(
        client,
        name="Retry rule",
        trigger="task_created",
        conditions="[]",
        actions=json.dumps([{"type": "move", "status": "nonexistent"}]),
    )

    # First pass: action fails, last_run_at not stamped
    with client.app.state.SessionLocal() as db:
        results1 = emit_event(db, "task_created", task_id=task["id"])
        db.commit()
    assert results1[0]["action_results"][0]["success"] is False
    assert client.get(f"/api/automation-rules/{rule['id']}").json()["last_run_at"] is None

    # Second pass: rule still fires because last_run_at is None
    with client.app.state.SessionLocal() as db:
        results2 = emit_event(db, "task_created", task_id=task["id"])
        db.commit()
    assert len(results2) > 0  # Rule matched again (not deduped)


def test_concurrent_cron_exactly_once(client, monkeypatch):
    """Two _run_cron_rules calls — the atomic dedupe ensures exactly one fires.

    Simulates two runner passes racing: the first commits its atomic UPDATE
    (setting last_run_at), the second sees the updated row and skips.
    """
    from flow_app.runner import _run_cron_rules

    now = datetime(2026, 5, 25, 14, 30, 15)  # naive UTC for SQLite compatibility
    monkeypatch.setattr("flow_app.runner.utcnow", lambda: now)
    create_rule(
        client,
        name="Concurrent cron",
        trigger="cron",
        trigger_config=json.dumps({"cron": "* * * * *"}),
        conditions="[]",
        actions=json.dumps([]),
    )

    # First pass — fires and stamps last_run_at atomically
    with client.app.state.SessionLocal() as db:
        count1 = _run_cron_rules(db, dry_run=False)
        db.commit()

    # Second pass — sees last_run_at in the current minute, skips
    with client.app.state.SessionLocal() as db:
        count2 = _run_cron_rules(db, dry_run=False)
        db.commit()

    assert count1 == 1, f"First pass should fire, got {count1}"
    assert count2 == 0, f"Second pass should skip, got {count2}"


def test_markdown_import_fires_webhook_delivery(client):
    """Markdown import routes through TaskService, so webhook delivery fires."""
    # Create a webhook that listens for task_created
    webhook = client.post(
        "/api/webhooks",
        json={"name": "Import hook", "url": "https://example.com/hook", "events": ["task_created"], "project": "*"},
    )
    assert webhook.status_code == 201

    # Import a markdown task
    response = client.post(
        "/api/import/markdown/commit",
        json={
            "items": [
                {
                    "preview_id": "test-1",
                    "title": "Imported task for webhook",
                    "status": "todo",
                    "priority": 50,
                    "project": "default",
                }
            ]
        },
    )
    assert response.status_code == 200, response.text
    batch_id = response.json()["import_batch_id"]
    created = response.json()["created"]
    assert len(created) == 1

    # Verify webhook delivery was created (not just rule events)
    deliveries = client.get(f"/api/webhooks/{webhook.json()['id']}/deliveries").json()["items"]
    assert len(deliveries) >= 1
    assert deliveries[0]["event"] == "task_created"


def test_markdown_import_no_duplicate_board_events(client):
    """Markdown import must emit exactly one task_created board event per task,
    not two (svc.create_task already emits, the route must not emit again)."""
    from flow_app.realtime import board_events

    board_events.clear()
    response = client.post(
        "/api/import/markdown/commit",
        json={
            "items": [
                {
                    "preview_id": "test-1",
                    "title": "No dup events",
                    "status": "todo",
                    "priority": 50,
                    "project": "default",
                }
            ]
        },
    )
    assert response.status_code == 200, response.text

    task_created_events = [e for e in board_events.since(0) if e.event == "task_created"]
    assert len(task_created_events) == 1, (
        f"Expected exactly 1 task_created event, got {len(task_created_events)}"
    )


def test_markdown_import_atomic_rollback_on_failure(client, monkeypatch):
    """Import two tasks where the second task's creation raises.  After
    rollback, neither task must exist in the database.  The import must stage
    the batch and commit exactly once — not commit per item."""
    from flow_app.realtime import board_events
    from flow_app.services import task as task_service_mod

    board_events.clear()

    # Force the second create_task call to raise.  We patch repository.create_task
    # so it succeeds once then raises on the second call — this proves the batch
    # is staged together and rolled back together.
    original_create = task_service_mod.create_task
    call_count = {"n": 0}

    def flaky_create(session, payload):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("Simulated failure on second task")
        return original_create(session, payload)

    monkeypatch.setattr(task_service_mod, "create_task", flaky_create)

    response = client.post(
        "/api/import/markdown/commit",
        json={
            "items": [
                {
                    "preview_id": "test-1",
                    "title": "Atomic rollback A",
                    "status": "todo",
                    "priority": 50,
                    "project": "default",
                },
                {
                    "preview_id": "test-2",
                    "title": "Atomic rollback B",
                    "status": "todo",
                    "priority": 50,
                    "project": "default",
                },
            ]
        },
    )
    # The route re-raises → TestClient surfaces 500.
    assert response.status_code == 500, response.text

    # Neither task must exist in the DB after rollback.
    with client.app.state.SessionLocal() as db:
        from flow_app.models import Task
        titles = [t.title for t in db.query(Task).all()]
    assert "Atomic rollback A" not in titles, (
        f"First task leaked after rollback: {titles}"
    )
    assert "Atomic rollback B" not in titles, (
        f"Second task leaked after rollback: {titles}"
    )

    # No board events must have been published for the rolled-back batch.
    task_created_events = [e for e in board_events.since(0) if e.event == "task_created"]
    assert len(task_created_events) == 0, (
        f"Rolled-back batch published board events: {len(task_created_events)}"
    )


def test_markdown_import_no_side_effects_before_commit(client, monkeypatch):
    """AC #1-2: Inject recording rule, webhook, Telegram, and Discord providers.
    Force staging of task two to raise.  Assert zero provider/rule calls, zero
    persisted imported tasks, and zero board events.  Before the batch commit,
    code may only stage transactional DB state — no automation actions, no
    spawned agents, no external HTTP."""
    from flow_app.realtime import board_events
    from flow_app.services import task as task_service_mod
    from flow_app.routes import dependencies as deps_mod

    board_events.clear()

    # Recording providers — count every send() call.
    calls = {"telegram": 0, "discord": 0, "webhook": 0, "rule": 0}

    class RecordingTelegram:
        def send(self, db, event, task, changes=None):
            calls["telegram"] += 1

    class RecordingDiscord:
        def send(self, db, event, task, changes=None):
            calls["discord"] += 1

    class RecordingWebhook:
        def send(self, db, event, task, changes=None):
            calls["webhook"] += 1

    def recording_rule_emitter(session, trigger, task_id=None, data=None, actor=None, rule_id=None, dry_run=False):
        calls["rule"] += 1
        return []

    # Patch the module-level providers and the rule emitter used by
    # _make_task_service so the recording providers are injected.
    monkeypatch.setattr(deps_mod, "_telegram_notifier", RecordingTelegram())
    monkeypatch.setattr(deps_mod, "_discord_notifier", RecordingDiscord())
    monkeypatch.setattr(deps_mod, "_webhook_notifier", RecordingWebhook())
    monkeypatch.setattr(deps_mod, "emit_rule_event", recording_rule_emitter)

    # Force the second create_task call to raise during staging.
    original_create = task_service_mod.create_task
    call_count = {"n": 0}

    def flaky_create(session, payload):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("Simulated failure on second task")
        return original_create(session, payload)

    monkeypatch.setattr(task_service_mod, "create_task", flaky_create)

    response = client.post(
        "/api/import/markdown/commit",
        json={
            "items": [
                {
                    "preview_id": "test-1",
                    "title": "No side effects A",
                    "status": "todo",
                    "priority": 50,
                    "project": "default",
                },
                {
                    "preview_id": "test-2",
                    "title": "No side effects B",
                    "status": "todo",
                    "priority": 50,
                    "project": "default",
                },
            ]
        },
    )
    assert response.status_code == 500, response.text

    # Zero non-transactional side effects must have fired during staging.
    assert calls["telegram"] == 0, f"Telegram fired during staging: {calls['telegram']}"
    assert calls["discord"] == 0, f"Discord fired during staging: {calls['discord']}"
    assert calls["rule"] == 0, f"Rule emitter fired during staging: {calls['rule']}"

    # Zero persisted tasks.
    with client.app.state.SessionLocal() as db:
        from flow_app.models import Task
        titles = [t.title for t in db.query(Task).all()]
    assert "No side effects A" not in titles
    assert "No side effects B" not in titles

    # Zero board events.
    task_created_events = [e for e in board_events.since(0) if e.event == "task_created"]
    assert len(task_created_events) == 0


def test_markdown_import_post_commit_side_effects_fire_once(client, monkeypatch):
    """AC #3: On a successful two-task import, commit tasks first, then invoke
    each configured post-commit side effect exactly once per task and publish
    exactly one board event per task with the response import_batch_id."""
    from flow_app.realtime import board_events
    from flow_app.routes import dependencies as deps_mod

    board_events.clear()

    calls = {"telegram": 0, "discord": 0, "rule": 0}

    class RecordingTelegram:
        def send(self, db, event, task, changes=None):
            calls["telegram"] += 1

    class RecordingDiscord:
        def send(self, db, event, task, changes=None):
            calls["discord"] += 1

    def recording_rule_emitter(session, trigger, task_id=None, data=None, actor=None, rule_id=None, dry_run=False):
        calls["rule"] += 1
        return []

    monkeypatch.setattr(deps_mod, "_telegram_notifier", RecordingTelegram())
    monkeypatch.setattr(deps_mod, "_discord_notifier", RecordingDiscord())
    monkeypatch.setattr(deps_mod, "emit_rule_event", recording_rule_emitter)

    response = client.post(
        "/api/import/markdown/commit",
        json={
            "items": [
                {
                    "preview_id": "test-1",
                    "title": "Post-commit A",
                    "status": "todo",
                    "priority": 50,
                    "project": "default",
                },
                {
                    "preview_id": "test-2",
                    "title": "Post-commit B",
                    "status": "todo",
                    "priority": 50,
                    "project": "default",
                },
            ]
        },
    )
    assert response.status_code == 200, response.text
    batch_id = response.json()["import_batch_id"]

    # Each side effect fired exactly once per task (2 tasks = 2 calls each).
    assert calls["telegram"] == 2, f"Telegram should fire 2x, got {calls['telegram']}"
    assert calls["discord"] == 2, f"Discord should fire 2x, got {calls['discord']}"
    assert calls["rule"] == 2, f"Rule should fire 2x, got {calls['rule']}"

    # Exactly 2 board events with matching import_batch_id.
    task_created_events = [e for e in board_events.since(0) if e.event == "task_created"]
    assert len(task_created_events) == 2
    for event in task_created_events:
        assert event.data.get("import_batch_id") == batch_id


def test_markdown_import_post_commit_failure_does_not_rollback(client, monkeypatch):
    """AC #4: A post-commit notification failure must not claim the task batch
    rolled back or create duplicate tasks.  The delivery failure is recorded
    through existing delivery semantics."""
    from flow_app.realtime import board_events
    from flow_app.routes import dependencies as deps_mod

    board_events.clear()

    class FailingTelegram:
        def send(self, db, event, task, changes=None):
            raise RuntimeError("Telegram API down")

    monkeypatch.setattr(deps_mod, "_telegram_notifier", FailingTelegram())

    response = client.post(
        "/api/import/markdown/commit",
        json={
            "items": [
                {
                    "preview_id": "test-1",
                    "title": "Post-commit failure task",
                    "status": "todo",
                    "priority": 50,
                    "project": "default",
                },
            ]
        },
    )
    # The batch must succeed despite the Telegram failure.
    assert response.status_code == 200, response.text
    assert len(response.json()["created"]) == 1

    # The task must be persisted in the DB.
    with client.app.state.SessionLocal() as db:
        from flow_app.models import Task
        titles = [t.title for t in db.query(Task).all()]
    assert "Post-commit failure task" in titles

    # Board event must still be published.
    task_created_events = [e for e in board_events.since(0) if e.event == "task_created"]
    assert len(task_created_events) == 1


def test_markdown_import_emits_two_events_with_import_batch_id(client):
    """A successful two-task import must produce exactly two task_created board
    events, one per task, and each event.data['import_batch_id'] must equal the
    response import_batch_id.  Events are published only after the commit."""
    from flow_app.realtime import board_events

    board_events.clear()
    response = client.post(
        "/api/import/markdown/commit",
        json={
            "items": [
                {
                    "preview_id": "test-1",
                    "title": "Batch event A",
                    "status": "todo",
                    "priority": 50,
                    "project": "default",
                },
                {
                    "preview_id": "test-2",
                    "title": "Batch event B",
                    "status": "todo",
                    "priority": 50,
                    "project": "default",
                },
            ]
        },
    )
    assert response.status_code == 200, response.text
    batch_id = response.json()["import_batch_id"]

    task_created_events = [e for e in board_events.since(0) if e.event == "task_created"]
    assert len(task_created_events) == 2, (
        f"Expected exactly 2 task_created events, got {len(task_created_events)}"
    )
    for event in task_created_events:
        assert event.data.get("import_batch_id") == batch_id, (
            f"Event import_batch_id mismatch: {event.data.get('import_batch_id')} != {batch_id}"
        )


def test_true_concurrent_cron_exactly_once(client, monkeypatch):
    """Two threads call _run_cron_rules simultaneously — atomic dedupe ensures
    exactly one fires.  This is a real threaded race, not a sequential simulation."""
    import threading

    from flow_app.runner import _run_cron_rules

    now = datetime(2026, 5, 25, 14, 30, 15)
    monkeypatch.setattr("flow_app.runner.utcnow", lambda: now)
    create_rule(
        client,
        name="Threaded concurrent cron",
        trigger="cron",
        trigger_config=json.dumps({"cron": "* * * * *"}),
        conditions="[]",
        actions=json.dumps([]),
    )

    results = {"a": None, "b": None}
    barrier = threading.Barrier(2)
    errors = {"a": None, "b": None}

    def run_cron(label):
        session = client.app.state.SessionLocal()
        try:
            barrier.wait(timeout=5)
            count = _run_cron_rules(session, dry_run=False)
            session.commit()
            results[label] = count
        except Exception as exc:
            session.rollback()
            errors[label] = str(exc)
        finally:
            session.close()

    t1 = threading.Thread(target=run_cron, args=("a",))
    t2 = threading.Thread(target=run_cron, args=("b",))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    # Both threads must have terminated cleanly (not hung/blocked).
    assert not t1.is_alive(), "Thread a did not terminate within timeout"
    assert not t2.is_alive(), "Thread b did not terminate within timeout"
    # No unhandled exceptions in either thread.
    assert errors == {"a": None, "b": None}, (
        f"Expected no errors in either thread, got errors={errors}"
    )
    total_fired = sum(v or 0 for v in results.values())
    assert total_fired == 1, (
        f"Expected exactly 1 cron fire across both threads, got {total_fired}. "
        f"Results: {results}, Errors: {errors}"
    )


def test_wrong_shape_conditions_flagged_broken(client):
    """A rule with valid JSON but wrong-shape conditions (dict instead of list)
    must be flagged as broken, not silently matched or crash."""
    from flow_app.models import AutomationRule

    now = utcnow()
    with client.app.state.SessionLocal() as db:
        rule = AutomationRule(
            id="rule_wrong_shape_cond",
            name="Wrong-shape conditions",
            description="",
            enabled=1,
            priority=50,
            trigger="task_created",
            trigger_config="",
            conditions=json.dumps({"field": "priority", "operator": "gte", "value": 70}),  # dict, not list
            actions=json.dumps([{"type": "notify", "channel": "ops"}]),
            last_run_at=None,
            created_at=now,
            updated_at=now,
        )
        db.add(rule)
        db.commit()
        rule_id = rule.id

    # The rule serializer should report broken=True
    fetched = client.get(f"/api/automation-rules/{rule_id}").json()
    assert fetched["broken"] is True, (
        f"Wrong-shape conditions should be broken, got broken={fetched['broken']}"
    )


def test_wrong_shape_actions_flagged_broken(client):
    """A rule with valid JSON but wrong-shape actions (string instead of list)
    must be flagged as broken."""
    from flow_app.models import AutomationRule

    now = utcnow()
    with client.app.state.SessionLocal() as db:
        rule = AutomationRule(
            id="rule_wrong_shape_actions",
            name="Wrong-shape actions",
            description="",
            enabled=1,
            priority=50,
            trigger="task_created",
            trigger_config="",
            conditions=json.dumps([]),
            actions=json.dumps("notify"),  # string, not list
            last_run_at=None,
            created_at=now,
            updated_at=now,
        )
        db.add(rule)
        db.commit()
        rule_id = rule.id

    fetched = client.get(f"/api/automation-rules/{rule_id}").json()
    assert fetched["broken"] is True, (
        f"Wrong-shape actions should be broken, got broken={fetched['broken']}"
    )


def test_wrong_shape_rule_skipped_in_match(client):
    """A wrong-shape rule (valid JSON, not a list) must be skipped during
    match_rules, not crash or silently match."""
    from flow_app.models import AutomationRule
    from flow_app.rules_engine import match_rules

    now = utcnow()
    with client.app.state.SessionLocal() as db:
        rule = AutomationRule(
            id="rule_wrong_shape_match",
            name="Wrong-shape match skip",
            description="",
            enabled=1,
            priority=50,
            trigger="task_created",
            trigger_config="",
            conditions=json.dumps({"field": "priority"}),  # dict, not list
            actions=json.dumps([{"type": "notify", "channel": "ops"}]),
            last_run_at=None,
            created_at=now,
            updated_at=now,
        )
        db.add(rule)
        db.commit()

    # match_rules should return empty — the wrong-shape rule is skipped
    results = match_rules(
        client.app.state.SessionLocal(),
        trigger="task_created",
        task_id=None,
        data={"priority": 80, "project": "default"},
    )
    assert results == [], (
        f"Wrong-shape rule should be skipped, got matches: {results}"
    )
