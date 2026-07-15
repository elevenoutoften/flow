"""Tests for Agent Registry, Dispatcher, AgentRun lifecycle, and permissions."""
from __future__ import annotations

import json
import sys
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from flow_app.database import Base, build_engine, build_session_factory
from flow_app.dispatcher import DispatchError, _next_capable_task, complete_run, dispatch_loop, dispatch_one, stale_recovery
from flow_app.main import create_app
from flow_app.models import AgentApiKey, AgentRun, ApiKeyRole, Task, utcnow
from flow_app.repository import create_agent as repo_create_agent
from flow_app.repository import create_task as repo_create_task
from flow_app.repository import create_task_link as repo_create_task_link
from flow_app.repository import create_workspace_config as repo_create_workspace_config
from flow_app.repository import is_dispatch_ready, next_task
from flow_app.schemas import AgentCreate, TaskCreate, TaskLinkCreate, WorkspaceConfigCreate


# ---------- helpers ----------

ADMIN_HEADERS = {"X-Axis-Admin": "1", "X-Axis-User": "test-admin"}


def _python_command(code: str) -> str:
    return f'"{sys.executable}" -c "{code}"'


def _success_command() -> str:
    return _python_command("print('hello')")


def _running_command() -> str:
    return _python_command("import time; time.sleep(10)")


@pytest.fixture
def default_command():
    """Cross-platform command that exits 0."""
    return _success_command()


def _client(tmp_path):
    app = create_app(f"sqlite:///{tmp_path / 'flow.sqlite'}", trusted_headers=True)
    tc = TestClient(app, raise_server_exceptions=False)
    tc.headers.update(ADMIN_HEADERS)
    return tc


def bearer_headers(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


def create_agent(client, **overrides):
    payload = {
        "name": "codex-agent",
        "description": "Test agent",
        "command": _running_command(),
        "capabilities": "code,testing",
        "max_concurrency": 2,
    }
    payload.update(overrides)
    r = client.post("/api/agents", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def create_task(client, **overrides):
    payload = {
        "title": "Test task",
        "status": "todo",
        "priority": 50,
        "project": "default",
    }
    payload.update(overrides)
    r = client.post("/api/tasks", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _db(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'flow.sqlite'}")
    Base.metadata.create_all(bind=engine)
    return build_session_factory(engine)()


class _NoopThread:
    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        pass


def _link_tasks(db, parent_id: str, child_id: str, link_type: str = "blocks"):
    return repo_create_task_link(
        db,
        TaskLinkCreate(parent_id=parent_id, child_id=child_id, link_type=link_type),
    )


# ---------- Agent CRUD ----------


class TestAgentCRUD:
    def test_create_agent(self, tmp_path, default_command):
        with _client(tmp_path) as c:
            agent = create_agent(c, command=default_command, command_allowlist=f"{sys.executable},python3")
            assert agent["id"].startswith("agent_")
            assert agent["name"] == "codex-agent"
            assert agent["enabled"] is True
            assert agent["agent_type"] == "cli"
            assert agent["capabilities"] == "code,testing"
            assert agent["command_allowlist"] == f"{sys.executable},python3"

    def test_list_agents(self, tmp_path):
        with _client(tmp_path) as c:
            create_agent(c, name="agent-a")
            create_agent(c, name="agent-b")
            r = c.get("/api/agents")
            assert r.status_code == 200
            data = r.json()
            assert len(data) == 2
            assert [a["name"] for a in data] == ["agent-a", "agent-b"]

    def test_list_agents_enabled_only(self, tmp_path):
        with _client(tmp_path) as c:
            create_agent(c, name="enabled-agent")
            a2 = create_agent(c, name="disabled-agent")
            c.patch(f"/api/agents/{a2['id']}", json={"enabled": False})
            r = c.get("/api/agents", params={"enabled_only": "true"})
            assert r.status_code == 200
            data = r.json()
            assert len(data) == 1
            assert data[0]["name"] == "enabled-agent"

    def test_get_agent(self, tmp_path):
        with _client(tmp_path) as c:
            agent = create_agent(c)
            r = c.get(f"/api/agents/{agent['id']}")
            assert r.status_code == 200
            assert r.json()["name"] == "codex-agent"

    def test_get_agent_not_found(self, tmp_path):
        with _client(tmp_path) as c:
            r = c.get("/api/agents/nonexistent")
            assert r.status_code == 404

    def test_update_agent(self, tmp_path):
        with _client(tmp_path) as c:
            agent = create_agent(c)
            r = c.patch(
                f"/api/agents/{agent['id']}",
                json={"description": "updated", "enabled": False, "command_allowlist": "python3"},
            )
            assert r.status_code == 200
            assert r.json()["description"] == "updated"
            assert r.json()["enabled"] is False
            assert r.json()["command_allowlist"] == "python3"

    def test_create_agent_requires_name(self, tmp_path):
        with _client(tmp_path) as c:
            r = c.post("/api/agents", json={"description": "no name"})
            assert r.status_code == 422

    def test_create_agent_duplicate_name(self, tmp_path):
        with _client(tmp_path) as c:
            create_agent(c, name="unique-name")
            r = c.post("/api/agents", json={"name": "unique-name"})
            # IntegrityError from unique constraint: 409 (if caught) or 500 (unhandled flush)
            assert r.status_code in (400, 409, 500)

    def test_agent_type_validation(self, tmp_path):
        with _client(tmp_path) as c:
            r = c.post("/api/agents", json={"name": "bad-agent", "agent_type": "invalid"})
            assert r.status_code == 422

            agent = create_agent(c, agent_type="remote")
            r = c.patch(f"/api/agents/{agent['id']}", json={"agent_type": "invalid"})
            assert r.status_code == 422


class TestDispatchStatuses:
    def test_dispatch_statuses_default(self, tmp_path):
        with _client(tmp_path) as c:
            agent = create_agent(c)
            assert agent["dispatch_statuses"] == "backlog,todo"

    def test_dispatch_statuses_custom(self, tmp_path):
        with _client(tmp_path) as c:
            agent = create_agent(c, dispatch_statuses="review")
            assert agent["dispatch_statuses"] == "review"

    def test_dispatch_statuses_filter_by_status(self, tmp_path, monkeypatch):
        db = _db(tmp_path)

        def fake_popen(args, **kwargs):
            return SimpleNamespace(pid=12345)

        monkeypatch.setattr("flow_app.dispatcher.subprocess.Popen", fake_popen)
        monkeypatch.setattr("flow_app.dispatcher.threading.Thread", _NoopThread)
        try:
            repo_create_agent(
                db,
                AgentCreate(
                    name="implementer",
                    command=_success_command(),
                    dispatch_statuses="backlog,todo",
                ),
            )
            repo_create_agent(
                db,
                AgentCreate(
                    name="reviewer",
                    command=_success_command(),
                    dispatch_statuses="review",
                ),
            )
            todo_task = repo_create_task(db, TaskCreate(title="Implement code", status="todo"))
            review_task = repo_create_task(db, TaskCreate(title="Review code", status="review"))

            impl_runs = dispatch_loop(
                db,
                "implementer",
                api_key="key",
                base_url="http://flow.test",
                continuous=False,
                interval=0,
            )
            review_runs = dispatch_loop(
                db,
                "reviewer",
                api_key="key",
                base_url="http://flow.test",
                continuous=False,
                interval=0,
            )

            assert [run.task_id for run in impl_runs] == [todo_task.id]
            assert [run.task_id for run in review_runs] == [review_task.id]
        finally:
            db.close()


class TestDispatchDependencies:
    def test_next_task_skips_blocked_child(self, tmp_path):
        db = _db(tmp_path)
        try:
            parent = repo_create_task(db, TaskCreate(title="Parent", status="todo", assignee="human"))
            child = repo_create_task(db, TaskCreate(title="Child", status="todo"))
            _link_tasks(db, parent.id, child.id, "blocks")

            assert next_task(db) is None

            parent.status = "done"
            db.flush()

            assert next_task(db).id == child.id
        finally:
            db.close()

    def test_next_task_skips_human_required(self, tmp_path):
        db = _db(tmp_path)
        try:
            task = repo_create_task(db, TaskCreate(title="Needs human", status="todo", human_required=True))

            assert next_task(db) is None

            task.human_required = 0
            db.flush()

            assert next_task(db).id == task.id
        finally:
            db.close()

    def test_next_capable_task_respects_dependencies(self, tmp_path):
        db = _db(tmp_path)
        try:
            agent = repo_create_agent(db, AgentCreate(name="worker", command=_success_command()))
            parent = repo_create_task(db, TaskCreate(title="Parent", status="todo", assignee="human"))
            child = repo_create_task(db, TaskCreate(title="Child", status="todo"))
            _link_tasks(db, parent.id, child.id, "depends_on")

            assert _next_capable_task(db, agent) is None

            parent.status = "done"
            db.flush()

            assert _next_capable_task(db, agent).id == child.id
        finally:
            db.close()

    def test_dispatch_one_rejects_blocked_task(self, tmp_path, monkeypatch):
        db = _db(tmp_path)

        def fake_popen(args, **kwargs):
            return SimpleNamespace(pid=12345)

        monkeypatch.setattr("flow_app.dispatcher.subprocess.Popen", fake_popen)
        monkeypatch.setattr("flow_app.dispatcher.threading.Thread", _NoopThread)
        try:
            agent = repo_create_agent(db, AgentCreate(name="worker", command=_success_command()))
            parent = repo_create_task(db, TaskCreate(title="Parent", status="todo"))
            child = repo_create_task(db, TaskCreate(title="Child", status="todo"))
            _link_tasks(db, parent.id, child.id, "blocks")

            with pytest.raises(DispatchError, match="unresolved blocking dependencies"):
                dispatch_one(db, agent, child, api_key="key", base_url="http://flow.test")

            parent.status = "done"
            db.flush()

            run = dispatch_one(db, agent, child, api_key="key", base_url="http://flow.test")

            assert run.task_id == child.id
        finally:
            db.close()

    def test_is_dispatch_ready_with_related_link(self, tmp_path):
        db = _db(tmp_path)
        try:
            parent = repo_create_task(db, TaskCreate(title="Parent", status="todo"))
            child = repo_create_task(db, TaskCreate(title="Child", status="todo"))
            _link_tasks(db, parent.id, child.id, "related")

            assert is_dispatch_ready(db, child) is True
        finally:
            db.close()

    def test_is_dispatch_ready_all_blocking_parents_done(self, tmp_path):
        db = _db(tmp_path)
        try:
            first = repo_create_task(db, TaskCreate(title="First parent", status="done"))
            second = repo_create_task(db, TaskCreate(title="Second parent", status="done"))
            child = repo_create_task(db, TaskCreate(title="Child", status="todo"))
            _link_tasks(db, first.id, child.id, "depends_on")
            _link_tasks(db, second.id, child.id, "depends_on")

            assert is_dispatch_ready(db, child) is True
        finally:
            db.close()


class TestCommandAllowlist:
    def test_dispatch_rejects_command_not_in_allowlist(self, tmp_path, monkeypatch):
        db = _db(tmp_path)
        popen_called = False
        workspace_root = tmp_path / "scratch"

        def fake_popen(args, **kwargs):
            nonlocal popen_called
            popen_called = True
            return SimpleNamespace(pid=12345)

        monkeypatch.setattr("flow_app.dispatcher.subprocess.Popen", fake_popen)
        try:
            agent = repo_create_agent(
                db,
                AgentCreate(name="worker", command=_success_command(), command_allowlist="codex,python3"),
            )
            task = repo_create_task(db, TaskCreate(title="Blocked command", status="todo"))
            repo_create_workspace_config(
                db,
                WorkspaceConfigCreate(
                    name="default",
                    strategy="scratch_dir",
                    scratch_root=str(workspace_root),
                ),
            )

            with pytest.raises(DispatchError, match="Command not in allowlist"):
                dispatch_one(db, agent, task, api_key="key", base_url="http://flow.test")

            db.expire_all()
            refreshed_task = db.get(Task, task.id)
            assert popen_called is False
            assert refreshed_task is not None
            assert refreshed_task.assignee is None
            assert refreshed_task.status == "todo"
            assert db.query(AgentRun).count() == 0
            assert not (workspace_root / task.id).exists()
        finally:
            db.close()

    def test_dispatch_allows_command_matching_allowlist_prefix(self, tmp_path, monkeypatch):
        db = _db(tmp_path)
        captured = {}

        def fake_popen(args, **kwargs):
            captured["args"] = args
            return SimpleNamespace(pid=12345)

        monkeypatch.setattr("flow_app.dispatcher.subprocess.Popen", fake_popen)
        monkeypatch.setattr("flow_app.dispatcher.threading.Thread", _NoopThread)
        try:
            command = _success_command()
            agent = repo_create_agent(
                db,
                AgentCreate(name="worker", command=command, command_allowlist=f'codex,"{sys.executable}"'),
            )
            task = repo_create_task(db, TaskCreate(title="Allowed command", status="todo"))

            run = dispatch_one(db, agent, task, api_key="key", base_url="http://flow.test")

            assert run.pid == 12345
            assert captured["args"] == [sys.executable, "-c", "print('hello')"]
        finally:
            db.close()

    def test_dispatch_empty_allowlist_allows_all_commands(self, tmp_path, monkeypatch):
        db = _db(tmp_path)
        captured = {}

        def fake_popen(args, **kwargs):
            captured["args"] = args
            return SimpleNamespace(pid=12345)

        monkeypatch.setattr("flow_app.dispatcher.subprocess.Popen", fake_popen)
        monkeypatch.setattr("flow_app.dispatcher.threading.Thread", _NoopThread)
        try:
            agent = repo_create_agent(db, AgentCreate(name="worker", command=_success_command()))
            task = repo_create_task(db, TaskCreate(title="Backward compatible", status="todo"))

            run = dispatch_one(db, agent, task, api_key="key", base_url="http://flow.test")

            assert run.pid == 12345
            assert captured["args"] == [sys.executable, "-c", "print('hello')"]
        finally:
            db.close()

    def test_dispatch_skip_human_required(self, tmp_path, monkeypatch):
        db = _db(tmp_path)

        def fake_popen(args, **kwargs):
            return SimpleNamespace(pid=12345)

        monkeypatch.setattr("flow_app.dispatcher.subprocess.Popen", fake_popen)
        monkeypatch.setattr("flow_app.dispatcher.threading.Thread", _NoopThread)
        try:
            repo_create_agent(db, AgentCreate(name="worker", command=_success_command()))
            task = repo_create_task(db, TaskCreate(title="Needs a human", status="todo", human_required=True))

            runs = dispatch_loop(
                db,
                "worker",
                api_key="key",
                base_url="http://flow.test",
                continuous=False,
                interval=0,
            )

            assert runs == []
            assert task.assignee is None
        finally:
            db.close()

    def test_dispatch_statuses_update(self, tmp_path):
        with _client(tmp_path) as c:
            agent = create_agent(c)
            r = c.patch(f"/api/agents/{agent['id']}", json={"dispatch_statuses": "review"})
            assert r.status_code == 200, r.text
            assert r.json()["dispatch_statuses"] == "review"

            r = c.get(f"/api/agents/{agent['id']}")
            assert r.status_code == 200
            assert r.json()["dispatch_statuses"] == "review"

    def test_dispatch_statuses_validation(self, tmp_path):
        with _client(tmp_path) as c:
            r = c.post("/api/agents", json={"name": "bad-agent", "dispatch_statuses": "invalid"})
            assert r.status_code == 422

    def test_dispatch_without_task_id_uses_dispatch_statuses(self, tmp_path, monkeypatch):
        def fake_popen(args, **kwargs):
            return SimpleNamespace(pid=12345)

        monkeypatch.setattr("flow_app.dispatcher.subprocess.Popen", fake_popen)
        monkeypatch.setattr("flow_app.dispatcher.threading.Thread", _NoopThread)

        with _client(tmp_path) as c:
            agent = create_agent(c, capabilities="", dispatch_statuses="todo")
            backlog_task = create_task(c, title="Backlog task", status="backlog", priority=100)
            todo_task = create_task(c, title="Todo task", status="todo", priority=50)
            human_required = create_task(
                c,
                title="Human todo task",
                status="todo",
                priority=100,
                human_required=True,
                blocker_reason="Needs human input.",
            )

            r = c.post(f"/api/agents/{agent['id']}/dispatch")
            assert r.status_code == 200, r.text
            assert r.json()["task_id"] == todo_task["id"]

            assert c.get(f"/api/tasks/{backlog_task['id']}").json()["assignee"] is None
            assert c.get(f"/api/tasks/{human_required['id']}").json()["assignee"] is None

    def test_dispatch_without_task_id_skips_human_required(self, tmp_path, monkeypatch):
        def fake_popen(args, **kwargs):
            return SimpleNamespace(pid=12345)

        monkeypatch.setattr("flow_app.dispatcher.subprocess.Popen", fake_popen)
        monkeypatch.setattr("flow_app.dispatcher.threading.Thread", _NoopThread)

        with _client(tmp_path) as c:
            agent = create_agent(c, capabilities="", dispatch_statuses="review")
            human_required = create_task(
                c,
                title="Human review task",
                status="review",
                priority=100,
                human_required=True,
                blocker_reason="Needs human input.",
            )
            review_task = create_task(c, title="Review task", status="review", priority=50)

            r = c.post(f"/api/agents/{agent['id']}/dispatch")
            assert r.status_code == 200, r.text
            assert r.json()["task_id"] == review_task["id"]

            assert c.get(f"/api/tasks/{human_required['id']}").json()["assignee"] is None


# ---------- AgentRun lifecycle ----------


class TestAgentRunLifecycle:
    def test_dispatch_one_mints_scoped_key_when_api_key_missing(self, tmp_path, monkeypatch):
        db = _db(tmp_path)
        captured = {}

        def fake_popen(args, **kwargs):
            captured["env"] = kwargs["env"]
            return SimpleNamespace(pid=12345)

        monkeypatch.setattr("flow_app.dispatcher.subprocess.Popen", fake_popen)
        monkeypatch.setattr("flow_app.dispatcher.threading.Thread", _NoopThread)
        try:
            agent = repo_create_agent(db, AgentCreate(name="worker", command=_success_command()))
            task = repo_create_task(db, TaskCreate(title="Mint key", status="todo"))

            run = dispatch_one(db, agent, task, base_url="http://flow.test")

            api_key = db.get(AgentApiKey, run.scoped_key_id)
            assert api_key is not None
            assert api_key.name == f"dispatch-{run.id}"
            assert api_key.role == ApiKeyRole.implementer.value
            assert api_key.revoked_at is None
            assert captured["env"]["FLOW_API_KEY"].startswith("flow_")
            assert captured["env"]["FLOW_API_KEY"] != ""
            assert captured["env"]["FLOW_BASE_URL"] == "http://flow.test"
        finally:
            db.close()

    def test_dispatch_one_uses_explicit_api_key_without_minting(self, tmp_path, monkeypatch):
        db = _db(tmp_path)
        captured = {}

        def fake_popen(args, **kwargs):
            captured["env"] = kwargs["env"]
            return SimpleNamespace(pid=12345)

        monkeypatch.setattr("flow_app.dispatcher.subprocess.Popen", fake_popen)
        monkeypatch.setattr("flow_app.dispatcher.threading.Thread", _NoopThread)
        try:
            agent = repo_create_agent(db, AgentCreate(name="worker", command=_success_command()))
            task = repo_create_task(db, TaskCreate(title="Explicit key", status="todo"))

            run = dispatch_one(db, agent, task, api_key="explicit-key", base_url="http://flow.test")

            assert run.scoped_key_id is None
            assert captured["env"]["FLOW_API_KEY"] == "explicit-key"
            assert db.scalars(select(AgentApiKey)).all() == []
        finally:
            db.close()

    def test_complete_run_revokes_scoped_key(self, tmp_path, monkeypatch):
        db = _db(tmp_path)

        def fake_popen(args, **kwargs):
            return SimpleNamespace(pid=12345)

        monkeypatch.setattr("flow_app.dispatcher.subprocess.Popen", fake_popen)
        monkeypatch.setattr("flow_app.dispatcher.threading.Thread", _NoopThread)
        try:
            agent = repo_create_agent(db, AgentCreate(name="worker", command=_success_command()))
            task = repo_create_task(db, TaskCreate(title="Complete revokes", status="todo"))
            run = dispatch_one(db, agent, task, base_url="http://flow.test")

            complete_run(db, run, exit_code=0)

            api_key = db.get(AgentApiKey, run.scoped_key_id)
            assert api_key is not None
            assert api_key.revoked_at is not None
        finally:
            db.close()

    def test_stale_recovery_revokes_scoped_key(self, tmp_path, monkeypatch):
        db = _db(tmp_path)

        def fake_popen(args, **kwargs):
            return SimpleNamespace(pid=12345)

        monkeypatch.setattr("flow_app.dispatcher.subprocess.Popen", fake_popen)
        monkeypatch.setattr("flow_app.dispatcher.threading.Thread", _NoopThread)
        try:
            agent = repo_create_agent(
                db,
                AgentCreate(name="worker", command=_success_command(), stale_claim_timeout_seconds=1),
            )
            task = repo_create_task(db, TaskCreate(title="Stale revokes", status="todo"))
            run = dispatch_one(db, agent, task, base_url="http://flow.test")
            run.last_heartbeat_at = utcnow() - timedelta(seconds=10)
            db.flush()

            recovered = stale_recovery(db)

            api_key = db.get(AgentApiKey, run.scoped_key_id)
            assert recovered == [run.id]
            assert api_key is not None
            assert api_key.revoked_at is not None
        finally:
            db.close()

    def test_stale_recovery_skips_live_process(self, tmp_path, monkeypatch):
        """Stale recovery must not recover a run whose process is still alive."""
        import os as _os
        db = _db(tmp_path)

        def fake_popen(args, **kwargs):
            return SimpleNamespace(pid=_os.getpid())  # our own PID is alive

        monkeypatch.setattr("flow_app.dispatcher.subprocess.Popen", fake_popen)
        monkeypatch.setattr("flow_app.dispatcher.threading.Thread", _NoopThread)
        try:
            agent = repo_create_agent(
                db,
                AgentCreate(name="worker-live", command=_success_command(), stale_claim_timeout_seconds=1),
            )
            task = repo_create_task(db, TaskCreate(title="Live process", status="todo"))
            run = dispatch_one(db, agent, task, base_url="http://flow.test")
            run.last_heartbeat_at = utcnow() - timedelta(seconds=10)
            db.flush()

            recovered = stale_recovery(db)

            # Run should NOT be recovered — process is alive
            assert recovered == []
            # Run should still be running
            refreshed = db.get(AgentRun, run.id)
            assert refreshed.status == "running"
        finally:
            db.close()

    def test_manual_dispatch_mints_key_instead_of_forwarding_bearer_token(self, tmp_path, monkeypatch):
        captured = {}

        def fake_popen(args, **kwargs):
            captured["env"] = kwargs["env"]
            return SimpleNamespace(pid=12345)

        monkeypatch.setattr("flow_app.dispatcher.subprocess.Popen", fake_popen)
        monkeypatch.setattr("flow_app.dispatcher.threading.Thread", _NoopThread)

        with _client(tmp_path) as c:
            agent = create_agent(c)
            task = create_task(c)
            caller_key = c.post("/api/api-keys", json={"name": "caller-admin", "role": "admin"}).json()["api_key"]

            r = c.post(
                f"/api/agents/{agent['id']}/dispatch",
                params={"task_id": task["id"]},
                headers={"Authorization": f"Bearer {caller_key}"},
            )

            assert r.status_code == 200, r.text
            assert captured["env"]["FLOW_API_KEY"] != caller_key
            assert captured["env"]["FLOW_API_KEY"].startswith("flow_")

    def test_dispatch_one_atomic_claim_prevents_double_claim(self, tmp_path, monkeypatch):
        """Two concurrent dispatch_one calls on the same task — only one wins."""
        db = _db(tmp_path)

        def fake_popen(args, **kwargs):
            return SimpleNamespace(pid=12345)

        monkeypatch.setattr("flow_app.dispatcher.subprocess.Popen", fake_popen)
        monkeypatch.setattr("flow_app.dispatcher.threading.Thread", _NoopThread)
        try:
            agent1 = repo_create_agent(db, AgentCreate(name="agent-a", command=_success_command()))
            agent2 = repo_create_agent(db, AgentCreate(name="agent-b", command=_success_command()))
            task = repo_create_task(db, TaskCreate(title="Race task", status="todo"))

            # First dispatch succeeds
            run1 = dispatch_one(db, agent1, task, base_url="http://flow.test")
            db.commit()
            assert run1.agent_id == agent1.id

            # Re-fetch task — it now has an assignee and bumped version
            task = db.get(Task, task.id)
            assert task.assignee == "agent-a"

            # Second dispatch on the same task must fail (already claimed)
            with pytest.raises(DispatchError, match="already claimed"):
                dispatch_one(db, agent2, task, base_url="http://flow.test")
        finally:
            db.close()

    def test_dispatch_creates_run(self, tmp_path):
        with _client(tmp_path) as c:
            agent = create_agent(c)
            task = create_task(c)
            r = c.post(f"/api/agents/{agent['id']}/dispatch", params={"task_id": task["id"]})
            assert r.status_code == 200, r.text
            run = r.json()
            assert run["agent_id"] == agent["id"]
            assert run["task_id"] == task["id"]
            assert run["status"] == "running"
            assert run["pid"] is not None

    def test_remote_dispatch_creates_run_without_process(self, tmp_path, monkeypatch):
        def fail_popen(*args, **kwargs):
            raise AssertionError("remote dispatch must not spawn a subprocess")

        monkeypatch.setattr("flow_app.dispatcher.subprocess.Popen", fail_popen)
        with _client(tmp_path) as c:
            agent = create_agent(c, agent_type="remote")
            task = create_task(c)

            r = c.post(f"/api/agents/{agent['id']}/dispatch", params={"task_id": task["id"]})

            assert r.status_code == 200, r.text
            run = r.json()
            assert run["agent_id"] == agent["id"]
            assert run["task_id"] == task["id"]
            assert run["status"] == "running"
            assert run["pid"] is None

            fetched = c.get(f"/api/tasks/{task['id']}").json()
            assert fetched["status"] == "doing"
            assert fetched["assignee"] == agent["name"]

    def test_dispatch_disabled_agent(self, tmp_path):
        with _client(tmp_path) as c:
            agent = create_agent(c)
            c.patch(f"/api/agents/{agent['id']}", json={"enabled": False})
            task = create_task(c)
            r = c.post(f"/api/agents/{agent['id']}/dispatch", params={"task_id": task["id"]})
            assert r.status_code == 409

    def test_heartbeat(self, tmp_path):
        with _client(tmp_path) as c:
            agent = create_agent(c)
            task = create_task(c)
            r = c.post(f"/api/agents/{agent['id']}/dispatch", params={"task_id": task["id"]})
            run = r.json()
            hb = c.post(f"/api/agent-runs/{run['id']}/heartbeat")
            assert hb.status_code == 200
            assert hb.json()["last_heartbeat_at"] is not None

    def test_complete_run(self, tmp_path):
        with _client(tmp_path) as c:
            agent = create_agent(c)
            task = create_task(c)
            r = c.post(f"/api/agents/{agent['id']}/dispatch", params={"task_id": task["id"]})
            run = r.json()
            comp = c.post(f"/api/agent-runs/{run['id']}/complete", params={"exit_code": 0})
            assert comp.status_code == 200
            assert comp.json()["status"] == "done"
            assert comp.json()["exit_code"] == 0

    def test_complete_run_crashed(self, tmp_path):
        with _client(tmp_path) as c:
            agent = create_agent(c)
            task = create_task(c)
            r = c.post(f"/api/agents/{agent['id']}/dispatch", params={"task_id": task["id"]})
            run = r.json()
            comp = c.post(f"/api/agent-runs/{run['id']}/complete", params={"exit_code": 1})
            assert comp.status_code == 200
            assert comp.json()["status"] == "crashed"

    def test_list_agent_runs(self, tmp_path):
        with _client(tmp_path) as c:
            agent = create_agent(c)
            task = create_task(c)
            c.post(f"/api/agents/{agent['id']}/dispatch", params={"task_id": task["id"]})
            r = c.get("/api/agent-runs")
            assert r.status_code == 200
            assert len(r.json()["items"]) >= 1

    def test_list_agent_runs_filters(self, tmp_path):
        with _client(tmp_path) as c:
            agent = create_agent(c)
            task = create_task(c)
            c.post(f"/api/agents/{agent['id']}/dispatch", params={"task_id": task["id"]})
            r = c.get("/api/agent-runs", params={"agent_id": agent["id"]})
            assert r.status_code == 200
            assert len(r.json()["items"]) >= 1
            r2 = c.get("/api/agent-runs", params={"status": "running"})
            assert r2.status_code == 200
            assert len(r2.json()["items"]) >= 1

    def test_stale_recovery(self, tmp_path):
        with _client(tmp_path) as c:
            r = c.post("/api/agent-runs/stale-recovery")
            assert r.status_code == 200
            assert "recovered_run_ids" in r.json()

    def test_dispatch_concurrency_limit(self, tmp_path):
        with _client(tmp_path) as c:
            agent = create_agent(c, max_concurrency=1)
            task1 = create_task(c, title="Task 1")
            task2 = create_task(c, title="Task 2")
            r1 = c.post(f"/api/agents/{agent['id']}/dispatch", params={"task_id": task1["id"]})
            assert r1.status_code == 200
            r2 = c.post(f"/api/agents/{agent['id']}/dispatch", params={"task_id": task2["id"]})
            assert r2.status_code == 409

    def test_dispatch_passes_workspace_dir_when_config_exists(self, tmp_path, monkeypatch):
        db = _db(tmp_path)
        captured = {}

        def fake_popen(args, **kwargs):
            captured["env"] = kwargs["env"]
            return SimpleNamespace(pid=12345)

        monkeypatch.setattr("flow_app.dispatcher.subprocess.Popen", fake_popen)
        monkeypatch.setattr("flow_app.dispatcher.threading.Thread", _NoopThread)
        try:
            agent = repo_create_agent(db, AgentCreate(name="worker", command=_success_command()))
            task = repo_create_task(db, TaskCreate(title="Needs workspace", project="default"))
            repo_create_workspace_config(
                db,
                WorkspaceConfigCreate(
                    name="default",
                    strategy="scratch_dir",
                    scratch_root=str(tmp_path / "scratch"),
                ),
            )

            run = dispatch_one(db, agent, task, api_key="key", base_url="http://flow.test")

            assert captured["env"]["FLOW_WORKSPACE_DIR"] == str(tmp_path / "scratch" / task.id)
            assert run.workspace_state
            assert (tmp_path / "scratch" / task.id).is_dir()
        finally:
            db.close()

    def test_dispatch_omits_workspace_dir_without_config(self, tmp_path, monkeypatch):
        db = _db(tmp_path)
        captured = {}

        def fake_popen(args, **kwargs):
            captured["env"] = kwargs["env"]
            return SimpleNamespace(pid=12345)

        monkeypatch.setattr("flow_app.dispatcher.subprocess.Popen", fake_popen)
        monkeypatch.setattr("flow_app.dispatcher.threading.Thread", _NoopThread)
        try:
            agent = repo_create_agent(db, AgentCreate(name="worker", command=_success_command()))
            task = repo_create_task(db, TaskCreate(title="No workspace", project="default"))

            run = dispatch_one(db, agent, task, api_key="key", base_url="http://flow.test")

            assert "FLOW_WORKSPACE_DIR" not in captured["env"]
            assert run.workspace_state == ""
        finally:
            db.close()

    def test_dispatch_uses_workspace_path_as_cwd(self, tmp_path, monkeypatch):
        db = _db(tmp_path)
        captured = {}
        agent_cwd = tmp_path / "agent-cwd"

        def fake_popen(args, **kwargs):
            captured["cwd"] = kwargs["cwd"]
            captured["env"] = kwargs["env"]
            return SimpleNamespace(pid=12345)

        monkeypatch.setattr("flow_app.dispatcher.subprocess.Popen", fake_popen)
        monkeypatch.setattr("flow_app.dispatcher.threading.Thread", _NoopThread)
        try:
            agent = repo_create_agent(
                db,
                AgentCreate(
                    name="worker",
                    command=_success_command(),
                    working_directory=str(agent_cwd),
                ),
            )
            task = repo_create_task(db, TaskCreate(title="Needs workspace cwd", project="default"))
            repo_create_workspace_config(
                db,
                WorkspaceConfigCreate(
                    name="default",
                    strategy="scratch_dir",
                    scratch_root=str(tmp_path / "scratch"),
                ),
            )

            dispatch_one(db, agent, task, api_key="key", base_url="http://flow.test")

            workspace_path = str(tmp_path / "scratch" / task.id)
            assert captured["cwd"] == workspace_path
            assert captured["cwd"] != str(agent_cwd)
            assert captured["env"]["FLOW_WORKSPACE_DIR"] == workspace_path
        finally:
            db.close()

    def test_dispatch_fails_closed_when_workspace_not_ready(self, tmp_path, monkeypatch):
        db = _db(tmp_path)
        popen_called = False

        def fake_popen(args, **kwargs):
            nonlocal popen_called
            popen_called = True
            return SimpleNamespace(pid=12345)

        monkeypatch.setattr("flow_app.dispatcher.subprocess.Popen", fake_popen)
        monkeypatch.setattr("flow_app.dispatcher.threading.Thread", _NoopThread)
        try:
            agent = repo_create_agent(db, AgentCreate(name="worker", command=_success_command()))
            task = repo_create_task(db, TaskCreate(title="Bad workspace", project="default"))
            repo_create_workspace_config(
                db,
                WorkspaceConfigCreate(
                    name="default",
                    strategy="git_worktree",
                    root_dir=str(tmp_path / "outside-cwd"),
                ),
            )

            with pytest.raises(DispatchError):
                dispatch_one(db, agent, task, api_key="key", base_url="http://flow.test")

            db.expire_all()
            run = db.query(AgentRun).one()
            refreshed_task = db.get(Task, task.id)
            workspace_state = json.loads(run.workspace_state)
            assert popen_called is False
            assert run.status == "crashed"
            assert workspace_state["ready"] is False
            assert workspace_state["error"]
            assert refreshed_task is not None
            assert any("Workspace provisioning failed for worker:" in note.body for note in refreshed_task.notes)
        finally:
            db.close()

    def test_dispatch_uses_agent_cwd_when_no_workspace_config(self, tmp_path, monkeypatch):
        db = _db(tmp_path)
        captured = {}
        agent_cwd = tmp_path / "agent-cwd"

        def fake_popen(args, **kwargs):
            captured["cwd"] = kwargs["cwd"]
            captured["env"] = kwargs["env"]
            return SimpleNamespace(pid=12345)

        monkeypatch.setattr("flow_app.dispatcher.subprocess.Popen", fake_popen)
        monkeypatch.setattr("flow_app.dispatcher.threading.Thread", _NoopThread)
        try:
            agent = repo_create_agent(
                db,
                AgentCreate(
                    name="worker",
                    command=_success_command(),
                    working_directory=str(agent_cwd),
                ),
            )
            task = repo_create_task(db, TaskCreate(title="No workspace cwd", project="default"))

            dispatch_one(db, agent, task, api_key="key", base_url="http://flow.test")

            assert captured["cwd"] == str(agent_cwd)
            assert "FLOW_WORKSPACE_DIR" not in captured["env"]
        finally:
            db.close()


# ---------- Permission checks ----------


class TestAgentPermissions:
    def test_read_only_can_list_agents(self, client, no_auth_client):
        key_data = client.post("/api/api-keys", json={"name": "ro-agent", "role": "read_only"}).json()
        headers = bearer_headers(key_data["api_key"])
        r = no_auth_client.get("/api/agents", headers=headers)
        assert r.status_code == 200

    def test_read_only_cannot_create_agent(self, client, no_auth_client):
        key_data = client.post("/api/api-keys", json={"name": "ro-agent2", "role": "read_only"}).json()
        headers = bearer_headers(key_data["api_key"])
        r = no_auth_client.post("/api/agents", json={"name": "forbidden"}, headers=headers)
        assert r.status_code == 403

    def test_implementer_can_dispatch(self, client, no_auth_client):
        key_data = client.post("/api/api-keys", json={"name": "impl-agent", "role": "implementer"}).json()
        headers = bearer_headers(key_data["api_key"])
        agent = create_agent(client)
        task = create_task(client)
        r = no_auth_client.post(
            f"/api/agents/{agent['id']}/dispatch",
            params={"task_id": task["id"]},
            headers=headers,
        )
        # 200 (success) or 409 (business logic) are fine; 403 is not
        assert r.status_code in (200, 409), f"Expected 200 or 409, got {r.status_code}: {r.text}"

    def test_read_only_cannot_dispatch(self, client, no_auth_client):
        key_data = client.post("/api/api-keys", json={"name": "ro-agent3", "role": "read_only"}).json()
        headers = bearer_headers(key_data["api_key"])
        agent = create_agent(client)
        task = create_task(client)
        r = no_auth_client.post(
            f"/api/agents/{agent['id']}/dispatch",
            params={"task_id": task["id"]},
            headers=headers,
        )
        assert r.status_code == 403
