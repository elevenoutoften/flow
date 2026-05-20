# Task Links

Task links describe relationships between Flow tasks.

## Link Types

- `blocks`: the parent blocks the child. Cycles are rejected.
- `depends_on`: the parent blocks the child. Cycles are rejected.
- `related`: non-blocking relationship. Cycles are allowed.

Blocking links always mean `parent_id` blocks `child_id`.

## REST API

- `POST /api/tasks/{task_id}/link`
  - Permission: `links:manage`
  - Body: `{"parent_id": "flow_000001", "child_id": "flow_000002", "link_type": "blocks"}`
- `DELETE /api/tasks/{task_id}/link/{link_id}`
  - Permission: `links:manage`
- `GET /api/tasks/{task_id}/links?link_type=blocks`
  - Permission: `links:read`
- `GET /api/tasks/{task_id}/dependencies`
  - Permission: `links:read`

The dependency summary returns:

- `parents`: all links where the task is the child.
- `children`: all links where the task is the parent.
- `blocked_by`: blocking parent links only.
- `blocking`: blocking child links only.

## MCP Tools

- `flow_link_task`: create a task link.
- `flow_unlink_task`: delete a task link.
- `flow_get_dependencies`: fetch dependency summary for a task.
- `flow_list_task_links`: list links touching a task, optionally filtered by link type.

## Cycle Prevention

Flow prevents cycles for `blocks` and `depends_on` links. When adding a blocking link from parent to child, Flow checks whether the child already reaches the parent through existing blocking links. `related` links are informational and are excluded from cycle checks.

## Auto-Promotion

When a task moves to `done`, Flow checks its blocking children. If a child is still in `backlog` and every blocking parent is now `done`, Flow moves the child to `todo` and adds a system note explaining that the dependency was unblocked.

Example:

1. Link `flow_000001` blocks `flow_000002`.
2. `flow_000002` is in `backlog`.
3. Move `flow_000001` to `done`.
4. Flow automatically moves `flow_000002` to `todo`.
