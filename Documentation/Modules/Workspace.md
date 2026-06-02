# Workspace

## Source

| File | Role |
|------|------|
| `flow_app/workspace.py` | Provisioning and cleanup logic |
| `flow_app/models.py:205-220` | `WorkspaceConfig` model |
| `flow_app/routes/workspace.py` | REST API router |
| `flow_app/dispatcher.py:270-315` | Workspace integration in dispatch |

## Overview

Workspace configs describe how an agent receives an isolated working directory for a task. They enable parallel agent runs where each task needs its own filesystem location.

## Strategies

| Strategy | Description |
|----------|-------------|
| `git_worktree` | Creates a Git worktree under `.worktrees/{task_id}` from `base_branch` on branch `{branch_prefix}{task_id}` |
| `shared_dir` | Creates a task directory under `root_dir` (or `/tmp/flow-shared`) |
| `scratch_dir` | Creates a task directory under `scratch_root` (default `/tmp/flow-scratch`) |

## Model

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `id` | string (`ws_NNNNNN`) | auto | Primary key |
| `name` | string(180) | *(required, unique)* | Config name |
| `strategy` | string(24) | `git_worktree` | Isolation strategy |
| `base_branch` | string(240) | `main` | Git base branch |
| `branch_prefix` | string(120) | `task-` | Git branch prefix |
| `root_dir` | string(500) | `""` | Root for shared_dir |
| `scratch_root` | string(500) | `/tmp/flow-scratch` | Root for scratch_dir |
| `description` | text | `""` | Description |
| `enabled` | bool | `true` | Active flag |

## Provisioning Workflow

1. Create or select an enabled workspace config.
2. Before dispatching an agent, provision with a task ID.
3. Pass the returned `path` and optional `branch` to the agent runtime.
4. Clean up the workspace when the task is complete.

### Dispatch Integration

When `dispatch_one()` runs:
1. `_workspace_config_for_task()` finds a matching config (by project name, then "default", then first enabled).
2. `provision_workspace()` creates the workspace.
3. `save_run_workspace_state()` records the workspace state on the agent run.
4. `FLOW_WORKSPACE_DIR` is set in the subprocess environment.
5. The subprocess CWD is set to the workspace path.
6. On completion or crash, `_cleanup_run_workspace()` cleans up.

## Safety

- Task IDs are validated against `^[a-z]+_[0-9]+$` to prevent path traversal.
- Branch components are validated to reject `..`, `/`, `\`, and leading `-`.
- Resolved paths are checked for containment within the root using `os.path.commonpath()`.

## REST API

| Method | Path | Permission |
|--------|------|-----------|
| `GET` | `/api/workspace-configs` | `workspace:read` |
| `GET` | `/api/workspace-configs/{id}` | `workspace:read` |
| `POST` | `/api/workspace-configs` | `workspace:manage` |
| `PATCH` | `/api/workspace-configs/{id}` | `workspace:manage` |
| `POST` | `/api/workspace-configs/{id}/provision?task_id=&repo_path=` | `workspace:manage` |
| `POST` | `/api/workspace-configs/{id}/cleanup` | `workspace:manage` |

## MCP Tools

| Tool | Permission |
|------|-----------|
| `flow_list_workspace_configs` | `workspace:read` |
| `flow_get_workspace_config` | `workspace:read` |
| `flow_create_workspace_config` | `workspace:manage` |
| `flow_update_workspace_config` | `workspace:manage` |
| `flow_provision_workspace` | `workspace:manage` |
| `flow_cleanup_workspace` | `workspace:manage` |

## See Also

- [Dispatcher](Dispatcher.md) — agent dispatch with workspace
- [Security](Security.md) — workspace permissions
- [REST API](REST-API.md) — endpoint reference
