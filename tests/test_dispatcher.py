"""Tests for Agent Registry, Dispatcher, AgentRun lifecycle, and permissions."""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from flow_app.database import Base, build_engine, build_session_factory
from flow_app.dispatcher import dispatch_loop, dispatch_one
from flow_app.main import create_app
from flow_app.repository import create_agent as repo_create_agent
from flow_app.repository import create_task as repo_create_task
from flow_app.repository import create_workspace_config as repo_create_workspace_config
from flow_app.schemas import AgentCreate, TaskCreate, WorkspaceConfigCreate


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


# ---------- Agent CRUD ----------


class TestAgentCRUD:
    def test_create_agent(self, tmp_path, default_command):
        with _client(tmp_path) as c:
            agent = create_agent(c, command=default_command)
            assert agent["id"].startswith("agent_")
            assert agent["name"] == "codex-agent"
            assert agent["enabled"] is True
            assert agent["agent_type"] == "cli"
            assert agent["capabilities"] == "code,testing"

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
            r = c.patch(f"/api/agents/{agent['id']}", json={"description": "updated", "enabled": False})
            assert r.status_code == 200
            assert r.json()["description"] == "updated"
            assert r.json()["enabled"] is False

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


# ---------- AgentRun lifecycle ----------


class TestAgentRunLifecycle:
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
            assert len(r.json()) >= 1

    def test_list_agent_runs_filters(self, tmp_path):
        with _client(tmp_path) as c:
            agent = create_agent(c)
            task = create_task(c)
            c.post(f"/api/agents/{agent['id']}/dispatch", params={"task_id": task["id"]})
            r = c.get("/api/agent-runs", params={"agent_id": agent["id"]})
            assert r.status_code == 200
            assert len(r.json()) >= 1
            r2 = c.get("/api/agent-runs", params={"status": "running"})
            assert r2.status_code == 200
            assert len(r2.json()) >= 1

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
