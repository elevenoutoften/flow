from __future__ import annotations

from datetime import datetime, timedelta, timezone

from flow_app.repository import get_recurring_task_template, get_task, list_tasks
from flow_app.scheduler import compute_next_run, materialize_due_templates, parse_cadence
from flow_app.schemas import RecurringTaskTemplateCreate
from flow_app.services.recurring_task_template import RecurringTemplateService


NOW = datetime(2026, 6, 4, 9, 0, tzinfo=timezone.utc)


def _create_template(db, **overrides):
    payload = {
        "name": "Weekly triage",
        "project": "default",
        "title": "Review the incoming task queue",
        "description": "Check new tasks and sort them.",
        "acceptance_criteria": "Queue is prioritized.",
        "priority": 350,
        "status": "todo",
        "complexity": "small",
        "impact": "medium",
        "effort": "medium",
        "risk": "low",
        "cadence": "weekly",
        "next_run_at": NOW - timedelta(minutes=1),
        "enabled": True,
    }
    payload.update(overrides)
    return RecurringTemplateService(db).create(RecurringTaskTemplateCreate(**payload))


def test_parse_named_cadences():
    assert parse_cadence("daily") == timedelta(days=1)
    assert parse_cadence("weekly") == timedelta(weeks=1)
    assert parse_cadence("biweekly") == timedelta(weeks=2)
    assert parse_cadence("monthly") == timedelta(days=30)
    assert parse_cadence("quarterly") == timedelta(days=90)
    assert parse_cadence("yearly") == timedelta(days=365)


def test_parse_simple_intervals():
    assert parse_cadence("15m") == timedelta(minutes=15)
    assert parse_cadence("2h") == timedelta(hours=2)
    assert parse_cadence("1d") == timedelta(days=1)
    assert parse_cadence("3w") == timedelta(weeks=3)


def test_parse_cron_fallback(caplog):
    assert parse_cadence("0 9 * * 1") == timedelta(days=1)
    assert "defaulting to 1 day" in caplog.text


def test_compute_next_run_daily():
    assert compute_next_run("daily", NOW) == NOW + timedelta(days=1)


def test_compute_next_run_weekly():
    assert compute_next_run("weekly", NOW) == NOW + timedelta(weeks=1)


def test_materialize_due_template(client):
    with client.app.state.SessionLocal() as db:
        template = _create_template(db, cadence="daily")
        result = materialize_due_templates(db, now=NOW)

        assert result.materialized == 1
        assert result.skipped == 0
        assert result.details[0].template_id == template["id"]
        assert result.details[0].task_id == "flow_000001"

        created = get_task(db, "flow_000001")
        assert created is not None
        assert created.title == template["title"]
        assert created.description.endswith(f"Created from recurring template {template['id']} ({template['name']}).")

        updated = get_recurring_task_template(db, template["id"])
        assert updated.next_run_at.replace(tzinfo=timezone.utc) == NOW + timedelta(days=1)


def test_materialize_skips_not_due(client):
    with client.app.state.SessionLocal() as db:
        _create_template(db, next_run_at=NOW + timedelta(minutes=1))
        result = materialize_due_templates(db, now=NOW)

        assert result.materialized == 0
        assert result.skipped == 0
        assert result.details == []
        assert list_tasks(db) == []


def test_materialize_skips_disabled(client):
    with client.app.state.SessionLocal() as db:
        _create_template(db, enabled=False)
        result = materialize_due_templates(db, now=NOW)

        assert result.materialized == 0
        assert result.skipped == 0
        assert result.details == []
        assert list_tasks(db) == []


def test_materialize_dry_run(client):
    with client.app.state.SessionLocal() as db:
        template = _create_template(db, cadence="daily")
        result = materialize_due_templates(db, now=NOW, dry_run=True)

        assert result.materialized == 0
        assert result.skipped == 1
        assert result.dry_run is True
        assert result.details[0].template_id == template["id"]
        assert result.details[0].skipped is True
        assert result.details[0].skip_reason == "dry_run"
        assert list_tasks(db) == []
        next_run_at = get_recurring_task_template(db, template["id"]).next_run_at
        assert next_run_at.replace(tzinfo=timezone.utc) == NOW - timedelta(minutes=1)


def test_materialize_idempotent(client):
    with client.app.state.SessionLocal() as db:
        _create_template(db, cadence="daily")
        first = materialize_due_templates(db, now=NOW)
        second = materialize_due_templates(db, now=NOW)

        assert first.materialized == 1
        assert second.materialized == 0
        assert list_tasks(db, limit=100) == [get_task(db, "flow_000001")]


def test_materialize_adds_audit_note(client):
    with client.app.state.SessionLocal() as db:
        template = _create_template(db)
        materialize_due_templates(db, now=NOW)

        created = get_task(db, "flow_000001")
        assert created is not None
        assert len(created.notes) == 1
        assert created.notes[0].author == "scheduler"
        assert created.notes[0].body == f"Created from recurring template {template['id']} ({template['name']})"


def test_rest_materialize_endpoint(client):
    due = (NOW - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    response = client.post(
        "/api/recurring-task-templates",
        json={
            "name": "Daily triage",
            "project": "default",
            "title": "Review the incoming task queue",
            "cadence": "daily",
            "next_run_at": due,
        },
    )
    assert response.status_code == 201, response.text

    response = client.post("/api/recurring-task-templates/materialize")

    assert response.status_code == 200
    body = response.json()
    assert body["materialized"] == 1
    assert body["skipped"] == 0
    assert body["dry_run"] is False
    assert body["details"][0]["task_id"] == "flow_000001"


def test_rest_materialize_dry_run_endpoint(client):
    due = (NOW - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    response = client.post(
        "/api/recurring-task-templates",
        json={
            "name": "Daily triage",
            "project": "default",
            "title": "Review the incoming task queue",
            "cadence": "daily",
            "next_run_at": due,
        },
    )
    assert response.status_code == 201, response.text

    response = client.post("/api/recurring-task-templates/materialize", params={"dry_run": "true"})

    assert response.status_code == 200
    body = response.json()
    assert body["materialized"] == 0
    assert body["skipped"] == 1
    assert body["dry_run"] is True
    assert body["details"][0]["skipped"] is True
    assert body["details"][0]["skip_reason"] == "dry_run"
