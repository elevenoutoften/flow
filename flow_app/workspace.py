"""Workspace manager: provision and clean up isolated workspaces for agent tasks."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess

from .models import WorkspaceConfig

logger = logging.getLogger(__name__)

SAFE_ID_PATTERN = re.compile(r"^[a-z]+_[0-9]+$")


@dataclass
class WorkspaceResult:
    workspace_id: str
    strategy: str
    path: str
    branch: str | None
    ready: bool
    error: str | None = None


def validate_task_id(task_id: str) -> str:
    """Validate that a task ID is safe for filesystem path construction."""
    if not task_id:
        raise ValueError("Task ID is required.")
    if not SAFE_ID_PATTERN.match(task_id):
        raise ValueError(f"Invalid task ID format: {task_id!r}. Must match pattern prefix_NNNNNN.")
    if ".." in task_id or "/" in task_id or "\\" in task_id:
        raise ValueError(f"Task ID contains unsafe characters: {task_id!r}")
    return task_id


def validate_containment(candidate_path: str, root_path: str) -> str:
    """Validate that a resolved path is contained within the root path."""
    candidate = os.path.realpath(candidate_path)
    root = os.path.realpath(root_path)
    try:
        common = os.path.commonpath([candidate, root])
    except ValueError as exc:
        raise ValueError(f"Path {candidate!r} escapes workspace root {root!r}") from exc
    if common != root:
        raise ValueError(f"Path {candidate!r} escapes workspace root {root!r}")
    return candidate


def provision_workspace(config: WorkspaceConfig, task_id: str, repo_path: str | None = None) -> WorkspaceResult:
    """Provision a workspace for a task based on the config strategy."""
    if config.strategy == "git_worktree":
        return _provision_git_worktree(config, task_id, repo_path or os.getcwd())
    if config.strategy == "shared_dir":
        return _provision_shared_dir(config, task_id)
    if config.strategy == "scratch_dir":
        return _provision_scratch_dir(config, task_id)
    return WorkspaceResult(
        workspace_id=f"ws-{task_id}",
        strategy=config.strategy,
        path="",
        branch=None,
        ready=False,
        error=f"Unknown strategy: {config.strategy}",
    )


def cleanup_workspace(workspace_id: str, strategy: str, path: str, config: WorkspaceConfig | None = None) -> bool:
    """Clean up a workspace after task completion."""
    del workspace_id
    try:
        if config is None:
            logger.warning("Cleaning workspace path without configured root validation.")
        else:
            validate_containment(path, _cleanup_root_for_strategy(strategy, path, config))
        if strategy == "git_worktree":
            return _cleanup_git_worktree(path)
        if strategy in ("shared_dir", "scratch_dir"):
            if os.path.exists(path):
                shutil.rmtree(path, ignore_errors=True)
            return True
        return False
    except Exception:
        return False


def _provision_git_worktree(config: WorkspaceConfig, task_id: str, repo_path: str) -> WorkspaceResult:
    """Create a git worktree for the task."""
    ws_id = f"ws-{task_id}"
    try:
        validate_task_id(task_id)
        branch_name = f"{config.branch_prefix}{task_id}"
        repo_path = _normalize_path(repo_path)
        worktree_path = _normalize_path(os.path.join(repo_path, ".worktrees", task_id))
        validate_containment(worktree_path, repo_path)
        os.makedirs(os.path.dirname(worktree_path), exist_ok=True)
        result = subprocess.run(
            ["git", "worktree", "add", "-b", branch_name, worktree_path, config.base_branch],
            capture_output=True,
            text=True,
            cwd=repo_path,
            timeout=30,
        )
        if result.returncode != 0:
            return WorkspaceResult(
                workspace_id=ws_id,
                strategy="git_worktree",
                path=worktree_path,
                branch=None,
                ready=False,
                error=(result.stderr or result.stdout or "git worktree add failed").strip(),
            )
        return WorkspaceResult(
            workspace_id=ws_id,
            strategy="git_worktree",
            path=worktree_path,
            branch=branch_name,
            ready=True,
        )
    except Exception as exc:
        return WorkspaceResult(
            workspace_id=ws_id,
            strategy="git_worktree",
            path="",
            branch=None,
            ready=False,
            error=str(exc),
        )


def _provision_shared_dir(config: WorkspaceConfig, task_id: str) -> WorkspaceResult:
    """Create a shared directory workspace."""
    ws_id = f"ws-{task_id}"
    try:
        validate_task_id(task_id)
        base_dir = config.root_dir or "/tmp/flow-shared"
        dir_path = _normalize_path(os.path.join(base_dir, task_id))
        validate_containment(dir_path, base_dir)
        os.makedirs(dir_path, exist_ok=True)
        return WorkspaceResult(workspace_id=ws_id, strategy="shared_dir", path=dir_path, branch=None, ready=True)
    except Exception as exc:
        return WorkspaceResult(
            workspace_id=ws_id,
            strategy="shared_dir",
            path="",
            branch=None,
            ready=False,
            error=str(exc),
        )


def _provision_scratch_dir(config: WorkspaceConfig, task_id: str) -> WorkspaceResult:
    """Create a scratch directory workspace."""
    ws_id = f"ws-{task_id}"
    try:
        validate_task_id(task_id)
        dir_path = _normalize_path(os.path.join(config.scratch_root, task_id))
        validate_containment(dir_path, config.scratch_root)
        os.makedirs(dir_path, exist_ok=True)
        return WorkspaceResult(workspace_id=ws_id, strategy="scratch_dir", path=dir_path, branch=None, ready=True)
    except Exception as exc:
        return WorkspaceResult(
            workspace_id=ws_id,
            strategy="scratch_dir",
            path="",
            branch=None,
            ready=False,
            error=str(exc),
        )


def _cleanup_git_worktree(worktree_path: str) -> bool:
    """Remove a git worktree."""
    try:
        parent_repo = str(Path(worktree_path).parent.parent)
        subprocess.run(
            ["git", "worktree", "remove", worktree_path, "--force"],
            capture_output=True,
            cwd=parent_repo,
            timeout=15,
        )
        return True
    except Exception:
        if os.path.exists(worktree_path):
            shutil.rmtree(worktree_path, ignore_errors=True)
        return True


def _normalize_path(path: str) -> str:
    return os.path.normpath(str(path))


def _cleanup_root_for_strategy(strategy: str, path: str, config: WorkspaceConfig) -> str:
    if strategy == "shared_dir":
        return config.root_dir or "/tmp/flow-shared"
    if strategy == "scratch_dir":
        return config.scratch_root
    if strategy == "git_worktree":
        if config.root_dir:
            return config.root_dir
        worktree_parent = Path(path).parent
        if worktree_parent.name != ".worktrees":
            raise ValueError("Git worktree path must be under a .worktrees directory.")
        return str(worktree_parent.parent)
    raise ValueError(f"Unknown strategy: {strategy}")
