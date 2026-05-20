# Flow REST API Reference

Base URL: `http://localhost:8100` (replace with your deployment URL)

## Authentication

All API endpoints require authentication. Flow supports two authentication methods:

### Bearer Token (recommended for agents)

```http
Authorization: Bearer <api-key>
```

API keys are created in the board UI or via the `/api/api-keys` endpoint. Each key has a role that determines its permissions. See [ROLES.md](ROLES.md) for details.

### X-Axis-* Headers (trusted proxy)

> **Warning: These headers are ignored by default.** Set `FLOW_TRUSTED_HEADERS=true` to enable them.
> Only enable when deploying behind a reverse proxy that strips all inbound `X-Axis-*` headers.
> See [Trusted Header Configuration](#trusted-header-configuration) below.

When Flow sits behind a trusted reverse proxy, the proxy can set identity headers:

```http
X-Axis-Admin: 1          # Grants admin role
X-Axis-User: alice       # Grants read_only role
X-Axis-Agent: codex      # Grants read_only role (fallback)
```

If `X-Axis-Admin: 1` is present, the actor gets the `admin` role. Otherwise, the first present value of `X-Axis-User` or `X-Axis-Agent` grants `read_only`.

#### Trusted Header Configuration

By default, `resolve_actor()` ignores all `X-Axis-*` headers and only accepts Bearer token authentication. To enable trusted header auth (required when behind a reverse proxy like Caddy or nginx):

```bash
FLOW_TRUSTED_HEADERS=true
```

**Warning:** Only enable if your proxy strips all inbound `X-Axis-*` headers before setting its own. Otherwise, any client can spoof admin access.

Bearer tokens take precedence over headers.

---

## Projects

### List Projects

```
GET /api/projects
```

**Permission:** `tasks:read`

**Response:**

```json
[
  {
    "slug": "default",
    "name": "Flow",
    "description": "Task management project",
    "repo_url": "",
    "repo_path": "",
    "default_branch": "main",
    "notes": "",
    "created_at": "2025-01-15T10:00:00",
    "updated_at": "2025-01-15T10:00:00"
  }
]
```

### Get Project

```
GET /api/projects/{slug}
```

**Permission:** `tasks:read`

**Response:** Single project object (same shape as list).

**Errors:** `404` if project not found.

### Create Project

```
POST /api/projects
```

**Permission:** `tasks:create`

**Request body:**

```json
{
  "slug": "my-project",
  "name": "My Project",
  "description": "Optional description",
  "repo_url": "https://github.com/org/repo",
  "repo_path": "",
  "default_branch": "main",
  "notes": ""
}
```

**Response:** Created project object (201).

**Errors:** `422` if slug is invalid (must be lowercase alphanumeric with hyphens, 2–120 chars).

### Update Project

```
PATCH /api/projects/{slug}
```

**Permission:** `tasks:edit`

**Request body:** Any subset of `name`, `description`, `repo_url`, `repo_path`, `default_branch`, `notes`.

**Response:** Updated project object.

**Errors:** `404` if project not found.

---

## Tasks

### List Tasks

```
GET /api/tasks
```

**Permission:** `tasks:read`

**Query parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `project` | string | Filter by project slug |
| `status` | string | Filter by status (`backlog`, `todo`, `doing`, `review`, `done`) |
| `assignee` | string | Filter by assignee name |
| `unclaimed` | bool | If `true`, return only unclaimed tasks |

**Response:**

```json
[
  {
    "id": "flow_000001",
    "title": "Add GPU monitoring",
    "status": "todo",
    "priority": 50,
    "project": "default",
    "assignee": null,
    "human_required": false,
    "assignee_type": "agent",
    "blocker_reason": "",
    "complexity": "medium",
    "impact": "high",
    "effort": "medium",
    "risk": "low",
    "description": "Set up NVIDIA DCGM exporter",
    "acceptance_criteria": "Metrics visible in Grafana",
    "source_filename": null,
    "source_line": null,
    "import_batch_id": null,
    "source_title": null,
    "notes": [],
    "created_at": "2025-01-15T10:00:00",
    "updated_at": "2025-01-15T10:00:00"
  }
]
```

### Get Next Task

```
GET /api/tasks/next
```

**Permission:** `tasks:read`

**Query parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `project` | string | Filter by project slug |

Returns the highest-priority unclaimed task from `todo` or `backlog`.

**Errors:** `404` if no unclaimed task is available.

### Get Task

```
GET /api/tasks/{task_id}
```

**Permission:** `tasks:read`

**Response:** Single task object.

**Errors:** `404` if task not found.

### Create Task

```
POST /api/tasks
```

**Permission:** `tasks:create`

**Request body:**

```json
{
  "title": "Fix login timeout",
  "status": "backlog",
  "priority": 75,
  "project": "default",
  "assignee": null,
  "human_required": false,
  "assignee_type": "agent",
  "blocker_reason": "",
  "complexity": "small",
  "impact": "medium",
  "effort": "low",
  "risk": "low",
  "description": "Users report login timing out after 30s",
  "acceptance_criteria": "Login completes within 10s under normal load"
}
```

All fields except `title` are optional. Defaults: `status=backlog`, `priority=50`, `project=default`, `human_required=false`, `assignee_type=agent`, `complexity=small`, `impact=medium`, `effort=medium`, `risk=low`.

**Response:** Created task object (201).

### Update Task

```
PATCH /api/tasks/{task_id}
```

**Permission:** `tasks:read` (base), plus additional permissions for specific fields (see below).

**Request body:** Any subset of task fields.

**Human-required field permissions:**

Setting `human_required=true` with `assignee_type` requires the `tasks:set_human_required` permission (admin and architect roles only). Implementers can set `human_required=true` only on tasks they are assigned to. Reviewers can set `human_required=true` only on tasks in `review` status.

Clearing `human_required` (setting to `false`) is restricted to admin and architect roles only. When cleared, `blocker_reason` is automatically cleared as well. Implementers and reviewers cannot clear `human_required`.

**Response:** Updated task object.

**Errors:** `404` if task not found, `403` if insufficient permission.

### Claim Task

```
POST /api/tasks/{task_id}/claim
```

**Permission:** `tasks:claim`

**Request body:**

```json
{
  "agent_name": "codex"
}
```

If `agent_name` is omitted, the actor's name is used. If the task is already claimed by a different agent, returns `409`. Claiming moves the task from `backlog` or `todo` to `doing` (if the role allows).

**Response:** Updated task object.

### Release Task

```
POST /api/tasks/{task_id}/release
```

**Permission:** `tasks:claim`

Clears the assignee. If the task was in `doing`, it moves back to `todo`.

**Response:** Updated task object.

### Move Task

```
POST /api/tasks/{task_id}/move
```

**Permission:** `tasks:move`

**Request body:**

```json
{
  "status": "review"
}
```

Valid transitions depend on role. See [ROLES.md](ROLES.md) for the full matrix.

**Response:** Updated task object.

**Errors:** `403` if the transition is not allowed for the actor's role.

### Add Note

```
POST /api/tasks/{task_id}/note
```

**Permission:** `tasks:edit`

**Request body:**

```json
{
  "note": "Made progress on the API endpoint",
  "author": "codex"
}
```

If `author` is omitted, the actor's name is used.

**Response:** Updated task object with new note appended.

### Mark Task Done

```
POST /api/tasks/{task_id}/done
```

**Permission:** `tasks:done`

**Request body:**

```json
{
  "summary": "Implemented and tested the endpoint",
  "author": "codex"
}
```

Moves the task to `done` and appends the summary as a note.

**Response:** Updated task object.

**Errors:** `403` if the role cannot mark the task as done from its current status.

---

## Human-Required Fields

Tasks have three fields that work together to signal when human intervention is needed:

| Field | Type | Description |
|-------|------|-------------|
| `human_required` | bool | When `true`, the task needs human attention |
| `assignee_type` | enum | `agent`, `human`, or `mixed` — who should handle this |
| `blocker_reason` | string | Free-text explanation of why a human is needed |

When `human_required` is set to `false`, `blocker_reason` is automatically cleared. Only admin and architect roles can clear `human_required`; implementers and reviewers cannot.

Only roles with `tasks:set_human_required` permission (admin, architect) can freely set these fields. Implementers and reviewers have restricted access — see [ROLES.md](ROLES.md).

---

## Qualification Fields

Every task carries four qualification fields for sizing and prioritization:

| Field | Values |
|-------|--------|
| `complexity` | `trivial`, `small`, `medium`, `large`, `epic` |
| `impact` | `low`, `medium`, `high`, `critical` |
| `effort` | `low`, `medium`, `high` |
| `risk` | `low`, `medium`, `high` |

These are informational and do not affect workflow transitions.

---

## Ideas

### List Ideas

```
GET /api/ideas
```

**Permission:** `tasks:read`

**Query parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `project` | string | — | Filter by project slug |
| `archived` | bool | `false` | Include archived ideas |

**Response:**

```json
[
  {
    "id": "idea_000001",
    "title": "Add dark mode to board UI",
    "description": "Users want a dark theme option",
    "project": "default",
    "author": "alice",
    "archived_at": null,
    "promoted_task_ids": [],
    "created_at": "2025-01-15T10:00:00",
    "updated_at": "2025-01-15T10:00:00"
  }
]
```

### Get Idea

```
GET /api/ideas/{idea_id}
```

**Permission:** `tasks:read`

**Errors:** `404` if idea not found.

### Create Idea

```
POST /api/ideas
```

**Permission:** `tasks:create`

**Request body:**

```json
{
  "title": "Support multiple boards",
  "description": "Allow separate boards per team",
  "project": "default",
  "author": "alice"
}
```

**Response:** Created idea object (201).

### Update Idea

```
PATCH /api/ideas/{idea_id}
```

**Permission:** `tasks:edit`

**Request body:** Any subset of `title`, `description`, `project`, `author`.

**Response:** Updated idea object.

### Archive Idea

```
POST /api/ideas/{idea_id}/archive
```

**Permission:** `tasks:edit`

Sets `archived_at` timestamp. Archived ideas are excluded from default listings.

**Response:** Updated idea object.

### Unarchive Idea

```
POST /api/ideas/{idea_id}/unarchive
```

**Permission:** `tasks:edit`

Clears `archived_at`.

**Response:** Updated idea object.

### Promote Idea to Tasks

```
POST /api/ideas/{idea_id}/promote
```

**Permission:** `tasks:create`

**Request body:**

```json
[
  {
    "title": "Design multi-board UI",
    "status": "backlog",
    "priority": 50,
    "description": "",
    "acceptance_criteria": ""
  },
  {
    "title": "Implement board switching API",
    "status": "backlog",
    "priority": 50,
    "description": "",
    "acceptance_criteria": ""
  }
]
```

Creates linked tasks from the idea. The idea's `promoted_task_ids` field is updated.

**Response:** Updated idea object with `promoted_task_ids`.

---

## API Keys

### List API Keys

```
GET /api/api-keys
```

**Permission:** `key:manage`

**Response:**

```json
[
  {
    "id": "key_000001",
    "name": "codex-agent",
    "description": "Codex implementation agent",
    "role": "implementer",
    "key_prefix": "flow_sk_abc123",
    "created_at": "2025-01-15T10:00:00",
    "revoked_at": null
  }
]
```

### Create API Key

```
POST /api/api-keys
```

**Permission:** `key:manage`

**Request body:**

```json
{
  "name": "my-agent",
  "description": "Description of the agent",
  "role": "implementer"
}
```

Valid roles: `admin`, `architect`, `implementer`, `reviewer`, `read_only`.

**Response:**

```json
{
  "id": "key_000002",
  "name": "my-agent",
  "description": "Description of the agent",
  "role": "implementer",
  "key_prefix": "flow_sk_xyz789",
  "api_key": "flow_sk_full_key_here...",
  "created_at": "2025-01-15T10:00:00",
  "revoked_at": null
}
```

The `api_key` field is returned only on creation and is shown once.

### Revoke API Key

```
POST /api/api-keys/{api_key_id}/revoke
```

**Permission:** `key:manage`

**Response:** Updated key object with `revoked_at` set.

**Errors:** `404` if key not found.

---

## Board and Health

### HTML Board

```
GET /
```

**Permission:** `board:view` (via auth)

Returns the Kanban board HTML page. Supports `?project=<slug>` to filter by project.

### Health Check

```
GET /healthz
```

**Permission:** None (public)

**Response:**

```json
{
  "ok": true,
  "database": true
}
```

Returns `503` if the database is unreachable.

---

## Markdown Import

### Preview Import

```
POST /api/import/markdown/preview
```

**Permission:** `tasks:create`

**Request body:**

```json
{
  "markdown": "- [ ] Fix login timeout\n- [x] Set up CI\n- [ ] Add GPU monitoring",
  "source_filename": "tasks.md",
  "default_project": "default",
  "default_status": "backlog",
  "default_priority": 50
}
```

Parses Markdown checkboxes into task previews. Checked items (`[x]`) are skipped. Detects duplicates by title and source location.

**Response:**

```json
{
  "items": [
    {
      "preview_id": "...",
      "title": "Fix login timeout",
      "status": "backlog",
      "priority": 50,
      "project": "default",
      "assignee": null,
      "description": "",
      "acceptance_criteria": "",
      "source_filename": "tasks.md",
      "source_line": 1,
      "source_title": null,
      "duplicate": false,
      "duplicate_task_id": null
    }
  ]
}
```

### Commit Import

```
POST /api/import/markdown/commit
```

**Permission:** `tasks:create`

**Request body:** The `items` array from the preview response, optionally with `duplicate: true` set on items to skip.

**Response:**

```json
{
  "import_batch_id": "import_abc123def456",
  "created": [...],
  "skipped": [...]
}
```

---

## Common Errors

| Status | Meaning |
|--------|---------|
| `401` | Authentication is required — no valid bearer token or identity headers |
| `403` | Insufficient permission — the actor's role does not allow this action |
| `404` | Resource not found — task, project, idea, or API key does not exist |
| `409` | Conflict — task is already claimed by another agent |
| `422` | Validation error — request body or query parameters are invalid |

Error response format:

```json
{
  "detail": "Human-readable error message"
}
```
