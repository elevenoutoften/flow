# Tasks

## Source

| File | Role |
|------|------|
| `flow_app/models.py:60-106` | `Task`, `TaskNote` ORM models |
| `flow_app/schemas.py` | `TaskCreate`, `TaskUpdate`, `TaskResponse` Pydantic schemas |
| `flow_app/repository.py:1186-1322` | Task CRUD, listing, next-task, CAS updates |
| `flow_app/services/task.py` | `TaskService` — claim, release, move, done, note logic |
| `flow_app/routes/tasks.py` | FastAPI router |

## Board Columns

| Column | Purpose |
|--------|---------|
| `backlog` | Unprioritized work — agents cannot claim from here |
| `todo` | Ready for work — agents can claim and move to `doing` |
| `doing` | Actively being worked on |
| `review` | Waiting for review |
| `done` | Completed (terminal) |

## Task Model

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `id` | string | auto (`flow_NNNNNN`) | Primary key |
| `title` | string(240) | *(required)* | Task title |
| `status` | string(24) | `backlog` | Board column |
| `priority` | int | `50` | Priority (0–1000, higher = more important) |
| `project` | string(120) | `default` | Project slug |
| `assignee` | string/null | null | Claimant name |
| `claimer_key_id` | string/null | null | API key ID that claimed |
| `version` | int | `1` | Optimistic lock version |
| `human_required` | bool | `false` | Needs human attention |
| `assignee_type` | enum | `agent` | `agent`, `human`, `mixed` |
| `blocker_reason` | text | `""` | Why human is needed |
| `complexity` | enum | `small` | `trivial`, `small`, `medium`, `large`, `epic` |
| `impact` | enum | `medium` | `low`, `medium`, `high`, `critical` |
| `effort` | enum | `medium` | `low`, `medium`, `high` |
| `risk` | enum | `low` | `low`, `medium`, `high` |
| `description` | text | `""` | Task description |
| `acceptance_criteria` | text | `""` | Acceptance criteria |
| `source_filename` | string/null | null | Markdown import source |
| `source_line` | int/null | null | Line number in source |
| `import_batch_id` | string/null | null | Import batch or idea ID |
| `source_title` | string/null | null | Source section title |
| `created_at` | datetime | auto | Creation timestamp |
| `updated_at` | datetime | auto | Last update timestamp |

## Qualification Fields

Every task carries four qualification fields for sizing and prioritization: `complexity`, `impact`, `effort`, `risk`. These are informational and do not affect workflow transitions.

## Task Notes

Notes are child records of tasks (`task_notes` table). Each note has `body`, `author`, `author_key_id`, and `created_at`. Notes are appended, never edited.

Note permissions are role-scoped — see [Security](Security.md).

## Lifecycle

### Claiming

1. `POST /api/tasks/{id}/claim` with optional `agent_name`.
2. If already claimed by a different agent → 409.
3. Task moves from `backlog`/`todo` to `doing` (if role allows).
4. `assignee` and `claimer_key_id` are set.

### Releasing

1. `POST /api/tasks/{id}/release`.
2. `assignee` and `claimer_key_id` are cleared.
3. If in `doing`, task moves back to `todo`.

### Moving

1. `POST /api/tasks/{id}/move` with `{"status": "review"}`.
2. Transition is validated against the role's allowed transitions.
3. If invalid → 403.

### Marking Done

1. `POST /api/tasks/{id}/done` with optional `summary` and `author`.
2. Summary is appended as a note.
3. Task moves to `done`.
4. Auto-promotion checks: blocked children in `backlog` with all parents done are moved to `todo`.

## Concurrency

Tasks use optimistic locking via the `version` column. `cas_update_task()` atomically updates only if `version` matches the expected value, preventing lost updates from concurrent modifications.

## Next Task Selection

`GET /api/tasks/next` and the MCP `flow_next_task` tool return the highest-priority unclaimed task from `todo` (then `backlog`) that is:
- Not claimed (`assignee IS NULL`)
- Not `human_required`
- Dispatch-ready (no unresolved blocking dependencies)

## Markdown Import

Preview: `POST /api/import/markdown/preview` parses Markdown checkboxes into task previews. Checked items (`[x]`) are skipped. Duplicates are detected by title and source location.

Commit: `POST /api/import/markdown/commit` creates tasks from the preview items.

## See Also

- [Security](Security.md) — transition matrix and permissions
- [Task Links](TaskLinks.md) — dependencies and auto-promotion
- [Handoff](Handoff.md) — structured inter-agent transitions
- [REST API](REST-API.md) — endpoint reference
