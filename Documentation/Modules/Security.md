# Security

## Source

| File | Role |
|------|------|
| `flow_app/security.py` | `Actor`, `Permission` enum, role matrix, auth resolution, session cookies |
| `flow_app/models.py:16-21` | `ApiKeyRole` enum |
| `flow_app/models.py:47-57` | `AgentApiKey` model |
| `flow_app/repository.py:226-270` | API key CRUD, hashing |

## Roles

| Role | Purpose |
|------|---------|
| `admin` | Full access — all permissions including key management |
| `architect` | Planning and task management — create, edit, move, complete tasks; manage agents, rules, webhooks, workspaces; cannot manage keys |
| `implementer` | Doing the work — claim tasks, move through doing/review, add notes to own tasks, dispatch agents, create handoffs |
| `reviewer` | Reviewing work — read tasks, move from review to done or back, add notes to review tasks, create handoffs |
| `read_only` | Observing — read tasks, view board; cannot mutate any state |

## Permission Matrix

| Permission | admin | architect | implementer | reviewer | read_only |
|------------|-------|-----------|-------------|----------|-----------|
| `tasks:read` | Yes | Yes | Yes | Yes | Yes |
| `tasks:create` | Yes | Yes | — | — | — |
| `tasks:edit` | Yes | Yes | — | — | — |
| `tasks:claim` | Yes | Yes | Yes | Yes | — |
| `tasks:move` | Yes | Yes | Yes | Yes | — |
| `tasks:note` | Yes | Yes | Yes* | Yes* | — |
| `tasks:done` | Yes | Yes | — | Yes | — |
| `tasks:set_human_required` | Yes | Yes | Yes* | Yes* | — |
| `key:manage` | Yes | — | — | — | — |
| `board:view` | Yes | Yes | Yes | Yes | Yes |
| `agent:read` | Yes | Yes | Yes | Yes | Yes |
| `agent:manage` | Yes | Yes | — | — | — |
| `dispatch` | Yes | Yes | Yes | — | — |
| `rules:read` | Yes | Yes | Yes | Yes | Yes |
| `rules:manage` | Yes | Yes | — | — | — |
| `rules:evaluate` | Yes | Yes | — | — | — |
| `links:read` | Yes | Yes | Yes | Yes | Yes |
| `links:manage` | Yes | Yes | — | — | — |
| `workspace:read` | Yes | Yes | Yes | Yes | Yes |
| `workspace:manage` | Yes | Yes | — | — | — |
| `webhook:manage` | Yes | Yes | — | — | — |
| `webhook:read` | Yes | Yes | Yes | Yes | Yes |
| `handoff:read` | Yes | Yes | Yes | Yes | Yes |
| `handoff:create` | Yes | Yes | Yes | Yes | — |
| `handoff:manage` | Yes | Yes | — | — | — |

\* = restricted, see below.

## Task Status Transitions by Role

| From → To | admin | architect | implementer | reviewer | read_only |
|-----------|-------|-----------|-------------|----------|-----------|
| `backlog` → `todo` | Yes | Yes | — | — | — |
| `backlog` → `doing` | Yes | Yes | — | — | — |
| `todo` → `doing` | Yes | Yes | Yes | Yes | — |
| `doing` → `review` | Yes | Yes | Yes | — | — |
| `review` → `doing` | Yes | Yes | Yes | Yes | — |
| `review` → `todo` | Yes | Yes | — | Yes | — |
| `review` → `done` | Yes | Yes | — | Yes | — |
| `doing` → `done` | Yes | Yes | — | — | — |
| Any → `backlog` | Yes | Yes | — | — | — |
| `done` → anything | — | — | — | — | — |

Key rules:
- `done` is terminal.
- `backlog` is protected — only admin and architect.
- Implementers work within `todo` → `doing` → `review`.
- Reviewers control `review` → `done` and can send tasks back.

## Note Permissions

| Role | Can Note | Condition |
|------|----------|-----------|
| admin | Any task | — |
| architect | Any task | — |
| implementer | Own tasks only | `task.claimer_key_id == actor.key_id` or `task.assignee == actor.name` |
| reviewer | Review tasks only | `task.status == "review"` |
| read_only | None | — |

## Human-Required Field Permissions

| Role | Can Set `human_required=true` | Can Clear `human_required` |
|------|------------------------------|---------------------------|
| admin | Always | Always |
| architect | Always | Always |
| implementer | Own assigned tasks only | Never |
| reviewer | Tasks in `review` only | Never |
| read_only | Never | Never |

Clearing `human_required` also clears `blocker_reason`.

## Authentication Methods

### Bearer Token

```http
Authorization: Bearer <api-key>
```

API keys are hashed with SHA-256 before storage. Lookup is by hash. Revoked keys are rejected.

### Session Cookie

When `FLOW_SESSION_SECRET` is set, the browser UI uses a signed cookie (`flow_session`). The cookie contains `{name, role, iat, exp}` signed with HMAC-SHA256. Expiry is 12 hours.

### Trusted Headers

Only accepted when `FLOW_TRUSTED_HEADERS=true`:

- `X-Axis-Admin: 1` → admin role
- `X-Axis-User: alice` → read_only role
- `X-Axis-Agent: codex` → read_only role (fallback)

Bearer tokens take precedence over headers and cookies.

### Resolution Order

1. Bearer token
2. Session cookie
3. Trusted headers (if enabled)

## API Key Management

### Creation

Keys are created via `POST /api/api-keys` (admin only) or the board UI. The raw key is returned once and never stored — only the SHA-256 hash and 16-char prefix are persisted.

### Revocation

`POST /api/api-keys/{id}/revoke` sets `revoked_at`. Revoked keys immediately stop working. The record is preserved for audit.

### Pre-Role Keys

API keys created before the role system was introduced are assigned `read_only` during schema migration.

## Warning: Admin Keys

Admin keys grant full access including key management. Treat them as secrets:
- Never commit admin keys to version control
- Never share admin keys with implementation agents
- Use the minimum role needed for each agent
- Rotate admin keys periodically

## See Also

- [REST API](REST-API.md) — endpoint reference
- [Architecture](../Architecture.md) — system design
- [Operations](../Operations.md) — deployment configuration
