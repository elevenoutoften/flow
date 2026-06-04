from __future__ import annotations

import re

from flow_app.repository import generate_recurring_task_template_id
from flow_app.schemas import RecurringTaskTemplateResponse


NEXT_RUN_AT = "2026-06-05T09:00:00Z"


def template_payload(**overrides):
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
        "next_run_at": NEXT_RUN_AT,
        "enabled": True,
    }
    payload.update(overrides)
    return payload


def create_template(client, **overrides):
    response = client.post("/api/recurring-task-templates", json=template_payload(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


def test_create_template(client):
    template = create_template(client)

    assert template["id"] == "rpt_000001"
    assert template["name"] == "Weekly triage"
    assert template["project"] == "default"
    assert template["title"] == "Review the incoming task queue"
    assert template["description"] == "Check new tasks and sort them."
    assert template["acceptance_criteria"] == "Queue is prioritized."
    assert template["priority"] == 350
    assert template["status"] == "todo"
    assert template["complexity"] == "small"
    assert template["impact"] == "medium"
    assert template["effort"] == "medium"
    assert template["risk"] == "low"
    assert template["cadence"] == "weekly"
    assert template["enabled"] is True


def test_create_template_invalid_cadence(client):
    response = client.post("/api/recurring-task-templates", json=template_payload(cadence=" "))

    assert response.status_code == 422


def test_create_template_invalid_status(client):
    response = client.post("/api/recurring-task-templates", json=template_payload(status="blocked"))

    assert response.status_code == 422


def test_list_templates(client):
    first = create_template(client, name="First", next_run_at="2026-06-05T09:00:00Z")
    second = create_template(client, name="Second", next_run_at="2026-06-06T09:00:00Z")

    response = client.get("/api/recurring-task-templates")

    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["items"]] == [first["id"], second["id"]]
    assert body["total"] == 2
    assert body["limit"] == 100
    assert body["offset"] == 0


def test_list_templates_filter_project(client):
    default_template = create_template(client, name="Default", project="default")
    create_template(client, name="Ops", project="ops")

    response = client.get("/api/recurring-task-templates", params={"project": "default"})

    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["items"]] == [default_template["id"]]
    assert body["total"] == 1


def test_list_templates_filter_enabled(client):
    enabled = create_template(client, name="Enabled", enabled=True)
    create_template(client, name="Disabled", enabled=False)

    response = client.get("/api/recurring-task-templates", params={"enabled": "true"})

    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["items"]] == [enabled["id"]]
    assert body["total"] == 1


def test_get_template(client):
    template = create_template(client)

    response = client.get(f"/api/recurring-task-templates/{template['id']}")

    assert response.status_code == 200
    assert response.json()["id"] == template["id"]


def test_get_template_not_found(client):
    response = client.get("/api/recurring-task-templates/rpt_999999")

    assert response.status_code == 404


def test_update_template(client):
    template = create_template(client)

    response = client.patch(
        f"/api/recurring-task-templates/{template['id']}",
        json={"name": "Daily triage", "priority": 100, "cadence": "daily"},
    )

    assert response.status_code == 200
    updated = response.json()
    assert updated["name"] == "Daily triage"
    assert updated["priority"] == 100
    assert updated["cadence"] == "daily"


def test_update_template_enable_disable(client):
    template = create_template(client, enabled=True)

    disabled = client.patch(f"/api/recurring-task-templates/{template['id']}", json={"enabled": False})
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False

    enabled = client.patch(f"/api/recurring-task-templates/{template['id']}", json={"enabled": True})
    assert enabled.status_code == 200
    assert enabled.json()["enabled"] is True


def test_update_template_not_found(client):
    response = client.patch("/api/recurring-task-templates/rpt_999999", json={"name": "Missing"})

    assert response.status_code == 404


def test_template_serialization(client):
    template = create_template(client)

    serialized = RecurringTaskTemplateResponse(**template)

    assert serialized.id == template["id"]
    assert serialized.name == template["name"]
    assert serialized.next_run_at is not None
    assert serialized.created_at is not None
    assert serialized.updated_at is not None


def test_default_metadata(client):
    template = create_template(client)

    assert template["metadata"] == "{}"


def test_id_generation(client):
    first = create_template(client, name="First")
    second = create_template(client, name="Second")

    assert re.fullmatch(r"rpt_\d{6}", first["id"])
    assert first["id"] == "rpt_000001"
    assert second["id"] == "rpt_000002"


def test_repository_id_generation(client):
    with client.app.state.SessionLocal() as db:
        generated = generate_recurring_task_template_id(db)

    assert generated == "rpt_000001"
