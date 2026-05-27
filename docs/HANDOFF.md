# Flow Handoff

Handoff is Flow's structured inter-agent transition protocol. It records what changed, what was tested, what did not work, and what should happen next when a task moves between agents or needs follow-up.

Unlike simple task completion, a handoff is not just a status change or a note. Completion says the task is done. A handoff preserves operational context for the next agent, including partial outcomes, failed attempts, test evidence, artifacts, and recommended next ownership.

## REST API

### Create Handoff

```http
POST /api/tasks/{task_id}/handoffs
```

Compatibility route:

```http
POST /api/tasks/{task_id}/handoff
```

Permission: `handoff:create`

Roles with create permission: `admin`, `architect`, `implementer`, `reviewer`.

Request body uses `HandoffRequest`:

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

`summary` is required. `outcome` must be `success`, `partial`, or `failed` and defaults to `success`.

### List Handoffs

```http
GET /api/tasks/{task_id}/handoffs
```

Permission: task read access. Results are newest first.

## MCP Tool

Agents can create the same structured record through the MCP tool:

```text
flow_task_handoff
```

The tool accepts the same handoff fields plus `task_id`. It requires `handoff:create`, so implementer and reviewer keys can create handoffs without admin privileges.

To read handoffs through MCP, use:

```text
flow_get_task_handoffs
```

This read tool requires `handoff:read`.

## HandoffRequest Fields

| Field | Type | Purpose |
|-------|------|---------|
| `summary` | string | Required concise handoff summary |
| `author` | string or null | Actor name; defaults to the authenticated actor when omitted |
| `changed_files` | list of strings | Files changed or inspected as part of the work |
| `commands_run` | list of strings | Commands executed during the work |
| `tests_run` | list of strings | Test commands or test targets that were run |
| `artifacts` | list of objects | Links or references to logs, screenshots, builds, or other outputs |
| `attempted_but_failed` | list of strings | Approaches or commands that did not work |
| `remaining_work` | string | Follow-up work or unresolved risks |
| `outcome` | string | `success`, `partial`, or `failed` |
| `next_recommended_agent` | string or null | Suggested next role or agent |
| `capabilities` | list of strings | Skills or capabilities useful for the next step |

## Permission Model

`read_only` can read handoff records but cannot create them.

`implementer` and `reviewer` can create handoffs with `handoff:create`. This lets ordinary worker agents preserve context while still preventing them from creating tasks, managing keys, or bypassing normal task transition rules.

## Dispatched Agent Context

When an agent is dispatched to work on a task, the wrapper (e.g. `hermes_wrapper`) injects structured context into the agent's prompt. This context is built by `build_task_context_bundle()` and appended to the task's title, description, and acceptance criteria.

The context bundle is transport-agnostic: any wrapper or agent client can call the same REST endpoints (`GET /api/tasks/{task_id}/dependencies` and `GET /api/tasks/{task_id}/handoffs`) and use the same formatting logic.

### Dependency Context (`## Dependency Context`)

Shown when the task has blocking dependencies:

```
## Dependency Context
Blocked by:
  - flow_000001 (Some parent task) — status: done
Blocking:
  - flow_000003 (Some child task) — status: todo
```

- **Blocked by**: Tasks that this task depends on. Lists up to 10, with a truncation marker if there are more.
- **Blocking**: Tasks that depend on this task. Lists up to 10, with a truncation marker if there are more.
- Each entry shows task ID, title, and current status.
- If no dependencies exist, the entire section is omitted.

### Handoff Context (`## Handoff Context`)

Shown when at least one handoff record exists for the task:

```
## Handoff Context
Latest handoff by hermes:
  Outcome: success
  Summary: Implemented the API route.
  Remaining work: Fix the migration and add remaining API routes.
  Attempted but failed: Database migration failed
  Tests run: tests/test_api.py
  Next recommended agent: reviewer
  Changed files: flow_app/main.py
  (2 earlier handoff(s) not shown)
```

- Shows only the **latest** handoff (newest first from the API).
- Individual field values are capped at 500 characters with a `…[truncated]` marker.
- The entire handoff block is bounded at 4000 total characters with a `[context truncated]` marker.
- Lists up to 5 items for multi-value fields (tests_run, changed_files, attempted_but_failed) before truncating.
- If no handoffs exist, the entire section is omitted.

### Graceful Fallback

If the dependency or handoff API endpoints are unavailable, return empty data, or the task has no records, the corresponding section is omitted from the prompt. The dispatch still proceeds normally — context is additive, never blocking.
