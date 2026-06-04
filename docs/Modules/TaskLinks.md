# Task Links

## Source

| File | Role |
|------|------|
| `flow_app/models.py:195-202` | `TaskLink` model |
| `flow_app/repository.py:831-1079` | Link CRUD, cycle detection, auto-promotion |
| `flow_app/schemas.py` | `TaskLinkCreate`, `TaskLinkResponse`, `DependencySummary` |
| `flow_app/routes/tasks.py` | REST API routes |

## Link Types

| Type | Blocking | Cycles |
|------|----------|--------|
| `blocks` | Yes — parent blocks child | Rejected |
| `depends_on` | Yes — parent blocks child | Rejected |
| `related` | No | Allowed |

Blocking links always mean `parent_id` blocks `child_id`.

## Model

| Field | Type | Description |
|-------|------|-------------|
| `id` | string (`link_NNNNNN`) | Primary key |
| `parent_id` | string | FK to tasks (blocker) |
| `child_id` | string | FK to tasks (blocked) |
| `link_type` | string | `blocks`, `depends_on`, `related` |
| `created_at` | datetime | Creation timestamp |

## REST API

| Method | Path | Permission |
|--------|------|-----------|
| `POST` | `/api/tasks/{task_id}/link` | `links:manage` |
| `DELETE` | `/api/tasks/{task_id}/link/{link_id}` | `links:manage` |
| `GET` | `/api/tasks/{task_id}/links?link_type=blocks` | `links:read` |
| `GET` | `/api/tasks/{task_id}/dependencies` | `links:read` |

### Create Link

```json
{
  "parent_id": "flow_000001",
  "child_id": "flow_000002",
  "link_type": "blocks"
}
```

### Dependency Summary

Returns:
- `parents`: all links where the task is the child
- `children`: all links where the task is the parent
- `blocked_by`: blocking parent links only
- `blocking`: blocking child links only
- `parent_tasks`, `child_tasks`, `blocked_by_tasks`, `blocking_tasks`: resolved task summaries

## MCP Tools

| Tool | Permission | Description |
|------|-----------|-------------|
| `flow_link_task` | `links:manage` | Create task link |
| `flow_unlink_task` | `links:manage` | Delete task link |
| `flow_get_dependencies` | `links:read` | Dependency summary |
| `flow_list_task_links` | `links:read` | List links |

## Cycle Prevention

When adding a blocking link from parent to child, `has_cycle()` traverses existing blocking links from the child to check if the parent is reachable. If so, the link is rejected with "Blocking task link would create a cycle." `related` links are excluded from cycle checks.

## Auto-Promotion

When a task moves to `done`, `auto_promote_unblocked_children()`:

1. Finds all blocking child links from the completed task.
2. For each child in `backlog`:
   - Checks if every blocking parent is now `done`.
   - If so, moves the child to `todo` using CAS update.
   - Adds a system note: "All blocking dependencies are done; dependency {parent_id} unblocked this task and moved it to todo."

Example:
1. Link `flow_000001` blocks `flow_000002`.
2. `flow_000002` is in `backlog`.
3. Move `flow_000001` to `done`.
4. Flow automatically moves `flow_000002` to `todo`.

## Dispatch Readiness

`is_dispatch_ready()` checks that a task:
- Has no assignee.
- Is not `human_required`.
- Is in an allowed status (configurable).
- Has all blocking parents in `done` status.

The runner and `GET /api/tasks/next` use this to skip tasks that are not yet ready.

## See Also

- [Tasks](Tasks.md) — task model and lifecycle
- [Handoff](Handoff.md) — dependency context in dispatch
- [Dispatcher](Dispatcher.md) — dispatch readiness checks
- [Security](Security.md) — link permissions
