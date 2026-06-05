from __future__ import annotations

from datetime import datetime, timezone


def test_create_task_with_metadata(client):
    response = client.post(
        "/api/tasks",
        json={
            "title": "Review metadata propagation",
            "project": "default",
            "metadata": '{"color":"blue"}',
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["metadata"] == '{"color":"blue"}'
    assert body["source_template_id"] is None


def test_create_task_source_template_id(client):
    template_response = client.post(
        "/api/recurring-task-templates",
        json={
            "name": "Daily triage",
            "project": "default",
            "title": "Review the incoming task queue",
            "cadence": "daily",
            "next_run_at": datetime(2026, 6, 4, 9, 0, tzinfo=timezone.utc).isoformat(),
        },
    )
    assert template_response.status_code == 201, template_response.text
    template_id = template_response.json()["id"]

    response = client.post(
        "/api/tasks",
        json={
            "title": "Manual child of template",
            "project": "default",
            "source_template_id": template_id,
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["source_template_id"] == template_id
    assert body["metadata"] == "{}"
