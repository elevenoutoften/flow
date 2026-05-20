from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from flow_app import hermes_wrapper
from flow_app.main import create_app


def _env(**overrides):
    env = {
        "FLOW_TASK_ID": "flow_000001",
        "FLOW_PROJECT": "default",
        "FLOW_BASE_URL": "http://127.0.0.1:8100",
        "FLOW_API_KEY": "flow_test",
        "FLOW_RUN_ID": "run_000001",
    }
    env.update(overrides)
    return env


def _task():
    return {
        "id": "flow_000001",
        "title": "Ship dogfood",
        "description": "Wire Hermes into Flow.",
        "acceptance_criteria": "Task reaches review.",
    }


def test_wrapper_missing_env_vars():
    with pytest.raises(hermes_wrapper.ConfigError, match="FLOW_TASK_ID"):
        hermes_wrapper.load_config({})


def test_wrapper_claims_task(monkeypatch):
    calls = []

    def fake_request(method, path, body=None, *, query=None):
        calls.append((method, path, body, query))
        if path == "/api/tasks/flow_000001" and method == "GET":
            return _task()
        return {}

    monkeypatch.setattr(hermes_wrapper, "flow_request", fake_request)
    monkeypatch.setattr(hermes_wrapper, "assignee_name", lambda agent_name: "hermes-test")
    monkeypatch.setattr(
        hermes_wrapper,
        "run_hermes",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="done", stderr=""),
    )
    monkeypatch.setattr(hermes_wrapper, "git_changed_files", lambda: [])

    assert hermes_wrapper.execute(hermes_wrapper.load_config(_env())) == 0
    assert ("POST", "/api/tasks/flow_000001/claim", {"agent_name": "hermes-test"}, None) in calls


def test_wrapper_runs_hermes(monkeypatch):
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(hermes_wrapper.subprocess, "run", fake_run)

    result = hermes_wrapper.run_hermes("hermes run", "Prompt text", 123)

    assert result.returncode == 0
    assert captured["args"] == ["hermes", "run", "Prompt text"]
    assert captured["kwargs"]["capture_output"] is True
    assert captured["kwargs"]["text"] is True
    assert captured["kwargs"]["timeout"] == 123


def test_wrapper_success_moves_to_review(monkeypatch):
    calls = []

    def fake_request(method, path, body=None, *, query=None):
        calls.append((method, path, body, query))
        if path == "/api/tasks/flow_000001" and method == "GET":
            return _task()
        return {}

    monkeypatch.setattr(hermes_wrapper, "flow_request", fake_request)
    monkeypatch.setattr(
        hermes_wrapper,
        "run_hermes",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="implemented", stderr=""),
    )
    monkeypatch.setattr(hermes_wrapper, "git_changed_files", lambda: ["flow_app/hermes_wrapper.py"])

    assert hermes_wrapper.execute(hermes_wrapper.load_config(_env())) == 0

    assert ("POST", "/api/tasks/flow_000001/move", {"status": "review"}, None) in calls
    handoff = [call for call in calls if call[1] == "/api/tasks/flow_000001/handoff"][0]
    assert handoff[2]["outcome"] == "success"
    assert handoff[2]["changed_files"] == ["flow_app/hermes_wrapper.py"]


def test_wrapper_failure_keeps_doing(monkeypatch):
    calls = []

    def fake_request(method, path, body=None, *, query=None):
        calls.append((method, path, body, query))
        if path == "/api/tasks/flow_000001" and method == "GET":
            return _task()
        return {}

    monkeypatch.setattr(hermes_wrapper, "flow_request", fake_request)
    monkeypatch.setattr(
        hermes_wrapper,
        "run_hermes",
        lambda *args, **kwargs: SimpleNamespace(returncode=7, stdout="", stderr="boom"),
    )
    monkeypatch.setattr(hermes_wrapper, "git_changed_files", lambda: [])

    assert hermes_wrapper.execute(hermes_wrapper.load_config(_env())) == 7

    assert not any(call[0] == "POST" and call[1] == "/api/tasks/flow_000001/move" for call in calls)
    handoff = [call for call in calls if call[1] == "/api/tasks/flow_000001/handoff"][0]
    assert handoff[2]["outcome"] == "failed"
    assert handoff[2]["remaining_work"]


def test_wrapper_heartbeats(monkeypatch):
    calls = []

    def fake_request(method, path, body=None, *, query=None):
        calls.append((method, path, body, query))
        if path == "/api/tasks/flow_000001" and method == "GET":
            return _task()
        return {}

    monkeypatch.setattr(hermes_wrapper, "flow_request", fake_request)
    monkeypatch.setattr(
        hermes_wrapper,
        "run_hermes",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="done", stderr=""),
    )
    monkeypatch.setattr(hermes_wrapper, "git_changed_files", lambda: [])

    hermes_wrapper.execute(hermes_wrapper.load_config(_env()))

    heartbeats = [call for call in calls if call[1] == "/api/agent-runs/run_000001/heartbeat"]
    assert len(heartbeats) >= 2
    assert all(call[0] == "POST" for call in heartbeats)


def test_wrapper_completes_run(monkeypatch):
    calls = []

    def fake_request(method, path, body=None, *, query=None):
        calls.append((method, path, body, query))
        if path == "/api/tasks/flow_000001" and method == "GET":
            return _task()
        return {}

    monkeypatch.setattr(hermes_wrapper, "flow_request", fake_request)
    monkeypatch.setattr(
        hermes_wrapper,
        "run_hermes",
        lambda *args, **kwargs: SimpleNamespace(returncode=3, stdout="", stderr="failed"),
    )
    monkeypatch.setattr(hermes_wrapper, "git_changed_files", lambda: [])

    hermes_wrapper.execute(hermes_wrapper.load_config(_env()))

    assert ("POST", "/api/agent-runs/run_000001/complete", None, {"exit_code": "3"}) in calls


def test_wrapper_least_privilege_endpoints(tmp_path):
    app = create_app(f"sqlite:///{tmp_path / 'flow.sqlite'}", trusted_headers=True)
    admin_headers = {"X-Axis-Admin": "1", "X-Axis-User": "test-admin"}
    with TestClient(app) as client:
        key_response = client.post(
            "/api/api-keys",
            json={"name": "wrapper-impl", "role": "implementer"},
            headers=admin_headers,
        )
        assert key_response.status_code == 201, key_response.text
        headers = {"Authorization": f"Bearer {key_response.json()['api_key']}"}

        task_response = client.post(
            "/api/tasks",
            json={"title": "Wrapper least privilege", "status": "todo", "project": "default"},
            headers=admin_headers,
        )
        assert task_response.status_code == 201, task_response.text
        task_id = task_response.json()["id"]

        claimed = client.post(f"/api/tasks/{task_id}/claim", json={"agent_name": "hermes-test"}, headers=headers)
        assert claimed.status_code == 200, claimed.text
        assert claimed.json()["status"] == "doing"
        assert claimed.json()["assignee"] == "hermes-test"

        moved = client.post(f"/api/tasks/{task_id}/move", json={"status": "review"}, headers=headers)
        assert moved.status_code == 200, moved.text
        assert moved.json()["status"] == "review"

        noted = client.post(
            f"/api/tasks/{task_id}/note",
            json={"note": "Ready for review.", "author": "hermes"},
            headers=headers,
        )
        assert noted.status_code == 200, noted.text
        assert noted.json()["notes"][-1]["body"] == "Ready for review."

        handoff = client.post(
            f"/api/tasks/{task_id}/handoff",
            json={
                "author": "hermes",
                "summary": "Implemented wrapper endpoint changes.",
                "changed_files": ["flow_app/hermes_wrapper.py"],
                "commands_run": ["pytest tests/test_hermes_wrapper.py"],
                "tests_run": [],
                "artifacts": [],
                "attempted_but_failed": [],
                "remaining_work": "",
                "outcome": "success",
                "next_recommended_agent": "reviewer",
                "capabilities": ["hermes", "dogfood"],
            },
            headers=headers,
        )
        assert handoff.status_code == 201, handoff.text
        assert handoff.json()["task_id"] == task_id
