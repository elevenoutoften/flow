# Workspace Isolation

Flow workspace configs describe how an agent should receive an isolated working directory for a task. They are intended for parallel agent runs where each task needs its own filesystem location.

## Strategies

- `git_worktree`: creates a Git worktree under `.worktrees/{task_id}` from `base_branch` on a branch named `{branch_prefix}{task_id}`.
- `shared_dir`: creates a task directory under `root_dir`, or `/tmp/flow-shared` when `root_dir` is blank.
- `scratch_dir`: creates a task directory under `scratch_root`, defaulting to `/tmp/flow-scratch`.

## REST API

- `GET /api/workspace-configs?enabled_only=false`
- `GET /api/workspace-configs/{config_id}`
- `POST /api/workspace-configs`
- `PATCH /api/workspace-configs/{config_id}`
- `POST /api/workspace-configs/{config_id}/provision?task_id=flow_000001&repo_path=/path/to/repo`
- `POST /api/workspace-configs/{config_id}/cleanup`

Create payload:

```json
{
  "name": "default worktrees",
  "strategy": "git_worktree",
  "base_branch": "main",
  "branch_prefix": "task-",
  "root_dir": "",
  "scratch_root": "/tmp/flow-scratch",
  "description": "Worktree isolation for parallel agents",
  "enabled": true
}
```

Cleanup payload:

```json
{
  "strategy": "git_worktree",
  "path": "/repo/.worktrees/flow_000001"
}
```

## MCP Tools

- `flow_list_workspace_configs`
- `flow_get_workspace_config`
- `flow_create_workspace_config`
- `flow_update_workspace_config`
- `flow_provision_workspace`
- `flow_cleanup_workspace`

## Permissions

All roles can read workspace configs through `workspace:read`. Only admins and architects can manage configs or provision and clean up workspaces through `workspace:manage`.

## Provisioning Workflow

1. Create or select an enabled workspace config.
2. Provision with a task ID before dispatching an agent.
3. Pass the returned `path` and optional `branch` to the agent runtime.
4. Clean up the workspace when the task is complete and its work has been preserved.
