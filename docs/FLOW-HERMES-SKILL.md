---
name: flow-agent
description: Connect to a Flow board and manage tasks via REST API
---

# Flow Board Agent Skill

Connect to a Flow Kanban board and manage tasks using the REST API.

## Setup

Set these environment variables (or configure them in your Hermes profile):

```
FLOW_BASE_URL=http://localhost:8100   # Your Flow server URL
FLOW_API_KEY=flow_xxxxx               # Your API key
FLOW_AGENT_NAME=my-agent              # Your agent name
```

> **Hermes wrapper note:** When Flow dispatches to a Hermes agent via the
> built-in `hermes_wrapper`, the wrapper reads `HERMES_AGENT_NAME` (not
> `FLOW_AGENT_NAME`) to identify itself. If you're running the Hermes adapter,
> set `HERMES_AGENT_NAME` in the agent's env_allowlist or the dispatch
> environment.

## Authentication

All requests use Bearer token auth:

```
Authorization: Bearer <FLOW_API_KEY>
Accept: application/json
Content-Type: application/json  (for POST/PATCH)
```

## Core Agent Loop

### 1. Pick a Task

```bash
curl -s -H "Authorization: Bearer $FLOW_API_KEY" \
  "$FLOW_BASE_URL/api/tasks/next?project=default"
```

Or list available tasks:

```bash
curl -s -H "Authorization: Bearer $FLOW_API_KEY" \
  "$FLOW_BASE_URL/api/tasks?project=default&status=todo"
```

### 2. Claim the Task

```bash
curl -s -X POST -H "Authorization: Bearer $FLOW_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"agent_name":"my-agent"}' \
  "$FLOW_BASE_URL/api/tasks/{task_id}/claim"
```

### 3. Read Task Details

```bash
curl -s -H "Authorization: Bearer $FLOW_API_KEY" \
  "$FLOW_BASE_URL/api/tasks/{task_id}"
```

### 4. Add Progress Notes

```bash
curl -s -X POST -H "Authorization: Bearer $FLOW_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"note":"Implemented feature, running tests","author":"my-agent"}' \
  "$FLOW_BASE_URL/api/tasks/{task_id}/note"
```

### 5. Move Task Status

```bash
# Move to review when done implementing
curl -s -X POST -H "Authorization: Bearer $FLOW_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"status":"review"}' \
  "$FLOW_BASE_URL/api/tasks/{task_id}/move"

# Or update via PATCH
curl -s -X PATCH -H "Authorization: Bearer $FLOW_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"status":"doing"}' \
  "$FLOW_BASE_URL/api/tasks/{task_id}"
```

### 6. Complete with Summary

```bash
curl -s -X POST -H "Authorization: Bearer $FLOW_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"summary":"Feature X implemented and tested","author":"my-agent"}' \
  "$FLOW_BASE_URL/api/tasks/{task_id}/done"
```

## Task Statuses

- `backlog` — Not yet prioritized
- `todo` — Ready to be picked up
- `doing` — Currently being worked on
- `review` — Implemented, awaiting review
- `done` — Completed (terminal)

**Important:** Move tasks to `review` when done implementing. Only reviewers/admins should mark tasks `done`.

## Creating Tasks

```bash
curl -s -X POST -H "Authorization: Bearer $FLOW_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Implement login page",
    "description": "Add OAuth2 login flow",
    "project": "default",
    "priority": 5,
    "complexity": "medium",
    "impact": "high",
    "effort": "medium",
    "risk": "low"
  }' \
  "$FLOW_BASE_URL/api/tasks"
```

## Human-Required Blockers

Flag tasks that need a human decision:

```bash
curl -s -X PATCH -H "Authorization: Bearer $FLOW_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"human_required": true, "blocker_reason": "Needs design approval"}' \
  "$FLOW_BASE_URL/api/tasks/{task_id}"
```

## Handoffs (Agent-to-Agent Context)

Pass structured context between agents:

```bash
curl -s -X POST -H "Authorization: Bearer $FLOW_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "summary": "Feature X implemented, tests passing",
    "author": "implementer-1",
    "changed_files": ["src/feature.py"],
    "commands_run": ["pytest tests/"],
    "tests_run": ["pytest tests/"],
    "artifacts": [],
    "attempted_but_failed": [],
    "remaining_work": "",
    "outcome": "success",
    "next_recommended_agent": "reviewer",
    "capabilities": ["code", "testing"]
  }' \
  "$FLOW_BASE_URL/api/tasks/{task_id}/handoff"
```

## Linking Tasks

Create dependency relationships:

```bash
curl -s -X POST -H "Authorization: Bearer $FLOW_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"parent_id":"flow_000001","child_id":"flow_000002","link_type":"blocks"}' \
  "$FLOW_BASE_URL/api/tasks/flow_000001/link"
```

Link types: `blocks`, `depends_on`, `related`

## Ideas

Capture ideas and promote them to tasks:

```bash
# Create idea
curl -s -X POST -H "Authorization: Bearer $FLOW_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"title":"New feature idea","description":"Could improve UX","project":"default"}' \
  "$FLOW_BASE_URL/api/ideas"

# Promote to tasks (body is required — list of PromoteTaskSpec with title)
# Note: project is NOT in PromoteTaskSpec — promoted tasks inherit the idea's project.
curl -s -X POST -H "Authorization: Bearer $FLOW_API_KEY" \
  -H "Content-Type: application/json" \
  -d '[{"title":"Implement feature X","status":"todo"}]' \
  "$FLOW_BASE_URL/api/ideas/{idea_id}/promote"
```

### Promote semantics

`POST /api/ideas/{idea_id}/promote` takes a JSON array of `PromoteTaskSpec` objects:

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `title` | string | **yes** | — | Task title (non-empty) |
| `status` | string | no | `"backlog"` | Initial task status |
| `priority` | int | no | `50` | Task priority (0-1000) |
| `description` | string | no | `""` | Task description |
| `acceptance_criteria` | string | no | `""` | Acceptance criteria |

**Auto-archive:** The idea is automatically archived after promotion. The response is the updated `IdeaResponse` with `archived_at` set and `promoted_task_ids` populated with the new task IDs.

**Import batch ID:** Each promoted task gets `import_batch_id` set to the idea's ID, linking the tasks back to their source idea.

**Response:** Returns the archived idea as `IdeaResponse`:

```json
{
  "id": "idea_000001",
  "title": "New feature idea",
  "description": "Could improve UX",
  "project": "default",
  "author": "nyx",
  "archived_at": "2026-07-16T12:00:00Z",
  "promoted_task_ids": ["flow_000001", "flow_000002"],
  "created_at": "2026-07-16T11:00:00Z",
  "updated_at": "2026-07-16T12:00:00Z"
}
```

## MCP Interface

For agents that support Model Context Protocol, Flow also exposes `POST /mcp` with JSON-RPC 2.0:

```bash
curl -s -X POST -H "Authorization: Bearer $FLOW_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' \
  "$FLOW_BASE_URL/mcp"
```

Available MCP tools include: `flow_list_tasks`, `flow_get_task`, `flow_create_task`, `flow_update_task`, `flow_move_task`, `flow_next_task`, `flow_claim_task`, `flow_set_human_required`, `flow_board_summary`, and more. See the MCP documentation for the full list.

## Roles & Permissions

| Role | Claim | Create | Edit | Move to Review | Move to Done |
|------|-------|-------|------|----------------|-------------|
| `admin` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `architect` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `implementer` | ✅ | — | — | ✅ | — |
| `reviewer` | ✅ | — | — | — | ✅ |
| `read_only` | — | — | — | — | — |

## Tips

- **Claim before working** — prevents race conditions
- **Add notes frequently** — keeps the board current
- **Move to `review`, not `done`** — unless you're a reviewer or admin
- **Use `human_required`** for tasks blocked on human decisions
- **Read notes with `body` field** — note text is in `body`, not `content`