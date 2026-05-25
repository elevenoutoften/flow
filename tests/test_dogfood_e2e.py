#!/usr/bin/env python3
"""DOGFOOD E2E smoke test: verify dogfood agent dispatch and review loop works end-to-end.

Test plan:
1. Create a test-project todo task
2. Register a test implementer agent with dispatch_statuses="todo"
3. Dispatch agent WITHOUT task_id → verify run.task_id matches our test task
4. Verify dispatch skips human_required tasks and tasks in wrong statuses
5. Verify no tasks outside test-project are claimed
6. Move task through review lifecycle with implementer + reviewer keys
7. Cleanup: revoke test keys, disable test agents
"""
from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from flow_app.bootstrap import main as bootstrap_main
from flow_app.main import create_app
from flow_app.models import AutomationRule
from flow_app.rules_engine import match_rules

ADMIN_HEADERS = {"X-Axis-Admin": "1", "X-Axis-User": "test-admin"}


def _create_api_key(client: TestClient, name: str, role: str) -> tuple[str, str]:
    """Create an API key and return (key_id, key_value)."""
    r = client.post("/api/api-keys", json={"name": name, "role": role}, headers=ADMIN_HEADERS)
    assert r.status_code == 201, f"Failed to create {role} key: {r.text}"
    data = r.json()
    return data["id"], data["api_key"]


def _create_agent(client: TestClient, name: str, dispatch_statuses: str) -> dict:
    """Create a test agent with given dispatch_statuses and empty capabilities (matches all tasks)."""
    import sys

    command = f'{sys.executable} -c "print(\'dispatched\')"'
    r = client.post("/api/agents", json={
        "name": name,
        "command": command,
        "dispatch_statuses": dispatch_statuses,
        "capabilities": "",
        "env_allowlist": "FLOW_TASK_ID,FLOW_PROJECT,FLOW_BASE_URL,FLOW_API_KEY,FLOW_RUN_ID,HOME,PATH",
    }, headers=ADMIN_HEADERS)
    assert r.status_code == 201, f"Failed to create agent {name}: {r.text}"
    return r.json()


def _bootstrap_key(output: str, name: str) -> str:
    match = re.search(rf"API key: {re.escape(name)} .* - (flow_[A-Za-z0-9_-]+)", output)
    assert match is not None, f"Missing printed key for {name}: {output}"
    return match.group(1)


def _add_handoff(client: TestClient, task_id: str, headers: dict[str, str], author: str) -> None:
    handoff_r = client.post(f"/api/tasks/{task_id}/handoff", json={
        "author": author,
        "summary": "Implementation complete and ready for review.",
        "changed_files": ["flow_app/bootstrap.py"],
        "commands_run": ["pytest tests/test_dogfood_e2e.py"],
        "tests_run": ["tests/test_dogfood_e2e.py"],
        "artifacts": [],
        "attempted_but_failed": [],
        "remaining_work": "",
        "outcome": "success",
        "next_recommended_agent": "reviewer-agent",
        "capabilities": ["review"],
    }, headers=headers)
    assert handoff_r.status_code == 201, handoff_r.text


class TestDogfoodE2E:
    """End-to-end smoke test for the dogfood agent loop."""

    def test_implementer_dispatch_gets_correct_task(self, tmp_path, monkeypatch):
        """DOGFOOD-04: Dispatch without task_id selects the correct eligible task."""
        from types import SimpleNamespace

        def fake_popen(args, **kwargs):
            return SimpleNamespace(pid=99999)

        monkeypatch.setattr("flow_app.dispatcher.subprocess.Popen", fake_popen)
        monkeypatch.setattr("flow_app.dispatcher.threading.Thread", _NoopThread)

        app = create_app(f"sqlite:///{tmp_path / 'flow.sqlite'}", trusted_headers=True)
        with TestClient(app) as client:
            # Create tasks in different projects and statuses
            impl_key_id, impl_key = _create_api_key(client, "smoke-impl", "implementer")
            rev_key_id, rev_key = _create_api_key(client, "smoke-rev", "reviewer")

            # Test project tasks
            test_todo = client.post("/api/tasks", json={
                "title": "Test-project todo task",
                "status": "todo",
                "priority": 50,
                "project": "test-project",
            }, headers=ADMIN_HEADERS).json()

            # Wrong-status task (should NOT be dispatched to)
            test_backlog = client.post("/api/tasks", json={
                "title": "Test-project backlog task",
                "status": "backlog",
                "priority": 100,  # higher priority but wrong status
                "project": "test-project",
            }, headers=ADMIN_HEADERS).json()

            # Human-required task (should be skipped)
            test_human = client.post("/api/tasks", json={
                "title": "Test-project human-required task",
                "status": "todo",
                "priority": 200,  # highest priority but human required
                "project": "test-project",
                "human_required": True,
                "blocker_reason": "Needs human input",
            }, headers=ADMIN_HEADERS).json()

            # Different project task (should NOT be dispatched to)
            other_todo = client.post("/api/tasks", json={
                "title": "Other-project todo task",
                "status": "todo",
                "priority": 10,
                "project": "other-project",
            }, headers=ADMIN_HEADERS).json()

            # Create agent with dispatch_statuses="todo"
            agent = _create_agent(client, "smoke-impl-agent", "todo")

            # Dispatch without task_id
            dispatch_r = client.post(
                f"/api/agents/{agent['id']}/dispatch",
                headers=ADMIN_HEADERS,
            )
            assert dispatch_r.status_code == 200, f"Dispatch failed: {dispatch_r.text}"
            run = dispatch_r.json()

            # Verify run.task_id matches our test-project todo task
            assert run["task_id"] == test_todo["id"], (
                f"Expected task {test_todo['id']} but got {run['task_id']}"
            )

            # Verify no tasks outside test-project were claimed
            assert client.get(f"/api/tasks/{test_backlog['id']}", headers=ADMIN_HEADERS).json()["assignee"] is None
            assert client.get(f"/api/tasks/{test_human['id']}", headers=ADMIN_HEADERS).json()["assignee"] is None
            assert client.get(f"/api/tasks/{other_todo['id']}", headers=ADMIN_HEADERS).json()["assignee"] is None

    def test_reviewer_dispatch_skips_human_required(self, tmp_path, monkeypatch):
        """DOGFOOD-04: Reviewer agent skips human-required review tasks."""
        from types import SimpleNamespace

        def fake_popen(args, **kwargs):
            return SimpleNamespace(pid=99999)

        monkeypatch.setattr("flow_app.dispatcher.subprocess.Popen", fake_popen)
        monkeypatch.setattr("flow_app.dispatcher.threading.Thread", _NoopThread)

        app = create_app(f"sqlite:///{tmp_path / 'flow.sqlite'}", trusted_headers=True)
        with TestClient(app) as client:
            # Create review tasks
            regular_review = client.post("/api/tasks", json={
                "title": "Regular review task",
                "status": "review",
                "priority": 50,
                "project": "test-project",
            }, headers=ADMIN_HEADERS).json()

            human_review = client.post("/api/tasks", json={
                "title": "Human-required review task",
                "status": "review",
                "priority": 200,
                "project": "test-project",
                "human_required": True,
                "blocker_reason": "Needs human reviewer",
            }, headers=ADMIN_HEADERS).json()

            # Create reviewer agent with dispatch_statuses="review"
            agent = _create_agent(client, "smoke-rev-agent", "review")

            dispatch_r = client.post(
                f"/api/agents/{agent['id']}/dispatch",
                headers=ADMIN_HEADERS,
            )
            assert dispatch_r.status_code == 200, f"Dispatch failed: {dispatch_r.text}"
            run = dispatch_r.json()

            # Should pick the regular review task, NOT the human-required one
            assert run["task_id"] == regular_review["id"], (
                f"Expected {regular_review['id']} but got {run['task_id']}"
            )
            assert client.get(f"/api/tasks/{human_review['id']}", headers=ADMIN_HEADERS).json()["assignee"] is None

    def test_full_dogfood_lifecycle_with_scoped_keys(self, tmp_path):
        """DOGFOOD-04: Full lifecycle: claim → note → handoff → move to review → reviewer done."""
        app = create_app(f"sqlite:///{tmp_path / 'flow.sqlite'}", trusted_headers=True)
        with TestClient(app) as client:
            # Create scoped API keys
            impl_key_id, impl_key = _create_api_key(client, "lifecycle-impl", "implementer")
            rev_key_id, rev_key = _create_api_key(client, "lifecycle-rev", "reviewer")
            impl_headers = {"Authorization": f"Bearer {impl_key}"}
            rev_headers = {"Authorization": f"Bearer {rev_key}"}

            # Create a task
            task_r = client.post("/api/tasks", json={
                "title": "Lifecycle test task",
                "status": "todo",
                "priority": 50,
                "project": "test-project",
            }, headers=ADMIN_HEADERS)
            assert task_r.status_code == 201
            task_id = task_r.json()["id"]

            # Step 1: Implementer claims task
            claim_r = client.post(f"/api/tasks/{task_id}/claim", json={"agent_name": "test-impl"}, headers=impl_headers)
            assert claim_r.status_code == 200, f"Claim failed: {claim_r.text}"
            assert claim_r.json()["status"] == "doing"
            assert claim_r.json()["assignee"] == "test-impl"

            # Step 2: Implementer adds progress note
            note_r = client.post(f"/api/tasks/{task_id}/note", json={
                "note": "Implementation complete. Passes all tests.",
                "author": "test-impl",
            }, headers=impl_headers)
            assert note_r.status_code == 200

            # Step 3: Implementer creates handoff
            handoff_r = client.post(f"/api/tasks/{task_id}/handoff", json={
                "author": "test-impl",
                "summary": "Implemented feature X. Ready for review.",
                "changed_files": ["flow_app/hermes_wrapper.py", "tests/test_dogfood_e2e.py"],
                "commands_run": ["pytest tests/"],
                "tests_run": ["tests/test_dogfood_e2e.py"],
                "artifacts": [],
                "attempted_but_failed": [],
                "remaining_work": "",
                "outcome": "success",
                "next_recommended_agent": "reviewer",
                "capabilities": ["test", "dogfood"],
            }, headers=impl_headers)
            assert handoff_r.status_code == 201
            handoff = handoff_r.json()
            assert handoff["outcome"] == "success"
            assert handoff["next_recommended_agent"] == "reviewer"

            # Step 4: Implementer moves to review
            move_r = client.post(f"/api/tasks/{task_id}/move", json={"status": "review"}, headers=impl_headers)
            assert move_r.status_code == 200, f"Move to review failed: {move_r.text}"
            assert move_r.json()["status"] == "review"

            # Step 5: Reviewer adds review note
            rev_note_r = client.post(f"/api/tasks/{task_id}/note", json={
                "note": "LGTM. Approving.",
                "author": "test-rev",
            }, headers=rev_headers)
            assert rev_note_r.status_code == 200

            # Step 6: Reviewer marks done
            done_r = client.post(f"/api/tasks/{task_id}/done", json={
                "summary": "Reviewed and approved. All checks pass.",
                "author": "test-rev",
            }, headers=rev_headers)
            assert done_r.status_code == 200, f"Done failed: {done_r.text}"
            assert done_r.json()["status"] == "done"

            # Cleanup: revoke test keys
            client.post(f"/api/api-keys/{impl_key_id}/revoke", headers=ADMIN_HEADERS)
            client.post(f"/api/api-keys/{rev_key_id}/revoke", headers=ADMIN_HEADERS)

    def test_review_loop_approve_and_sendback(self, tmp_path, capsys, monkeypatch):
        """REVIEW-02: Bootstrap review rules support approve and send-back paths."""
        dispatch_calls = []

        def fake_dispatch_one(db, agent_model, task_model, api_key, base_url):
            dispatch_calls.append(
                {
                    "agent": agent_model.name,
                    "task_id": task_model.id,
                    "api_key": api_key,
                    "base_url": base_url,
                }
            )
            return SimpleNamespace(id=f"run_review_{len(dispatch_calls)}")

        monkeypatch.setattr("flow_app.dispatcher.dispatch_one", fake_dispatch_one)

        db_url = f"sqlite:///{tmp_path / 'flow.sqlite'}"
        app = create_app(db_url, trusted_headers=True, session_secret="test-secret-for-testing")
        assert bootstrap_main(["--database-url", db_url]) == 0
        output = capsys.readouterr().out
        impl_key = _bootstrap_key(output, "impl-key")
        reviewer_key = _bootstrap_key(output, "reviewer-key")
        impl_headers = {"Authorization": f"Bearer {impl_key}"}
        rev_headers = {"Authorization": f"Bearer {reviewer_key}"}

        with TestClient(app) as client:
            approve_task = client.post("/api/tasks", json={
                "title": "Approve review-loop task",
                "status": "todo",
                "priority": 50,
                "project": "test-project",
            }, headers=ADMIN_HEADERS)
            assert approve_task.status_code == 201, approve_task.text
            approve_task_id = approve_task.json()["id"]

            claim_r = client.post(
                f"/api/tasks/{approve_task_id}/claim",
                json={"agent_name": "test-impl"},
                headers=impl_headers,
            )
            assert claim_r.status_code == 200, claim_r.text
            note_r = client.post(
                f"/api/tasks/{approve_task_id}/note",
                json={"note": "Implementation complete.", "author": "test-impl"},
                headers=impl_headers,
            )
            assert note_r.status_code == 200, note_r.text
            _add_handoff(client, approve_task_id, impl_headers, "test-impl")
            review_r = client.post(f"/api/tasks/{approve_task_id}/move", json={"status": "review"}, headers=impl_headers)
            assert review_r.status_code == 200, review_r.text

            reviewer_claim_r = client.post(
                f"/api/tasks/{approve_task_id}/claim",
                json={"agent_name": "reviewer-agent"},
                headers=rev_headers,
            )
            assert reviewer_claim_r.status_code == 200, reviewer_claim_r.text
            reviewer_note_r = client.post(
                f"/api/tasks/{approve_task_id}/note",
                json={"note": "Approved after review."},
                headers=rev_headers,
            )
            assert reviewer_note_r.status_code == 200, reviewer_note_r.text
            done_r = client.post(
                f"/api/tasks/{approve_task_id}/done",
                json={"summary": "Approved.", "author": "test-reviewer"},
                headers=rev_headers,
            )
            assert done_r.status_code == 200, done_r.text
            assert done_r.json()["status"] == "done"

            sendback_task = client.post("/api/tasks", json={
                "title": "Send back review-loop task",
                "status": "todo",
                "priority": 50,
                "project": "test-project",
            }, headers=ADMIN_HEADERS)
            assert sendback_task.status_code == 201, sendback_task.text
            sendback_task_id = sendback_task.json()["id"]

            claim_r = client.post(
                f"/api/tasks/{sendback_task_id}/claim",
                json={"agent_name": "test-impl"},
                headers=impl_headers,
            )
            assert claim_r.status_code == 200, claim_r.text
            note_r = client.post(
                f"/api/tasks/{sendback_task_id}/note",
                json={"note": "Second implementation complete.", "author": "test-impl"},
                headers=impl_headers,
            )
            assert note_r.status_code == 200, note_r.text
            _add_handoff(client, sendback_task_id, impl_headers, "test-impl")
            review_r = client.post(f"/api/tasks/{sendback_task_id}/move", json={"status": "review"}, headers=impl_headers)
            assert review_r.status_code == 200, review_r.text

            reviewer_claim_r = client.post(
                f"/api/tasks/{sendback_task_id}/claim",
                json={"agent_name": "reviewer-agent"},
                headers=rev_headers,
            )
            assert reviewer_claim_r.status_code == 200, reviewer_claim_r.text
            reviewer_note_r = client.post(
                f"/api/tasks/{sendback_task_id}/note",
                json={"note": "Please address the rejection feedback."},
                headers=rev_headers,
            )
            assert reviewer_note_r.status_code == 200, reviewer_note_r.text
            todo_r = client.post(f"/api/tasks/{sendback_task_id}/move", json={"status": "todo"}, headers=rev_headers)
            assert todo_r.status_code == 200, todo_r.text
            assert todo_r.json()["status"] == "todo"

            no_handoff_task = client.post("/api/tasks", json={
                "title": "Review task without handoff",
                "status": "review",
                "priority": 10,
                "project": "test-project",
            }, headers=ADMIN_HEADERS)
            assert no_handoff_task.status_code == 201, no_handoff_task.text
            no_handoff_task_id = no_handoff_task.json()["id"]

        assert len(dispatch_calls) == 2
        assert {call["agent"] for call in dispatch_calls} == {"reviewer-agent"}
        assert {call["api_key"] for call in dispatch_calls} == {reviewer_key}
        assert {call["base_url"] for call in dispatch_calls} == {"http://localhost:8100"}

        with app.state.SessionLocal() as db:
            route_rule = db.scalars(select(AutomationRule).where(AutomationRule.name == "route-review-tasks")).one()
            assert json.loads(route_rule.actions)[0]["api_key"] == reviewer_key
            route_matches = match_rules(db, "task_moved", task_id=approve_task_id, data={"status": "review"})
            assert "route-review-tasks" in {match.rule_name for match in route_matches}

            handoff_rule = db.scalars(select(AutomationRule).where(AutomationRule.name == "warn-missing-handoff")).one()
            assert {"field": "latest_handoff", "operator": "not_exists"} in json.loads(handoff_rule.conditions)
            no_handoff_matches = match_rules(db, "task_moved", task_id=no_handoff_task_id)
            assert "warn-missing-handoff" in {match.rule_name for match in no_handoff_matches}


class _NoopThread:
    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        pass
