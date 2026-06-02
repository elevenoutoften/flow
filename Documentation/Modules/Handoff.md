# Handoff

## Source

| File | Role |
|------|------|
| `flow_app/models.py:109-131` | `TaskHandoff` model |
| `flow_app/repository.py:1346-1431` | Handoff CRUD, serialization |
| `flow_app/routes/tasks.py` | REST API routes |
| `flow_app/schemas.py` | `HandoffRequest`, `HandoffResponse` |

## Overview

Handoff is Flow's structured inter-agent transition protocol. It records what changed, what was tested, what did not work, and what should happen next when a task moves between agents.

Unlike a simple status change or note, a handoff preserves operational context for the next agent: partial outcomes, failed attempts, test evidence, artifacts, and recommended next ownership.

## Model

| Field | Type | Description |
|-------|------|-------------|
| `id` | string (`handoff_NNNNNN`) | Primary key |
| `task_id` | string | FK to tasks |
| `author` | string | Author name |
| `author_key_id` | string? | API key ID |
| `summary` | text | Required handoff summary |
| `changed_files` | text (JSON list) | Files changed or inspected |
| `commands_run` | text (JSON list) | Commands executed |
| `tests_run` | text (JSON list) | Tests run |
| `artifacts` | text (JSON list) | Links to logs, builds, outputs |
| `attempted_but_failed` | text (JSON list) | Approaches that did not work |
| `remaining_work` | text | Follow-up work or unresolved risks |
| `outcome` | string | `success`, `partial`, or `failed` |
| `next_recommended_agent` | string? | Suggested next role or agent |
| `capabilities` | text (JSON list) | Skills needed for next step |
| `created_at` | datetime | Creation timestamp |

## REST API

### Create Handoff

```http
POST /api/tasks/{task_id}/handoffs
```

Compatibility route: `POST /api/tasks/{task_id}/handoff`

Permission: `handoff:create` (admin, architect, implementer, reviewer)

```json
{
  "summary": "Implemented the API route and left UI polish for the next pass.",
  "author": "codex",
  "changed_files": ["flow_app/main.py"],
  "commands_run": ["python -m pytest tests/test_api.py"],
  "tests_run": ["tests/test_api.py"],
  "artifacts": [{"name": "pytest log", "url": "artifacts/pytest.txt"}],
  "attempted_but_failed": ["Full suite was not run because the database fixture was unavailable."],
  "remaining_work": "Run the full suite and verify the board drawer.",
  "outcome": "partial",
  "next_recommended_agent": "reviewer",
  "capabilities": ["api", "tests"]
}
```

`summary` is required. `outcome` defaults to `success`.

### List Handoffs

```http
GET /api/tasks/{task_id}/handoffs
```

Permission: `handoff:read`. Results are newest first.

## MCP Tools

| Tool | Permission | Description |
|------|-----------|-------------|
| `flow_task_handoff` | `handoff:create` | Create structured handoff |
| `flow_get_task_handoffs` | `handoff:read` | List handoffs for task |

## Dispatched Agent Context

When an agent is dispatched, the wrapper builds a context bundle appended to the task's title, description, and acceptance criteria:

### Dependency Context

Shown when the task has blocking dependencies:

```
## Dependency Context
Blocked by:
  - flow_000001 (Parent task) — status: done
Blocking:
  - flow_000003 (Child task) — status: todo
```

- Lists up to 10 entries each direction, with truncation marker.
- Omitted if no dependencies exist.

### Handoff Context

Shown when at least one handoff exists:

```
## Handoff Context
Latest handoff by hermes:
  Outcome: success
  Summary: Implemented the API route.
  Remaining work: Fix the migration.
  (2 earlier handoff(s) not shown)
```

- Shows only the latest handoff.
- Individual fields capped at 500 characters.
- Entire block bounded at 4000 characters.
- Multi-value fields list up to 5 items before truncating.
- Omitted if no handoffs exist.

### Graceful Fallback

If dependency or handoff APIs are unavailable, the corresponding section is omitted. Dispatch proceeds normally — context is additive, never blocking.

## See Also

- [Tasks](Tasks.md) — task model and lifecycle
- [Dispatcher](Dispatcher.md) — agent dispatch context
- [Task Links](TaskLinks.md) — dependency tracking
- [Security](Security.md) — handoff permissions
