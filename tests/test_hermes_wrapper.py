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


# ---------------------------------------------------------------------------
# build_task_context_bundle tests
# ---------------------------------------------------------------------------


def _task_no_context():
    return {
        "id": "flow_000001",
        "title": "No context task",
        "description": "Simple.",
        "acceptance_criteria": "Works.",
    }


def _task_with_deps():
    return {
        "id": "flow_000002",
        "title": "Dep task",
        "description": "Has deps.",
        "acceptance_criteria": "Done.",
    }


def _dep_summary():
    return {
        "blocked_by_tasks": [
            {"id": "flow_000001", "title": "Parent task", "status": "done"},
        ],
        "blocking_tasks": [
            {"id": "flow_000003", "title": "Child task", "status": "todo"},
        ],
        "parents": [],
        "children": [],
        "blocked_by": [],
        "blocking": [],
        "parent_tasks": [],
        "child_tasks": [],
    }


def _handoff_success():
    return [
        {
            "id": "handoff_000002",
            "task_id": "flow_000002",
            "author": "hermes",
            "summary": "Implemented the feature.",
            "changed_files": ["flow_app/main.py"],
            "commands_run": [],
            "tests_run": ["tests/test_main.py"],
            "artifacts": [],
            "attempted_but_failed": [],
            "remaining_work": "",
            "outcome": "success",
            "next_recommended_agent": "reviewer",
            "capabilities": ["hermes"],
            "created_at": "2026-05-27T12:00:00Z",
        }
    ]


def _handoff_partial():
    return [
        {
            "id": "handoff_000003",
            "task_id": "flow_000002",
            "author": "codex",
            "summary": "Partial implementation.",
            "changed_files": ["flow_app/part.py"],
            "commands_run": ["pytest"],
            "tests_run": [],
            "artifacts": [],
            "attempted_but_failed": ["Database migration failed"],
            "remaining_work": "Fix the migration and add remaining API routes.",
            "outcome": "partial",
            "next_recommended_agent": "implementer",
            "capabilities": ["hermes"],
            "created_at": "2026-05-27T13:00:00Z",
        }
    ]


def test_build_prompt_no_context(monkeypatch):
    """Prompt includes no dependency/handoff sections when none exist."""
    monkeypatch.setattr(hermes_wrapper, "get_dependency_summary", lambda tid: None)
    monkeypatch.setattr(hermes_wrapper, "get_task_handoffs", lambda tid: None)

    cfg = hermes_wrapper.load_config(_env(task_id="flow_000001"))
    prompt = hermes_wrapper.build_prompt(_task_no_context(), cfg)

    assert "## Dependency Context" not in prompt
    assert "## Handoff Context" not in prompt
    assert "Implement the task" in prompt


def test_build_prompt_with_dependencies(monkeypatch):
    """Prompt includes dependency summary when dependencies exist."""
    monkeypatch.setattr(hermes_wrapper, "get_dependency_summary", lambda tid: _dep_summary())
    monkeypatch.setattr(hermes_wrapper, "get_task_handoffs", lambda tid: None)

    cfg = hermes_wrapper.load_config(_env(task_id="flow_000002"))
    prompt = hermes_wrapper.build_prompt(_task_with_deps(), cfg)

    assert "## Dependency Context" in prompt
    assert "Blocked by:" in prompt
    assert "flow_000001" in prompt
    assert "Parent task" in prompt
    assert "status: done" in prompt
    assert "Blocking:" in prompt
    assert "flow_000003" in prompt
    assert "Child task" in prompt
    assert "status: todo" in prompt
    assert "## Handoff Context" not in prompt


def test_build_prompt_with_successful_handoff(monkeypatch):
    """Prompt includes handoff context with success outcome."""
    monkeypatch.setattr(hermes_wrapper, "get_dependency_summary", lambda tid: None)
    monkeypatch.setattr(hermes_wrapper, "get_task_handoffs", lambda tid: _handoff_success())

    cfg = hermes_wrapper.load_config(_env(task_id="flow_000002"))
    prompt = hermes_wrapper.build_prompt(_task_with_deps(), cfg)

    assert "## Handoff Context" in prompt
    assert "Latest handoff by hermes" in prompt
    assert "Outcome: success" in prompt
    assert "Implemented the feature." in prompt
    assert "Tests run: tests/test_main.py" in prompt
    assert "Next recommended agent: reviewer" in prompt
    assert "Changed files: flow_app/main.py" in prompt
    assert "## Dependency Context" not in prompt


def test_build_prompt_with_failed_handoff_remaining_work(monkeypatch):
    """Prompt includes handoff context with remaining_work and attempted_but_failed."""
    monkeypatch.setattr(hermes_wrapper, "get_dependency_summary", lambda tid: None)
    monkeypatch.setattr(hermes_wrapper, "get_task_handoffs", lambda tid: _handoff_partial())

    cfg = hermes_wrapper.load_config(_env(task_id="flow_000002"))
    prompt = hermes_wrapper.build_prompt(_task_with_deps(), cfg)

    assert "## Handoff Context" in prompt
    assert "Latest handoff by codex" in prompt
    assert "Outcome: partial" in prompt
    assert "Partial implementation." in prompt
    assert "Remaining work: Fix the migration and add remaining API routes." in prompt
    assert "Attempted but failed: Database migration failed" in prompt
    assert "Next recommended agent: implementer" in prompt


def test_context_bundle_truncates_long_handoff_field(monkeypatch):
    """Long handoff summary is truncated visibly."""
    long_summary = "A" * 600
    handoffs = [
        {
            "id": "handoff_000004",
            "task_id": "flow_000002",
            "author": "hermes",
            "summary": long_summary,
            "changed_files": [],
            "commands_run": [],
            "tests_run": [],
            "artifacts": [],
            "attempted_but_failed": [],
            "remaining_work": "",
            "outcome": "failed",
            "next_recommended_agent": None,
            "capabilities": [],
            "created_at": "2026-05-27T14:00:00Z",
        }
    ]
    monkeypatch.setattr(hermes_wrapper, "get_dependency_summary", lambda tid: None)
    monkeypatch.setattr(hermes_wrapper, "get_task_handoffs", lambda tid: handoffs)

    block = hermes_wrapper._format_handoff_block(handoffs)
    assert "[truncated]" in block
    assert len(block) <= hermes_wrapper.MAX_CONTEXT_CHARS + 50  # slight wiggle


def test_context_bundle_truncates_entire_block(monkeypatch):
    """Entire handoff block is bounded by MAX_CONTEXT_CHARS when cumulative fields overflow."""
    # Each text field is just under individual truncation limit (500),
    # but many large list fields accumulate past MAX_CONTEXT_CHARS (4000)
    chunk = "x" * 499
    handoffs = [
        {
            "id": "handoff_000005",
            "task_id": "flow_000002",
            "author": "hermes",
            "summary": chunk,
            "changed_files": [chunk] * 5,   # ~2500 chars joined
            "commands_run": [],
            "tests_run": [chunk] * 3,       # ~1500 chars joined
            "artifacts": [],
            "attempted_but_failed": [chunk, chunk, chunk, chunk, chunk, chunk],
            "remaining_work": chunk,
            "outcome": "failed",
            "next_recommended_agent": None,
            "capabilities": [],
            "created_at": "2026-05-27T15:00:00Z",
        }
    ]
    monkeypatch.setattr(hermes_wrapper, "get_dependency_summary", lambda tid: None)
    monkeypatch.setattr(hermes_wrapper, "get_task_handoffs", lambda tid: handoffs)

    block = hermes_wrapper._format_handoff_block(handoffs)
    assert "[context truncated]" in block
    assert len(block) <= hermes_wrapper.MAX_CONTEXT_CHARS + len("\n…[context truncated]")


def test_task_context_bundle_both_deps_and_handoffs(monkeypatch):
    """Context bundle includes both dependency and handoff sections when both exist."""
    monkeypatch.setattr(hermes_wrapper, "get_dependency_summary", lambda tid: _dep_summary())
    monkeypatch.setattr(hermes_wrapper, "get_task_handoffs", lambda tid: _handoff_success())

    bundle = hermes_wrapper.build_task_context_bundle(_task_with_deps())

    assert "## Dependency Context" in bundle
    assert "## Handoff Context" in bundle
    assert "Blocked by:" in bundle
    assert "Latest handoff by hermes" in bundle
    assert "Outcome: success" in bundle


def test_task_context_bundle_empty_both(monkeypatch):
    """Context bundle is empty when neither dependencies nor handoffs exist."""
    monkeypatch.setattr(hermes_wrapper, "get_dependency_summary", lambda tid: None)
    monkeypatch.setattr(hermes_wrapper, "get_task_handoffs", lambda tid: None)

    bundle = hermes_wrapper.build_task_context_bundle(_task_no_context())
    assert bundle == ""
