# Flow Roles and API Keys

Flow uses role-scoped API keys to control access. Every key has one of five roles, each granting a specific set of permissions.

## Roles

| Role | Purpose |
|------|---------|
| `admin` | Full access — all permissions including key management |
| `architect` | Planning and task management — create, edit, move, and complete tasks, but cannot manage keys |
| `implementer` | Doing the work — claim tasks, move them through doing/review, and add notes |
| `reviewer` | Reviewing work — read tasks, move from review to done or back to todo/doing |
| `read_only` | Observing — read tasks and view the board; cannot claim, create, edit, or mutate any state |

## Permission Matrix

| Permission | admin | architect | implementer | reviewer | read_only |
|------------|-------|-----------|-------------|----------|-----------|
| `tasks:read` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `tasks:create` | ✅ | ✅ | ❌ | ❌ | ❌ |
| `tasks:edit` | ✅ | ✅ | ❌ | ❌ | ❌ |
| `tasks:claim` | ✅ | ✅ | ✅ | ✅ | ❌ |
| `tasks:move` | ✅ | ✅ | ✅ | ✅ | ❌ |
| `tasks:note` | ✅ | ✅ | ⚠️ | ⚠️ | ❌ |
| `tasks:done` | ✅ | ✅ | ❌ | ✅ | ❌ |
| `tasks:set_human_required` | ✅ | ✅ | ⚠️ | ⚠️ | ❌ |
| `key:manage` | ✅ | ❌ | ❌ | ❌ | ❌ |
| `board:view` | ✅ | ✅ | ✅ | ✅ | ✅ |

⚠️ = restricted access, see Note Permissions and Human-Required Permissions below.

## Task Status Transitions by Role

| From → To | admin | architect | implementer | reviewer | read_only |
|-----------|-------|-----------|-------------|----------|-----------|
| `backlog` → `todo` | ✅ | ✅ | ❌ | ❌ | ❌ |
| `backlog` → `doing` | ✅ | ✅ | ❌ | ❌ | ❌ |
| `todo` → `doing` | ✅ | ✅ | ✅ | ✅ | ❌ |
| `doing` → `review` | ✅ | ✅ | ✅ | ❌ | ❌ |
| `review` → `doing` | ✅ | ✅ | ✅ | ✅ | ❌ |
| `review` → `todo` | ✅ | ✅ | ❌ | ✅ | ❌ |
| `review` → `done` | ✅ | ✅ | ❌ | ✅ | ❌ |
| `doing` → `done` | ✅ | ✅ | ❌ | ❌ | ❌ |
| Any → `backlog` | ✅ | ✅ | ❌ | ❌ | ❌ |
| `done` → anything | ❌ | ❌ | ❌ | ❌ | ❌ |

**Key rules:**
- `done` is terminal — no role can move a task out of `done`
- `backlog` is protected — only admin and architect can move tasks to/from backlog
- Implementers work within `todo` → `doing` → `review`
- Reviewers control `review` → `done` and can send tasks back

## Note Permissions

Adding task notes is scoped by role:

| Role | Can note | Condition |
|------|----------|-----------|
| `admin` | Any task | - |
| `architect` | Any task | - |
| `implementer` | Own tasks only | `task.assignee == actor.name` |
| `reviewer` | Review tasks only | `task.status == "review"` |
| `read_only` | None | - |

## Human-Required Field Permissions

The `human_required`, `assignee_type`, and `blocker_reason` fields have special access rules:

| Role | Can set `human_required=true` | Can clear `human_required` |
|------|------------------------------|---------------------------|
| `admin` | Always | Always |
| `architect` | Always | Always |
| `implementer` | Only on tasks they are assigned to, and only to set `true` | Never |
| `reviewer` | Only on tasks in `review` status, and only to set `true` | Never |
| `read_only` | Never | Never |

When `human_required` is set to `false`, `blocker_reason` is automatically cleared. Only `admin` and `architect` roles can clear `human_required`, which also clears `blocker_reason`.

## Recommended Key Creation Patterns

### Implementer Pool

Create one key per agent that does implementation work:

```bash
curl -X POST http://localhost:8100/api/api-keys \
  -H "Authorization: Bearer <admin-key>" \
  -H "Content-Type: application/json" \
  -d '{"name": "codex", "role": "implementer", "description": "Codex feature agent"}'
```

### Reviewer Key

For agents or humans that review and approve work:

```bash
curl -X POST http://localhost:8100/api/api-keys \
  -H "Authorization: Bearer <admin-key>" \
  -H "Content-Type: application/json" \
  -d '{"name": "review-bot", "role": "reviewer", "description": "Automated review agent"}'
```

### Architect Key for Planning

For agents that create and organize tasks:

```bash
curl -X POST http://localhost:8100/api/api-keys \
  -H "Authorization: Bearer <admin-key>" \
  -H "Content-Type: application/json" \
  -d '{"name": "hermes", "role": "architect", "description": "Hermes planning agent"}'
```

### Read-Only for Observers

For agents that only need to read the board:

```bash
curl -X POST http://localhost:8100/api/api-keys \
  -H "Authorization: Bearer <admin-key>" \
  -H "Content-Type: application/json" \
  -d '{"name": "status-bot", "role": "read_only", "description": "Status reporting bot"}'
```

## Warning: Admin Keys

Admin keys grant full access including the ability to create and revoke other keys. Treat admin keys as secrets:

- Never commit admin keys to version control
- Never share admin keys with implementation agents
- Use the minimum role needed for each agent
- Rotate admin keys periodically

## Trusted Header Authentication

By default, Flow ignores `X-Axis-Admin`, `X-Axis-User`, and `X-Axis-Agent` headers.
This is safe for direct deployments where the server is exposed to clients.

To enable trusted header auth, set:

```bash
FLOW_TRUSTED_HEADERS=true
```

This is required when deploying behind a reverse proxy like Caddy or nginx that
strips inbound identity headers and sets its own.

Warning: Only enable trusted headers if your reverse proxy strips all inbound
`X-Axis-*` headers before setting its own. Otherwise, any client can spoof admin
access by sending `X-Axis-Admin: 1`.

## Creating Keys via REST API

```bash
curl -X POST http://localhost:8100/api/api-keys \
  -H "Authorization: Bearer <admin-key>" \
  -H "Content-Type: application/json" \
  -d '{"name": "my-agent", "role": "implementer", "description": "My agent"}'
```

Response includes the full `api_key` value (shown only once):

```json
{
  "id": "key_000001",
  "name": "my-agent",
  "role": "implementer",
  "key_prefix": "flow_sk_abc...",
  "api_key": "flow_sk_full_key_here...",
  "created_at": "2025-01-15T10:00:00",
  "revoked_at": null
}
```

## Creating Keys via Board UI

1. Open the Flow board at `http://localhost:8100`
2. The **API keys** section is visible only to admin-role actors
3. Fill in the name, description, and select a role from the dropdown
4. Click **Create key**
5. Copy the key immediately — it will not be shown again

## Listing and Revoking Keys

List all keys (admin only):

```bash
curl http://localhost:8100/api/api-keys \
  -H "Authorization: Bearer <admin-key>"
```

Revoke a key (admin only):

```bash
curl -X POST http://localhost:8100/api/api-keys/key_000001/revoke \
  -H "Authorization: Bearer <admin-key>"
```

Revoked keys immediately stop working. The key record is preserved with a `revoked_at` timestamp for audit purposes.

## Unscoped Keys After Migration

If you have API keys that were created before the role system was introduced, they will have been assigned the default role `read_only` during schema migration. These keys can only read tasks and view the board — they cannot claim, create, edit, move, or mutate any state.

To grant them additional permissions, you must create new keys with the desired role and revoke the old ones.
