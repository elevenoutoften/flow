from __future__ import annotations

from datetime import timedelta

import pytest

from flow_app.dispatcher import stale_recovery
from flow_app.models import AgentRun, RunnerLease, Task, utcnow
from flow_app.repository import create_runner_lease


def bearer_headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


def create_role_headers(client, role: str, name: str) -> dict[str, str]:
    response = client.post("/api/api-keys", json={"name": name, "role": role})
    assert response.status_code == 201, response.text
    return bearer_headers(response.json()["api_key"])


def create_runner(client, **overrides) -> dict:
    payload = {
        "name": "remote-runner",
        "description": "Remote runner",
        "capabilities": "python,tests",
        "agent_names": "implementer,reviewer",
    }
    payload.update(overrides)
    response = client.post("/api/runners", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def create_agent(client, **overrides) -> dict:
    payload = {
        "name": "implementer",
        "description": "Implementation agent",
        "command": "codex exec",
        "env_allowlist": "FLOW_API_KEY,GITHUB_TOKEN",
    }
    payload.update(overrides)
    response = client.post("/api/agents", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def create_task(client, **overrides) -> dict:
    payload = {"title": "Implement feature X", "status": "backlog"}
    payload.update(overrides)
    response = client.post("/api/tasks", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def link_tasks(client, parent_id: str, child_id: str, link_type: str = "blocks") -> dict:
    response = client.post(
        f"/api/tasks/{parent_id}/link",
        json={"parent_id": parent_id, "child_id": child_id, "link_type": link_type},
    )
    assert response.status_code == 201, response.text
    return response.json()


def create_runner_with_key(client, **overrides) -> tuple[dict, str]:
    key = overrides.pop("api_key_ref", "runner-secret")
    runner = create_runner(client, api_key_ref=key, **overrides)
    return runner, key


def test_runner_crud_and_soft_delete(client):
    runner = create_runner(client)

    assert runner["id"].startswith("runner_")
    assert runner["enabled"] is True
    assert runner["runner_type"] == "poll"
    assert runner["status"] == "offline"
    assert runner["lease_duration_seconds"] == 600
    assert runner["heartbeat_interval_seconds"] == 60
    assert runner["max_concurrent_leases"] == 1

    listed = client.get("/api/runners").json()
    assert listed["total"] == 1
    assert listed["items"][0]["id"] == runner["id"]

    fetched = client.get(f"/api/runners/{runner['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "remote-runner"

    updated = client.patch(
        f"/api/runners/{runner['id']}",
        json={
            "description": "Updated",
            "enabled": False,
            "runner_type": "push",
            "status": "draining",
            "lease_duration_seconds": 120,
            "heartbeat_interval_seconds": 15,
            "max_concurrent_leases": 3,
            "api_key_ref": "env:FLOW_RUNNER_KEY",
        },
    )
    assert updated.status_code == 200, updated.text
    data = updated.json()
    assert data["description"] == "Updated"
    assert data["enabled"] is False
    assert data["runner_type"] == "push"
    assert data["status"] == "draining"
    assert data["lease_duration_seconds"] == 120
    assert data["heartbeat_interval_seconds"] == 15
    assert data["max_concurrent_leases"] == 3
    assert data["api_key_ref"] == "env:FLOW_RUNNER_KEY"

    deleted = client.delete(f"/api/runners/{runner['id']}")
    assert deleted.status_code == 200, deleted.text
    data = deleted.json()
    assert data["enabled"] is False
    assert data["status"] == "offline"


def test_runner_api_key_ref_redacts_plaintext(client):
    response = client.post(
        "/api/runners",
        json={
            "name": "test-runner-redact",
            "runner_type": "poll",
            "api_key_ref": "sk-super-secret-key-12345",
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["api_key_ref"] == "***"


def test_runner_api_key_ref_preserves_env_reference(client):
    response = client.post(
        "/api/runners",
        json={
            "name": "test-runner-env-ref",
            "runner_type": "poll",
            "api_key_ref": "env:FLOW_RUNNER_KEY",
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["api_key_ref"] == "env:FLOW_RUNNER_KEY"


def test_runner_api_key_ref_preserves_file_reference(client):
    response = client.post(
        "/api/runners",
        json={
            "name": "test-runner-file-ref",
            "runner_type": "poll",
            "api_key_ref": "file:/etc/flow/runner-key",
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["api_key_ref"] == "file:/etc/flow/runner-key"


def test_runner_list_redacts_api_key_ref(client):
    create_runner(
        client,
        name="test-runner-list-redact",
        runner_type="poll",
        api_key_ref="plaintext-secret-xyz",
    )

    response = client.get("/api/runners")

    assert response.status_code == 200
    runners = response.json()["items"]
    for runner in runners:
        if runner["name"] == "test-runner-list-redact":
            assert runner["api_key_ref"] == "***"
            break
    else:
        pytest.fail("test-runner-list-redact not found in runner list")


def test_runner_list_filters(client):
    first = create_runner(client, name="runner-a")
    create_runner(client, name="runner-b")
    client.patch(f"/api/runners/{first['id']}", json={"enabled": False, "status": "online"})

    enabled_only = client.get("/api/runners", params={"enabled_only": "true"})
    assert enabled_only.status_code == 200
    assert [item["name"] for item in enabled_only.json()["items"]] == ["runner-b"]

    online = client.get("/api/runners", params={"status": "online"})
    assert online.status_code == 200
    assert [item["name"] for item in online.json()["items"]] == ["runner-a"]


def test_runner_permissions_admin_manages_implementer_reads(client, no_auth_client):
    runner = create_runner(client)
    implementer_headers = create_role_headers(client, "implementer", "runner-impl")

    read_response = no_auth_client.get("/api/runners", headers=implementer_headers)
    assert read_response.status_code == 200

    get_response = no_auth_client.get(f"/api/runners/{runner['id']}", headers=implementer_headers)
    assert get_response.status_code == 200

    create_response = no_auth_client.post(
        "/api/runners",
        json={"name": "forbidden-runner"},
        headers=implementer_headers,
    )
    assert create_response.status_code == 403

    update_response = no_auth_client.patch(
        f"/api/runners/{runner['id']}",
        json={"status": "online"},
        headers=implementer_headers,
    )
    assert update_response.status_code == 403


def test_runner_name_uniqueness(client):
    create_runner(client, name="unique-runner")
    duplicate = client.post("/api/runners", json={"name": "unique-runner"})
    assert duplicate.status_code == 409


def test_runner_type_validation(client):
    response = client.post("/api/runners", json={"name": "bad-type", "runner_type": "sidecar"})
    assert response.status_code == 422

    runner = create_runner(client, name="valid-type")
    response = client.patch(f"/api/runners/{runner['id']}", json={"runner_type": "sidecar"})
    assert response.status_code == 422


def test_runner_status_validation(client):
    runner = create_runner(client, name="status-runner")
    response = client.patch(f"/api/runners/{runner['id']}", json={"status": "busy"})
    assert response.status_code == 422


def test_runner_lease_duration_minimum(client):
    response = client.post("/api/runners", json={"name": "short-lease", "lease_duration_seconds": 59})
    assert response.status_code == 422

    runner = create_runner(client, name="lease-runner")
    response = client.patch(f"/api/runners/{runner['id']}", json={"lease_duration_seconds": 59})
    assert response.status_code == 422


def test_runner_poll_with_work_available(client):
    agent = create_agent(client)
    task = create_task(client)
    runner, key = create_runner_with_key(client, agent_names=agent["name"])

    response = client.post(f"/api/runners/{runner['id']}/poll", headers=bearer_headers(key))

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["id"].startswith("lease_")
    assert data["runner_id"] == runner["id"]
    assert data["status"] == "active"
    assert data["task_id"] == task["id"]
    assert data["task_title"] == task["title"]
    assert data["agent_name"] == agent["name"]
    assert data["agent_command"] == agent["command"]
    assert data["agent_env_allowlist"] == agent["env_allowlist"]

    fetched = client.get(f"/api/tasks/{task['id']}").json()
    assert fetched["status"] == "doing"
    assert fetched["assignee"] == agent["name"]


def test_runner_poll_no_work(client):
    create_agent(client)
    runner, key = create_runner_with_key(client, agent_names="implementer")

    response = client.post(f"/api/runners/{runner['id']}/poll", headers=bearer_headers(key))

    assert response.status_code == 204
    assert response.content == b""


def test_runner_poll_skips_blocked_tasks(client):
    create_agent(client)
    parent = create_task(client, title="Parent blocker", status="todo", human_required=True)
    child = create_task(client, title="Blocked child", status="todo", priority=100)
    link_tasks(client, parent["id"], child["id"], "blocks")
    runner, key = create_runner_with_key(client, agent_names="implementer")

    response = client.post(f"/api/runners/{runner['id']}/poll", headers=bearer_headers(key))

    assert response.status_code == 204
    assert response.content == b""
    fetched = client.get(f"/api/tasks/{child['id']}").json()
    assert fetched["status"] == "todo"
    assert fetched["assignee"] is None


def test_runner_poll_skips_task_when_agent_at_concurrency_limit(client):
    agent = create_agent(client, max_concurrency=1)
    first_task = create_task(client, title="First task", status="todo", priority=100)
    second_task = create_task(client, title="Second task", status="todo", priority=90)
    runner, key = create_runner_with_key(
        client,
        agent_names=agent["name"],
        max_concurrent_leases=2,
    )
    first = client.post(f"/api/runners/{runner['id']}/poll", headers=bearer_headers(key))
    assert first.status_code == 200, first.text
    assert first.json()["task_id"] == first_task["id"]

    response = client.post(f"/api/runners/{runner['id']}/poll", headers=bearer_headers(key))

    assert response.status_code == 204
    assert response.content == b""
    fetched = client.get(f"/api/tasks/{second_task['id']}").json()
    assert fetched["status"] == "todo"
    assert fetched["assignee"] is None


def test_runner_poll_resolves_dependency_and_leases(client):
    agent = create_agent(client)
    parent = create_task(client, title="Parent blocker", status="todo", human_required=True)
    child = create_task(client, title="Blocked child", status="todo", priority=100)
    link_tasks(client, parent["id"], child["id"], "depends_on")
    runner, key = create_runner_with_key(client, agent_names=agent["name"])

    blocked = client.post(f"/api/runners/{runner['id']}/poll", headers=bearer_headers(key))
    assert blocked.status_code == 204

    with client.app.state.SessionLocal() as db:
        stored_parent = db.get(Task, parent["id"])
        stored_parent.status = "done"
        stored_parent.updated_at = utcnow()
        db.commit()

    response = client.post(f"/api/runners/{runner['id']}/poll", headers=bearer_headers(key))

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["task_id"] == child["id"]
    assert data["agent_name"] == agent["name"]


def test_runner_poll_picks_up_remote_dispatched_run(client):
    agent = create_agent(client, agent_type="remote")
    task = create_task(client)
    runner, key = create_runner_with_key(client, agent_names=agent["name"])
    dispatched = client.post(f"/api/agents/{agent['id']}/dispatch", params={"task_id": task["id"]})
    assert dispatched.status_code == 200, dispatched.text
    run = dispatched.json()
    assert run["status"] == "running"
    assert run["pid"] is None

    response = client.post(f"/api/runners/{runner['id']}/poll", headers=bearer_headers(key))

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["id"].startswith("lease_")
    assert data["runner_id"] == runner["id"]
    assert data["agent_run_id"] == run["id"]
    assert data["task_id"] == task["id"]
    assert data["agent_name"] == agent["name"]
    with client.app.state.SessionLocal() as db:
        runs = db.query(AgentRun).filter(AgentRun.task_id == task["id"]).all()
        assert [stored.id for stored in runs] == [run["id"]]


def test_runner_poll_at_capacity_returns_active_leases(client):
    agent = create_agent(client)
    create_task(client)
    runner, key = create_runner_with_key(client, agent_names=agent["name"], max_concurrent_leases=1)
    first = client.post(f"/api/runners/{runner['id']}/poll", headers=bearer_headers(key))
    assert first.status_code == 200, first.text
    create_task(client, title="Second task")

    response = client.post(f"/api/runners/{runner['id']}/poll", headers=bearer_headers(key))

    assert response.status_code == 200, response.text
    data = response.json()
    assert isinstance(data, list)
    assert [item["id"] for item in data] == [first.json()["id"]]


def test_runner_poll_auth_failures(client):
    create_agent(client)
    runner, key = create_runner_with_key(client, agent_names="implementer")

    wrong = client.post(f"/api/runners/{runner['id']}/poll", headers=bearer_headers(key + "-wrong"))
    assert wrong.status_code == 403

    no_key = create_runner(client, name="no-key-runner", api_key_ref="")
    missing = client.post(f"/api/runners/{no_key['id']}/poll", headers=bearer_headers("anything"))
    assert missing.status_code == 403


def test_runner_poll_disabled_runner(client):
    runner, key = create_runner_with_key(client, enabled=False)

    response = client.post(f"/api/runners/{runner['id']}/poll", headers=bearer_headers(key))

    assert response.status_code == 403


def test_runner_lease_heartbeat_updates_lease_and_run(client):
    create_agent(client)
    create_task(client)
    runner, key = create_runner_with_key(client, agent_names="implementer")
    lease = client.post(f"/api/runners/{runner['id']}/poll", headers=bearer_headers(key)).json()

    response = client.post(
        f"/api/runners/{runner['id']}/leases/{lease['id']}/heartbeat",
        headers=bearer_headers(key),
        json={"runner_pid": 1234, "message": "running tests"},
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["runner_pid"] == 1234
    assert data["runner_message"] == "running tests"
    assert data["last_heartbeat_at"] is not None
    with client.app.state.SessionLocal() as db:
        run = db.get(AgentRun, lease["agent_run_id"])
        assert run.last_heartbeat_at is not None


def test_runner_lease_heartbeat_expired_returns_gone(client):
    create_agent(client)
    create_task(client)
    runner, key = create_runner_with_key(client, agent_names="implementer")
    lease = client.post(f"/api/runners/{runner['id']}/poll", headers=bearer_headers(key)).json()
    with client.app.state.SessionLocal() as db:
        stored = db.get(RunnerLease, lease["id"])
        stored.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()

    response = client.post(
        f"/api/runners/{runner['id']}/leases/{lease['id']}/heartbeat",
        headers=bearer_headers(key),
        json={},
    )

    assert response.status_code == 410


def test_runner_lease_heartbeat_wrong_runner(client):
    create_agent(client)
    create_task(client)
    runner, key = create_runner_with_key(client, agent_names="implementer")
    lease = client.post(f"/api/runners/{runner['id']}/poll", headers=bearer_headers(key)).json()
    other, other_key = create_runner_with_key(client, name="other-runner", agent_names="implementer", api_key_ref="other-key")

    response = client.post(
        f"/api/runners/{other['id']}/leases/{lease['id']}/heartbeat",
        headers=bearer_headers(other_key),
        json={},
    )

    assert response.status_code == 403


def test_runner_lease_complete_success_keeps_task_doing(client):
    create_agent(client)
    task = create_task(client)
    runner, key = create_runner_with_key(client, agent_names="implementer")
    lease = client.post(f"/api/runners/{runner['id']}/poll", headers=bearer_headers(key)).json()

    response = client.post(
        f"/api/runners/{runner['id']}/leases/{lease['id']}/complete",
        headers=bearer_headers(key),
        json={"exit_code": 0, "message": "done"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed"
    assert response.json()["runner_message"] == "done"
    fetched = client.get(f"/api/tasks/{task['id']}").json()
    assert fetched["status"] == "doing"
    assert fetched["assignee"] == "implementer"
    with client.app.state.SessionLocal() as db:
        run = db.get(AgentRun, lease["agent_run_id"])
        assert run.status == "done"


def test_runner_lease_complete_failure_reverts_task(client):
    create_agent(client)
    task = create_task(client)
    runner, key = create_runner_with_key(client, agent_names="implementer")
    lease = client.post(f"/api/runners/{runner['id']}/poll", headers=bearer_headers(key)).json()

    response = client.post(
        f"/api/runners/{runner['id']}/leases/{lease['id']}/complete",
        headers=bearer_headers(key),
        json={"exit_code": 2, "message": "failed"},
    )

    assert response.status_code == 200, response.text
    fetched = client.get(f"/api/tasks/{task['id']}").json()
    assert fetched["status"] == "todo"
    assert fetched["assignee"] is None
    with client.app.state.SessionLocal() as db:
        run = db.get(AgentRun, lease["agent_run_id"])
        assert run.status == "crashed"


def test_runner_lease_complete_already_completed_returns_conflict(client):
    create_agent(client)
    create_task(client)
    runner, key = create_runner_with_key(client, agent_names="implementer")
    lease = client.post(f"/api/runners/{runner['id']}/poll", headers=bearer_headers(key)).json()
    first = client.post(
        f"/api/runners/{runner['id']}/leases/{lease['id']}/complete",
        headers=bearer_headers(key),
        json={"exit_code": 0},
    )
    assert first.status_code == 200, first.text

    response = client.post(
        f"/api/runners/{runner['id']}/leases/{lease['id']}/complete",
        headers=bearer_headers(key),
        json={"exit_code": 0},
    )

    assert response.status_code == 409


def test_stale_recovery_expires_runner_leases(client):
    agent = create_agent(client)
    task = create_task(client)
    runner, _key = create_runner_with_key(client, agent_names=agent["name"])
    with client.app.state.SessionLocal() as db:
        stored_task = db.get(Task, task["id"])
        stored_task.status = "doing"
        stored_task.assignee = agent["name"]
        run = AgentRun(
            id="run_expired_lease",
            agent_id=agent["id"],
            task_id=task["id"],
            status="running",
            started_at=utcnow() - timedelta(minutes=5),
            last_heartbeat_at=utcnow(),
            created_at=utcnow() - timedelta(minutes=5),
            updated_at=utcnow() - timedelta(minutes=5),
        )
        db.add(run)
        db.flush()
        lease = create_runner_lease(db, runner["id"], run.id, utcnow() - timedelta(seconds=1))
        db.commit()

        recovered = stale_recovery(db)
        db.commit()
        db.refresh(lease)
        db.refresh(run)
        db.refresh(stored_task)

        assert recovered == [run.id]
        assert lease.status == "expired"
        assert run.status == "stale"
        assert stored_task.status == "todo"
        assert stored_task.assignee is None
    assert client.get(f"/api/runners/{runner['id']}").json()["status"] == "offline"
