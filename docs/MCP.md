# Flow MCP Interface

Flow provides a Model Context Protocol (MCP) endpoint for LLM agents to interact with the task board programmatically.

## Endpoint

```
POST /mcp
```

Content type: `application/json`  
Protocol: JSON-RPC 2.0

## Authentication

Same as the REST API. Send either:

```http
Authorization: Bearer <api-key>
```

Or (when behind a trusted proxy with `FLOW_TRUSTED_HEADERS=true`):

> **Note:** Trusted headers require `FLOW_TRUSTED_HEADERS=true` in the Flow server environment.

```http
X-Axis-Admin: 1
X-Axis-User: alice
X-Axis-Agent: codex
```

Bearer tokens take precedence. Unauthenticated requests receive a permission error.

## Methods

### initialize

Initial handshake. Returns server capabilities and version info.

**Request:**

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {}
}
```

**Response:**

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "protocolVersion": "2025-11-25",
    "capabilities": {
      "tools": {
        "listChanged": false
      }
    },
    "serverInfo": {
      "name": "flow",
      "version": "0.1.0"
    }
  }
}
```

### ping

Health check.

**Request:**

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "ping",
  "params": {}
}
```

**Response:**

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {}
}
```

### notifications/initialized

Client notification that initialization is complete. No response is returned (202).

**Request:**

```json
{
  "jsonrpc": "2.0",
  "method": "notifications/initialized",
  "params": {}
}
```

### tools/list

Returns all available Flow tools with their input schemas.

**Request:**

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "tools/list",
  "params": {}
}
```

**Response:** Returns the full tool list (see Tool Reference below).

### tools/call

Invoke a specific tool.

**Request:**

```json
{
  "jsonrpc": "2.0",
  "id": 4,
  "method": "tools/call",
  "params": {
    "name": "flow_list_tasks",
    "arguments": {
      "project": "default"
    }
  }
}
```

**Response:**

```json
{
  "jsonrpc": "2.0",
  "id": 4,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "Found 5 Flow tasks."
      }
    ],
    "structuredContent": {
      "tasks": [...],
      "count": 5
    },
    "isError": false
  }
}
```

---

## Tool Reference

### flow_list_tasks

List tasks, optionally filtered.

**Permission:** `tasks:read`

**Arguments:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `project` | string | No | Project slug filter |
| `status` | string | No | Status filter (`backlog`, `todo`, `doing`, `review`, `done`) |

**Example:**

```json
{
  "name": "flow_list_tasks",
  "arguments": {
    "project": "default",
    "status": "todo"
  }
}
```

### flow_get_task

Get a single task by ID.

**Permission:** `tasks:read`

**Arguments:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `task_id` | string | Yes | Flow task ID (e.g., `flow_000001`) |

**Example:**

```json
{
  "name": "flow_get_task",
  "arguments": {
    "task_id": "flow_000001"
  }
}
```

### flow_create_task

Create a new task.

**Permission:** `tasks:create`

**Arguments:**

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `title` | string | Yes | — | Task title |
| `status` | string | No | `backlog` | Initial status |
| `priority` | integer | No | `50` | Priority (0–1000) |
| `project` | string | No | `default` | Project slug |
| `description` | string | No | `""` | Task description |
| `acceptance_criteria` | string | No | `""` | Acceptance criteria |

**Example:**

```json
{
  "name": "flow_create_task",
  "arguments": {
    "title": "Add GPU monitoring",
    "status": "todo",
    "priority": 75,
    "project": "default",
    "description": "Set up NVIDIA DCGM exporter"
  }
}
```

### flow_update_task

Update any fields on a task.

**Permission:** `tasks:edit` (plus `tasks:set_human_required` for human-required fields)

**Arguments:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `task_id` | string | Yes | Task ID |
| `title` | string | No | New title |
| `status` | string | No | New status |
| `priority` | integer | No | New priority (0–1000) |
| `project` | string | No | New project slug |
| `assignee` | string/null | No | Assignee name |
| `human_required` | boolean | No | Human-required flag |
| `assignee_type` | string | No | `agent`, `human`, or `mixed` |
| `blocker_reason` | string | No | Blocker explanation |
| `complexity` | string | No | `trivial`, `small`, `medium`, `large`, `epic` |
| `impact` | string | No | `low`, `medium`, `high`, `critical` |
| `effort` | string | No | `low`, `medium`, `high` |
| `risk` | string | No | `low`, `medium`, `high` |
| `description` | string | No | Task description |
| `acceptance_criteria` | string | No | Acceptance criteria |

**Example:**

```json
{
  "name": "flow_update_task",
  "arguments": {
    "task_id": "flow_000001",
    "priority": 90,
    "description": "Updated: now includes DCGM and nvtop"
  }
}
```

### flow_move_task

Move a task to a different status.

**Permission:** `tasks:move`

**Arguments:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `task_id` | string | Yes | Task ID |
| `status` | string | Yes | Target status |

**Example:**

```json
{
  "name": "flow_move_task",
  "arguments": {
    "task_id": "flow_000001",
    "status": "review"
  }
}
```

### flow_set_human_required

Set or clear the human-required blocker on a task. Only admin and architect roles can clear `human_required` (set it to `false`). Implementers and reviewers can only set it to `true` under restricted conditions.

**Permission:** `tasks:set_human_required` (admin/architect), or restricted for implementer/reviewer (see [ROLES.md](ROLES.md))

**Arguments:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `task_id` | string | Yes | Task ID |
| `human_required` | boolean | Yes | Set or clear the flag |
| `blocker_reason` | string | No | Reason (required when setting) |
| `assignee_type` | string | No | `agent`, `human`, or `mixed` |

**Example — marking a task as needing human help:**

```json
{
  "name": "flow_set_human_required",
  "arguments": {
    "task_id": "flow_000001",
    "human_required": true,
    "blocker_reason": "Requires hardware access to test",
    "assignee_type": "human"
  }
}
```

**Example — clearing the blocker (admin or architect only):**

```json
{
  "name": "flow_set_human_required",
  "arguments": {
    "task_id": "flow_000001",
    "human_required": false
  }
}
```

### flow_board_summary

Get a summary of the board.

**Permission:** `tasks:read`

**Arguments:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `project` | string | No | Project slug filter |

**Response includes:** `counts_by_status` (task count per column) and `human_required_tasks` (list of tasks with `human_required=true`).

**Example:**

```json
{
  "name": "flow_board_summary",
  "arguments": {
    "project": "default"
  }
}
```

### flow_list_ideas

List ideas.

**Permission:** `tasks:read`

**Arguments:**

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `project` | string | No | — | Project slug filter |
| `archived` | boolean | No | `false` | Include archived ideas |

**Example:**

```json
{
  "name": "flow_list_ideas",
  "arguments": {
    "project": "default",
    "archived": false
  }
}
```

### flow_create_idea

Create a new idea.

**Permission:** `tasks:create`

**Arguments:**

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `title` | string | Yes | — | Idea title |
| `description` | string | No | `""` | Idea description |
| `project` | string | No | `default` | Project slug |
| `author` | string/null | No | — | Author name |

**Example:**

```json
{
  "name": "flow_create_idea",
  "arguments": {
    "title": "Add dark mode to board UI",
    "description": "Users have requested a dark theme",
    "project": "default",
    "author": "alice"
  }
}
```

### flow_update_idea

Update an idea.

**Permission:** `tasks:edit`

**Arguments:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `idea_id` | string | Yes | Idea ID |
| `title` | string | No | New title |
| `description` | string | No | New description |
| `project` | string | No | New project slug |
| `author` | string/null | No | New author |

**Example:**

```json
{
  "name": "flow_update_idea",
  "arguments": {
    "idea_id": "idea_000001",
    "title": "Add dark mode and light mode toggle"
  }
}
```

### flow_archive_idea

Archive an idea.

**Permission:** `tasks:edit`

**Arguments:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `idea_id` | string | Yes | Idea ID |

**Example:**

```json
{
  "name": "flow_archive_idea",
  "arguments": {
    "idea_id": "idea_000001"
  }
}
```

### flow_promote_idea

Promote an idea into linked tasks.

**Permission:** `tasks:create`

**Arguments:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `idea_id` | string | Yes | Idea ID |
| `tasks` | array | Yes | List of task specs to create |

Each task spec:

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `title` | string | Yes | — | Task title |
| `status` | string | No | `backlog` | Initial status |
| `priority` | integer | No | `50` | Priority (0–1000) |
| `description` | string | No | `""` | Task description |
| `acceptance_criteria` | string | No | `""` | Acceptance criteria |

**Example:**

```json
{
  "name": "flow_promote_idea",
  "arguments": {
    "idea_id": "idea_000001",
    "tasks": [
      {
        "title": "Design dark mode CSS",
        "status": "backlog",
        "priority": 60
      },
      {
        "title": "Add theme toggle to UI",
        "status": "backlog",
        "priority": 60
      }
    ]
  }
}
```

---

## Complete Examples

### Listing tasks

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "flow_list_tasks",
    "arguments": {
      "project": "default",
      "status": "todo"
    }
  }
}
```

### Marking a task as human-required

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/call",
  "params": {
    "name": "flow_set_human_required",
    "arguments": {
      "task_id": "flow_000001",
      "human_required": true,
      "blocker_reason": "Needs physical hardware access",
      "assignee_type": "human"
    }
  }
}
```

### Moving a task to review

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "tools/call",
  "params": {
    "name": "flow_move_task",
    "arguments": {
      "task_id": "flow_000001",
      "status": "review"
    }
  }
}
```

### Creating and promoting an idea

```json
{
  "jsonrpc": "2.0",
  "id": 4,
  "method": "tools/call",
  "params": {
    "name": "flow_create_idea",
    "arguments": {
      "title": "Improve GPU monitoring",
      "project": "default"
    }
  }
}
```

Then promote it (using the returned idea ID):

```json
{
  "jsonrpc": "2.0",
  "id": 5,
  "method": "tools/call",
  "params": {
    "name": "flow_promote_idea",
    "arguments": {
      "idea_id": "idea_000001",
      "tasks": [
        {
          "title": "Install DCGM exporter",
          "status": "todo",
          "priority": 80
        },
        {
          "title": "Create Grafana dashboard",
          "status": "todo",
          "priority": 70
        }
      ]
    }
  }
}
```

---

## Role and Scope Behavior

Each tool enforces the same permission model as the REST API. The actor's API key role determines what actions are allowed:

| Tool | Minimum Role |
|------|-------------|
| `flow_list_tasks` | `read_only` |
| `flow_get_task` | `read_only` |
| `flow_board_summary` | `read_only` |
| `flow_list_ideas` | `read_only` |
| `flow_create_task` | `implementer` (or any role with `tasks:create`) |
| `flow_create_idea` | `implementer` (or any role with `tasks:create`) |
| `flow_promote_idea` | `implementer` (or any role with `tasks:create`) |
| `flow_update_task` | `architect` (for full edit), restricted for implementer/reviewer |
| `flow_move_task` | `implementer` (or any role with `tasks:move`) |
| `flow_set_human_required` | `architect` (full), restricted for implementer/reviewer |
| `flow_update_idea` | `architect` (or any role with `tasks:edit`) |
| `flow_archive_idea` | `architect` (or any role with `tasks:edit`) |

See [ROLES.md](ROLES.md) for the complete permission matrix and transition rules.
