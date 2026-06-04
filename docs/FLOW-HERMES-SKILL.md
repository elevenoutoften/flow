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
    "files_changed": ["src/feature.py"],
    "decisions": ["Used Redis for caching"],
    "remaining_concerns": ["Performance under load not tested"]
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

Link types: `blocks`, `depends_on`, `related`, `duplicates`

## Ideas

Capture ideas and promote them to tasks:

```bash
# Create idea
curl -s -X POST -H "Authorization: Bearer $FLOW_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"title":"New feature idea","description":"Could improve UX","project":"default"}' \
  "$FLOW_BASE_URL/api/ideas"

# Promote to tasks
curl -s -X POST -H "Authorization: Bearer $FLOW_API_KEY" \
  "$FLOW_BASE_URL/api/ideas/{idea_id}/promote"
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