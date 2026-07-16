# REST API

Base URL: `http://localhost:8100` (replace with your deployment URL)

## Authentication

All API endpoints require authentication via one of:

- **Bearer token** (recommended): `Authorization: Bearer <api-key>`
- **Session cookie**: `flow_session=<signed-cookie>` (when `FLOW_SESSION_SECRET` is set)
- **Trusted headers** (behind proxy): `X-Axis-Admin: 1`, `X-Axis-User: alice`, `X-Axis-Agent: codex` — requires `FLOW_TRUSTED_HEADERS=true`

Bearer tokens take precedence. See [Security](Security.md) for details.

## Projects

| Method | Path | Permission | Description |
|--------|------|-----------|-------------|
| `GET` | `/api/projects` | `tasks:read` | List all projects |
| `GET` | `/api/projects/{slug}` | `tasks:read` | Get project |
| `POST` | `/api/projects` | `tasks:create` | Create project |
| `PATCH` | `/api/projects/{slug}` | `tasks:edit` | Update project |

**Project fields:** `slug`, `name`, `description`, `repo_url`, `repo_path`, `default_branch`, `notes`, `created_at`, `updated_at`

Slug validation: lowercase alphanumeric with hyphens, 2–120 chars.

## Tasks

| Method | Path | Permission | Description |
|--------|------|-----------|-------------|
| `GET` | `/api/tasks` | `tasks:read` | List tasks |
| `GET` | `/api/tasks/next` | `tasks:read` | Get next unclaimed task |
| `GET` | `/api/tasks/{task_id}` | `tasks:read` | Get task |
| `POST` | `/api/tasks` | `tasks:create` | Create task |
| `PATCH` | `/api/tasks/{task_id}` | `tasks:read` + field-specific | Update task |
| `POST` | `/api/tasks/{task_id}/claim` | `tasks:claim` | Claim task |
| `POST` | `/api/tasks/{task_id}/release` | `tasks:claim` | Release task |
| `POST` | `/api/tasks/{task_id}/move` | `tasks:move` | Move task |
| `POST` | `/api/tasks/{task_id}/note` | `tasks:note` | Add note |
| `POST` | `/api/tasks/{task_id}/done` | `tasks:done` | Mark done |

### List Query Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `project` | string | Filter by project slug |
| `status` | string | Filter by status |
| `assignee` | string | Filter by assignee |
| `unclaimed` | bool | Only unclaimed tasks |
| `limit` | int | Page size (default 50, max 200) |
| `offset` | int | Skip N results (default 0) |

Response: `{items, total, limit, offset}`

### Task Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `title` | string | *(required)* | Task title |
| `status` | string | `backlog` | Board column |
| `priority` | int | `50` | Priority (0–1000) |
| `project` | string | `default` | Project slug |
| `assignee` | string/null | null | Claimant name |
| `human_required` | bool | `false` | Needs human attention |
| `assignee_type` | enum | `agent` | `agent`, `human`, `mixed` |
| `blocker_reason` | string | `""` | Why human is needed |
| `complexity` | enum | `small` | `trivial`, `small`, `medium`, `large`, `epic` |
| `impact` | enum | `medium` | `low`, `medium`, `high`, `critical` |
| `effort` | enum | `medium` | `low`, `medium`, `high` |
| `risk` | enum | `low` | `low`, `medium`, `high` |
| `description` | string | `""` | Task description |
| `acceptance_criteria` | string | `""` | Acceptance criteria |

### Get Next Task

Returns the highest-priority unclaimed task from `todo` or `backlog` that is dispatch-ready (not human_required, no blocking dependencies). Returns 404 if none available.

### Claim

```json
{"agent_name": "codex"}
```

If already claimed by a different agent, returns 409. Claiming moves from `backlog`/`todo` to `doing` (if the role allows).

### Move

```json
{"status": "review"}
```

Valid transitions depend on role. See [Security](Security.md) for the transition matrix.

### Human-Required Fields

Setting `human_required=true` with `assignee_type` requires `tasks:set_human_required` (admin, architect). Implementers can set it on their own tasks. Reviewers can set it on tasks in `review`. Only admin and architect can clear it.

## Ideas

| Method | Path | Permission | Description |
|--------|------|-----------|-------------|
| `GET` | `/api/ideas` | `tasks:read` | List ideas |
| `GET` | `/api/ideas/{idea_id}` | `tasks:read` | Get idea |
| `POST` | `/api/ideas` | `tasks:create` | Create idea |
| `PATCH` | `/api/ideas/{idea_id}` | `tasks:edit` | Update idea |
| `POST` | `/api/ideas/{idea_id}/archive` | `tasks:edit` | Archive |
| `POST` | `/api/ideas/{idea_id}/unarchive` | `tasks:edit` | Unarchive |
| `POST` | `/api/ideas/{idea_id}/promote` | `tasks:create` | Promote to tasks |

Query: `project` (slug filter), `archived` (bool, default false).

## API Keys

| Method | Path | Permission | Description |
|--------|------|-----------|-------------|
| `GET` | `/api/api-keys` | `key:manage` | List keys |
| `POST` | `/api/api-keys` | `key:manage` | Create key |
| `POST` | `/api/api-keys/{id}/revoke` | `key:manage` | Revoke key |

The `api_key` field is returned only on creation.

## Agents

| Method | Path | Permission | Description |
|--------|------|-----------|-------------|
| `GET` | `/api/agents` | `agent:read` | List agents |
| `GET` | `/api/agents/{id}` | `agent:read` | Get agent |
| `POST` | `/api/agents` | `agent:manage` | Create agent |
| `PATCH` | `/api/agents/{id}` | `agent:manage` | Update agent |

## Agent Runs

| Method | Path | Permission | Description |
|--------|------|-----------|-------------|
| `GET` | `/api/agent-runs` | `tasks:read` | List runs |
| `GET` | `/api/agent-runs/{id}` | `tasks:read` | Get run |
| `POST` | `/api/agents/{id}/dispatch?task_id=` | `dispatch` | Dispatch agent |
| `POST` | `/api/agent-runs/{id}/heartbeat` | `dispatch` | Record heartbeat |
| `POST` | `/api/agent-runs/{id}/complete?exit_code=` | `dispatch` | Complete run |
| `POST` | `/api/agent-runs/stale-recovery` | `dispatch` | Recover stale runs |

## Automation Rules

| Method | Path | Permission | Description |
|--------|------|-----------|-------------|
| `GET` | `/api/automation-rules` | `rules:read` | List rules |
| `GET` | `/api/automation-rules/{id}` | `rules:read` | Get rule |
| `POST` | `/api/automation-rules` | `rules:manage` | Create rule |
| `PATCH` | `/api/automation-rules/{id}` | `rules:manage` | Update rule |
| `POST` | `/api/automation-rules/evaluate` | `rules:evaluate` | Evaluate rules |
| `POST` | `/api/automation-rules/dry-run` | `rules:evaluate` | Dry-run rules |

## Task Links

| Method | Path | Permission | Description |
|--------|------|-----------|-------------|
| `POST` | `/api/tasks/{task_id}/link` | `links:manage` | Create link |
| `DELETE` | `/api/tasks/{task_id}/link/{link_id}` | `links:manage` | Delete link |
| `GET` | `/api/tasks/{task_id}/links` | `links:read` | List links |
| `GET` | `/api/tasks/{task_id}/dependencies` | `links:read` | Dependency summary |

## Handoffs

| Method | Path | Permission | Description |
|--------|------|-----------|-------------|
| `POST` | `/api/tasks/{task_id}/handoffs` | `handoff:create` | Create handoff |
| `GET` | `/api/tasks/{task_id}/handoffs` | `handoff:read` | List handoffs |

Compatibility route: `POST /api/tasks/{task_id}/handoff`

## Workspace Configs

| Method | Path | Permission | Description |
|--------|------|-----------|-------------|
| `GET` | `/api/workspace-configs` | `workspace:read` | List configs |
| `GET` | `/api/workspace-configs/{id}` | `workspace:read` | Get config |
| `POST` | `/api/workspace-configs` | `workspace:manage` | Create config |
| `PATCH` | `/api/workspace-configs/{id}` | `workspace:manage` | Update config |
| `POST` | `/api/workspace-configs/{id}/provision` | `workspace:manage` | Provision workspace |
| `POST` | `/api/workspace-configs/{id}/cleanup` | `workspace:manage` | Cleanup workspace |

## Webhooks

| Method | Path | Permission | Description |
|--------|------|-----------|-------------|
| `POST` | `/api/webhooks` | `webhook:manage` | Create webhook |
| `GET` | `/api/webhooks` | `webhook:read` | List webhooks |
| `GET` | `/api/webhooks/{id}` | `webhook:read` | Get webhook |
| `PATCH` | `/api/webhooks/{id}` | `webhook:manage` | Update webhook |
| `DELETE` | `/api/webhooks/{id}` | `webhook:manage` | Delete webhook |
| `GET` | `/api/webhooks/{id}/deliveries` | `webhook:read` | List deliveries |
| `GET` | `/api/webhooks/{id}/deliveries/{did}` | `webhook:read` | Get delivery |
| `POST` | `/api/webhooks/{id}/deliveries/{did}/retry` | `webhook:manage` | Retry delivery |

## Markdown Import

| Method | Path | Permission | Description |
|--------|------|-----------|-------------|
| `POST` | `/api/import/markdown/preview` | `tasks:create` | Preview import |
| `POST` | `/api/import/markdown/commit` | `tasks:create` | Commit import |

## Board and Health

| Method | Path | Permission | Description |
|--------|------|-----------|-------------|
| `GET` | `/` | `board:view` | HTML Kanban board |
| `GET` | `/events/board` | `tasks:read` | SSE board event stream |
| `GET` | `/healthz` | None (public) | Health check |
| `GET` | `/healthz/config` | None (public) | Config status |

## Common Errors

| Status | Meaning |
|--------|---------|
| `401` | No valid authentication |
| `403` | Insufficient permission |
| `404` | Resource not found |
| `409` | Conflict (e.g., task already claimed) |
| `422` | Validation error |

Error format: `{"detail": "Human-readable error message"}`

## See Also

- [Security](Security.md) — roles and permissions
- [MCP Interface](MCP.md) — JSON-RPC 2.0 tools
- [Architecture](../Architecture.md) — system design
