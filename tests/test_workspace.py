from __future__ import annotations

import ntpath
import os
from types import SimpleNamespace

import pytest

from flow_app.database import build_engine, build_session_factory
from flow_app.main import ensure_compatible_schema
from flow_app.repository import create_agent, create_agent_run, create_task, get_task_workspace, save_run_workspace_state
from flow_app.schemas import AgentCreate, TaskCreate, WorkspaceConfigCreate
from flow_app.workspace import (
    WorkspaceResult,
    cleanup_workspace,
    provision_workspace,
    validate_branch_component,
    validate_containment,
    validate_task_id,
)


def bearer_headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


def create_role_headers(client, role: str, name: str | None = None) -> dict[str, str]:
    created = client.post("/api/api-keys", json={"name": name or f"{role}-workspace", "role": role})
    assert created.status_code == 201, created.text
    return bearer_headers(created.json()["api_key"])


def create_workspace_config(client, **overrides):
    payload = {
        "name": "Default worktrees",
        "strategy": "git_worktree",
        "base_branch": "main",
        "branch_prefix": "task-",
        "root_dir": "",
        "scratch_root": "/tmp/flow-scratch",
        "description": "",
        "enabled": True,
    }
    payload.update(overrides)
    response = client.post("/api/workspace-configs", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def rpc(client, name: str, arguments: dict, headers: dict | None = None):
    return client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}},
        headers=headers or {},
    )


def test_validate_task_id_accepts_safe_ids():
    for task_id in ("flow_000001", "idea_000003", "ws_000005"):
        assert validate_task_id(task_id) == task_id


def test_validate_task_id_rejects_unsafe_ids():
    for task_id in ("../etc", "foo/bar", r".\..\..", "", "/tmp/flow_000001", "flow_000001/extra"):
        with pytest.raises(ValueError):
            validate_task_id(task_id)


def test_validate_containment_rejects_sibling_directory(tmp_path):
    root = tmp_path / "root"
    sibling = tmp_path / "root2" / "flow_000001"
    root.mkdir()
    sibling.parent.mkdir()

    with pytest.raises(ValueError):
        validate_containment(str(sibling), str(root))


@pytest.mark.skipif(os.name == "nt" or not hasattr(os, "symlink"), reason="symlinks not available")
def test_validate_containment_rejects_symlink_escaping_root(tmp_path):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    link = root / "flow_000001"
    link.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError):
        validate_containment(str(link), str(root))


def test_provision_workspace_with_traversal_task_id_returns_error(tmp_path):
    config = WorkspaceConfigCreate(name="scratch", strategy="scratch_dir", scratch_root=str(tmp_path / "scratch"))

    result = provision_workspace(config, "../../etc")

    assert result.ready is False
    assert "Invalid task ID format" in str(result.error)
    assert not (tmp_path / "scratch").exists()


def test_cleanup_workspace_rejects_escaping_path(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    keep = outside / "keep.txt"
    keep.write_text("keep", encoding="utf-8")
    config = SimpleNamespace(strategy="shared_dir", root_dir=str(root), scratch_root=str(tmp_path / "scratch"))

    assert cleanup_workspace("ws-flow_000001", "shared_dir", str(outside), config) is False
    assert keep.exists()


def test_cleanup_workspace_requires_config(tmp_path):
    workspace_dir = tmp_path / "flow_000001"
    workspace_dir.mkdir()

    with pytest.raises(TypeError):
        cleanup_workspace("ws-flow_000001", "shared_dir", str(workspace_dir))

    assert workspace_dir.exists()


def test_cleanup_workspace_valid_path_succeeds(tmp_path):
    root = tmp_path / "root"
    workspace_dir = root / "flow_000001"
    workspace_dir.mkdir(parents=True)
    (workspace_dir / "artifact.txt").write_text("remove", encoding="utf-8")
    config = SimpleNamespace(strategy="shared_dir", root_dir=str(root), scratch_root=str(tmp_path / "scratch"))

    assert cleanup_workspace("ws-flow_000001", "shared_dir", str(workspace_dir), config) is True
    assert not workspace_dir.exists()


def test_workspace_config_crud_create_list_get_update_enable_disable(client):
    config = create_workspace_config(client, name="  Worktree config  ", description="  agents  ")

    assert config["id"] == "ws_000001"
    assert config["name"] == "Worktree config"
    assert config["strategy"] == "git_worktree"
    assert config["enabled"] is True
    assert config["description"] == "agents"

    listed = client.get("/api/workspace-configs").json()
    assert [item["id"] for item in listed] == [config["id"]]

    fetched = client.get(f"/api/workspace-configs/{config['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "Worktree config"

    updated = client.patch(
        f"/api/workspace-configs/{config['id']}",
        json={"enabled": False, "strategy": "scratch_dir", "scratch_root": "/tmp/flow-other"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["enabled"] is False
    assert updated.json()["strategy"] == "scratch_dir"
    assert client.get("/api/workspace-configs?enabled_only=true").json() == []


def test_workspace_invalid_strategy_rejected(client):
    created = client.post("/api/workspace-configs", json={"name": "Bad", "strategy": "invalid"})
    assert created.status_code == 422

    config = create_workspace_config(client)
    updated = client.patch(f"/api/workspace-configs/{config['id']}", json={"strategy": "invalid"})
    assert updated.status_code == 422


def test_validate_branch_component_rejects_unsafe_values():
    for value in ("../main", "feature/test", r"feature\test", "-main"):
        with pytest.raises(ValueError):
            validate_branch_component(value, "Branch")


def test_git_worktree_provision_rejects_repo_outside_root(tmp_path):
    root = tmp_path / "root"
    repo = tmp_path / "repo"
    root.mkdir()
    repo.mkdir()
    config = SimpleNamespace(strategy="git_worktree", branch_prefix="task-", base_branch="main", root_dir=str(root))

    result = provision_workspace(config, "flow_000123", str(repo))

    assert result.ready is False
    assert "escapes workspace root" in str(result.error)
    assert not (repo / ".worktrees").exists()


def test_git_worktree_provision_rejects_unsafe_branch_config(tmp_path):
    config = SimpleNamespace(strategy="git_worktree", branch_prefix="agent/", base_branch="main", root_dir="")

    result = provision_workspace(config, "flow_000123", str(tmp_path))

    assert result.ready is False
    assert "Branch prefix contains unsafe characters" in str(result.error)
    assert not (tmp_path / ".worktrees").exists()


def test_provision_git_worktree_requires_root_dir(tmp_path):
    config = SimpleNamespace(strategy="git_worktree", branch_prefix="task-", base_branch="main", root_dir="")

    result = provision_workspace(config, "flow_000123", str(tmp_path))

    assert result.ready is False
    assert "requires a configured root_dir" in str(result.error)
    assert not (tmp_path / ".worktrees").exists()


def test_provision_git_worktree_blank_root_dir_rejects(tmp_path):
    config = SimpleNamespace(strategy="git_worktree", branch_prefix="task-", base_branch="main", root_dir=" ")

    result = provision_workspace(config, "flow_000123", str(tmp_path))

    assert result.ready is False
    assert "requires a configured root_dir" in str(result.error)
    assert not (tmp_path / ".worktrees").exists()


def test_cleanup_git_worktree_requires_root_dir(tmp_path):
    config = SimpleNamespace(strategy="git_worktree", root_dir="", scratch_root=str(tmp_path / "scratch"))

    assert cleanup_workspace("ws-flow_000123", "git_worktree", str(tmp_path / "repo" / ".worktrees" / "flow_000123"), config) is False


def test_provision_git_worktree_arbitrary_path_rejected():
    config = SimpleNamespace(
        strategy="git_worktree",
        branch_prefix="task-",
        base_branch="main",
        root_dir="/opt/flow/workspaces",
    )

    result = provision_workspace(config, "flow_000123", "/etc/passwd")

    assert result.ready is False
    assert "escapes workspace root" in str(result.error)


def test_cleanup_git_worktree_arbitrary_path_rejected(tmp_path):
    config = SimpleNamespace(
        strategy="git_worktree",
        root_dir="/opt/flow/workspaces",
        scratch_root=str(tmp_path / "scratch"),
    )

    assert cleanup_workspace("ws-flow_000123", "git_worktree", "/etc/passwd", config) is False


def test_git_worktree_provision_and_cleanup_use_subprocess(client, tmp_path, monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("flow_app.workspace.subprocess.run", fake_run)
    config = create_workspace_config(client, branch_prefix="agent-", base_branch="develop", root_dir=str(tmp_path))

    provisioned = client.post(
        f"/api/workspace-configs/{config['id']}/provision",
        params={"task_id": "flow_000123", "repo_path": str(tmp_path)},
    )

    assert provisioned.status_code == 200, provisioned.text
    data = provisioned.json()
    assert data["workspace_id"] == "ws-flow_000123"
    assert data["strategy"] == "git_worktree"
    assert data["branch"] == "agent-flow_000123"
    assert data["ready"] is True
    assert data["path"] == str(tmp_path / ".worktrees" / "flow_000123")
    assert calls[0][0] == [
        "git",
        "worktree",
        "add",
        "-b",
        "agent-flow_000123",
        str(tmp_path / ".worktrees" / "flow_000123"),
        "develop",
    ]

    cleaned = client.post(
        f"/api/workspace-configs/{config['id']}/cleanup",
        json={"strategy": "git_worktree", "path": data["path"]},
    )
    assert cleaned.status_code == 200, cleaned.text
    assert cleaned.json()["cleaned"] is True
    assert calls[1][0] == ["git", "worktree", "remove", data["path"], "--force"]


def test_shared_dir_provision_and_cleanup(client, tmp_path):
    config = create_workspace_config(client, name="shared", strategy="shared_dir", root_dir=str(tmp_path / "shared"))

    response = client.post(f"/api/workspace-configs/{config['id']}/provision", params={"task_id": "flow_000002"})

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["ready"] is True
    assert data["path"] == str(tmp_path / "shared" / "flow_000002")
    assert (tmp_path / "shared" / "flow_000002").is_dir()

    cleaned = client.post(
        f"/api/workspace-configs/{config['id']}/cleanup",
        json={"strategy": "shared_dir", "path": data["path"]},
    )
    assert cleaned.json()["cleaned"] is True
    assert not (tmp_path / "shared" / "flow_000002").exists()


def test_scratch_dir_provision_and_cleanup(client, tmp_path):
    config = create_workspace_config(
        client,
        name="scratch",
        strategy="scratch_dir",
        scratch_root=str(tmp_path / "scratch"),
    )

    response = client.post(f"/api/workspace-configs/{config['id']}/provision", params={"task_id": "flow_000003"})

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["ready"] is True
    assert data["path"] == str(tmp_path / "scratch" / "flow_000003")
    assert (tmp_path / "scratch" / "flow_000003").is_dir()

    cleaned = client.post(
        f"/api/workspace-configs/{config['id']}/cleanup",
        json={"strategy": "scratch_dir", "path": data["path"]},
    )
    assert cleaned.json()["cleaned"] is True
    assert not (tmp_path / "scratch" / "flow_000003").exists()


def test_workspace_state_persists_for_task(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'flow.sqlite'}")
    session_factory = build_session_factory(engine)
    from flow_app.database import Base

    Base.metadata.create_all(bind=engine)
    ensure_compatible_schema(engine)
    db = session_factory()
    try:
        agent = create_agent(db, AgentCreate(name="worker", command="echo ok"))
        task = create_task(db, TaskCreate(title="Needs workspace", project="default"))
        run = create_agent_run(db, agent_id=agent.id, task_id=task.id, status="running")
        result = WorkspaceResult(
            workspace_id="ws-flow_000001",
            strategy="scratch_dir",
            path=str(tmp_path / "scratch" / task.id),
            branch=None,
            ready=True,
        )

        save_run_workspace_state(db, run, result)

        state = get_task_workspace(db, task.id)
        assert state is not None
        assert state["workspace_id"] == result.workspace_id
        assert state["strategy"] == "scratch_dir"
        assert state["path"] == result.path
        assert state["ready"] is True
        assert state["provisioned_at"]
    finally:
        db.close()


def test_git_worktree_path_uses_native_separators(monkeypatch):
    import flow_app.workspace as workspace

    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(workspace.subprocess, "run", fake_run)
    monkeypatch.setattr(workspace.os, "path", ntpath)
    monkeypatch.setattr(workspace.os, "makedirs", lambda *args, **kwargs: None)
    config = SimpleNamespace(strategy="git_worktree", branch_prefix="task-", base_branch="main", root_dir=r"C:\repo")

    result = workspace.provision_workspace(config, "flow_000123", r"C:\repo")

    assert result.path == r"C:\repo\.worktrees\flow_000123"
    assert "/" not in result.path
    assert calls[0][0][5] == result.path


def test_workspace_permissions_read_only_can_list_implementer_cannot_create(client, no_auth_client):
    create_workspace_config(client)
    reader_headers = create_role_headers(client, "read_only", "reader-workspace")
    implementer_headers = create_role_headers(client, "implementer", "impl-workspace")

    listed = no_auth_client.get("/api/workspace-configs", headers=reader_headers)
    assert listed.status_code == 200
    assert listed.json()[0]["name"] == "Default worktrees"

    denied = no_auth_client.post(
        "/api/workspace-configs",
        json={"name": "Denied"},
        headers=implementer_headers,
    )
    assert denied.status_code == 403


def test_mcp_workspace_tools(client, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "flow_app.workspace.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )

    created = rpc(
        client,
        "flow_create_workspace_config",
        {
            "name": "MCP worktrees",
            "strategy": "git_worktree",
            "base_branch": "main",
            "branch_prefix": "mcp-",
            "root_dir": str(tmp_path),
        },
    ).json()["result"]["structuredContent"]["workspace_config"]

    listed = rpc(client, "flow_list_workspace_configs", {}).json()
    assert listed["result"]["structuredContent"]["count"] == 1

    fetched = rpc(client, "flow_get_workspace_config", {"config_id": created["id"]}).json()
    assert fetched["result"]["structuredContent"]["workspace_config"]["name"] == "MCP worktrees"

    updated = rpc(client, "flow_update_workspace_config", {"config_id": created["id"], "description": "ready"}).json()
    assert updated["result"]["structuredContent"]["workspace_config"]["description"] == "ready"

    provisioned = rpc(
        client,
        "flow_provision_workspace",
        {"config_id": created["id"], "task_id": "flow_000004", "repo_path": str(tmp_path)},
    ).json()["result"]["structuredContent"]["workspace"]
    assert provisioned["ready"] is True
    assert provisioned["branch"] == "mcp-flow_000004"

    cleaned = rpc(
        client,
        "flow_cleanup_workspace",
        {
            "config_id": created["id"],
            "workspace_id": provisioned["workspace_id"],
            "strategy": "git_worktree",
            "path": provisioned["path"],
        },
    ).json()["result"]["structuredContent"]
    assert cleaned["cleaned"] is True


def test_mcp_cleanup_workspace_requires_config_id(client, tmp_path):
    response = rpc(
        client,
        "flow_cleanup_workspace",
        {"workspace_id": "ws-flow_000004", "strategy": "shared_dir", "path": str(tmp_path)},
    ).json()

    assert response["error"]["code"] == -32602
    assert response["error"]["message"] == "config_id is required."


def test_mcp_workspace_permission_denied_for_read_only(client, no_auth_client):
    reader_headers = create_role_headers(client, "read_only", "reader-mcp-workspace")

    denied = rpc(no_auth_client, "flow_create_workspace_config", {"name": "Denied"}, reader_headers)

    assert denied.json()["error"]["code"] == -32603
