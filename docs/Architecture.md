# Architecture

## Module Responsibilities

```
┌──────────────────────────────────────────────────────────────┐
│                    FastAPI Application                        │
│                     (flow_app.main)                          │
│                                                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌─────────────┐  │
│  │ REST API │  │ MCP API  │  │ HTML UI  │  │  /healthz   │  │
│  │ /api/*   │  │ POST /mcp│  │ GET /    │  │  GET        │  │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └─────────────┘  │
│       │             │             │                           │
│  ┌────┴─────────────┴─────────────┴──────┐                   │
│  │         Security Layer                │                   │
│  │  resolve_actor() → Actor              │                   │
│  │  require_permission() → dependency    │                   │
│  └────────────────┬──────────────────────┘                   │
│                   │                                          │
│  ┌────────────────┴──────────────────────┐                   │
│  │          Service Layer                │                   │
│  │  TaskService, AgentService, etc.      │                   │
│  └────────────────┬──────────────────────┘                   │
│                   │                                          │
│  ┌────────────────┴──────────────────────┐                   │
│  │        Repository Layer               │                   │
│  │  repository.py — CRUD, serialization  │                   │
│  └────────────────┬──────────────────────┘                   │
│                   │                                          │
│  ┌────────────────┴──────────────────────┐                   │
│  │    SQLAlchemy ORM + SQLite (WAL)      │                   │
│  └───────────────────────────────────────┘                   │
└──────────────────────────────────────────────────────────────┘

        ┌──────────────────────────────────────┐
        │         Automation Runner             │
        │       (flow_app.runner)               │
        │  dispatch + cron + stale + webhooks   │
        └──────────────────────────────────────┘
```

### Request Flow

1. HTTP request arrives at FastAPI router.
2. `require_permission(Permission.X)` dependency calls `resolve_actor()`.
3. `resolve_actor()` checks bearer token → session cookie → trusted headers (in order).
4. If no valid actor, returns 401. If actor lacks permission, returns 403.
5. Route handler calls service layer or repository directly.
6. Repository performs SQLAlchemy queries against the session.
7. Response is serialized through Pydantic response models.

### MCP Flow

1. JSON-RPC 2.0 request arrives at `POST /mcp`.
2. `resolve_actor()` authenticates the request.
3. `handle_mcp_message()` dispatches to the appropriate tool handler.
4. Tool handlers call the same repository/service layer as REST routes.
5. Response is wrapped in JSON-RPC result envelope.

## Data Model

### Tables

| Table | Primary Key | Purpose |
|-------|------------|---------|
| `flow_counters` | `name` (string) | Auto-increment ID generation |
| `projects` | `slug` | Project metadata |
| `tasks` | `id` (`flow_NNNNNN`) | Kanban tasks |
| `task_notes` | `id` (auto) | Task notes/comments |
| `task_handoffs` | `id` (`handoff_NNNNNN`) | Structured inter-agent transitions |
| `task_links` | `id` (`link_NNNNNN`) | Task relationships (blocks, depends_on, related) |
| `ideas` | `id` (`idea_NNNNNN`) | Idea intake |
| `api_keys` | `id` (`key_NNNNNN`) | Role-scoped API keys |
| `agents` | `id` (`agent_NNNNNN`) | Agent registry |
| `agent_runs` | `id` (`run_NNNNNN`) | Agent execution tracking |
| `automation_rules` | `id` (`rule_NNNNNN`) | Event-driven automation rules |
| `workspace_configs` | `id` (`ws_NNNNNN`) | Workspace isolation configs |
| `webhook_configs` | `id` (`webhook_NNNNNN`) | Webhook target configs |
| `webhook_deliveries` | `id` (`delivery_NNNNNN`) | Webhook delivery records |
| `notification_deliveries` | `id` (`nd_NNNNNN`) | Notification delivery records |

### Entity Relationships

```
projects 1───N tasks
tasks    1───N task_notes
tasks    1───N task_handoffs
tasks    N───N task_links      (parent_id ↔ child_id)
tasks    N───N agent_runs      (via task_id)
ideas    1───N tasks           (via import_batch_id = idea.id)
agents   1───N agent_runs
webhook_configs 1───N webhook_deliveries
```

### ID Generation

All IDs are generated through `flow_counters` using SQLite `INSERT ... ON CONFLICT DO UPDATE` for atomic increment. Format: `{prefix}_{value:06d}`.

| Entity | Prefix | Example |
|--------|--------|---------|
| Task | `flow` | `flow_000001` |
| Idea | `idea` | `idea_000001` |
| API Key | `key` | `key_000001` |
| Agent | `agent` | `agent_000001` |
| Agent Run | `run` | `run_000001` |
| Automation Rule | `rule` | `rule_000001` |
| Task Link | `link` | `link_000001` |
| Workspace Config | `ws` | `ws_000001` |
| Webhook Config | `webhook` | `webhook_000001` |
| Webhook Delivery | `delivery` | `delivery_000001` |
| Notification Delivery | `nd` | `nd_000001` |
| Handoff | `handoff` | `handoff_000001` |

## Lifecycle

### Task Lifecycle

```
  backlog ──→ todo ──→ doing ──→ review ──→ done
               ↑         │         │
               └─────────┘         │
               ↑                   │
               └───────────────────┘
```

- `backlog` → `todo`/`doing`: admin, architect only
- `todo` → `doing`: admin, architect, implementer, reviewer
- `doing` → `review`: admin, architect, implementer
- `review` → `done`: admin, architect, reviewer
- `review` → `todo`/`doing`: admin, architect, implementer, reviewer
- `done` is terminal — no role can move a task out

See [Security](Modules/Security.md) for the full transition matrix.

### Agent Run Lifecycle

```
  pending ──→ running ──→ done
                 │
                 ├──→ crashed   (non-zero exit)
                 │
                 └──→ stale     (heartbeat timeout exceeded)
```

1. Dispatcher creates run as `running`, spawns subprocess.
2. Background monitor thread polls process exit.
3. Agent optionally sends heartbeats via `POST /api/agent-runs/{id}/heartbeat`.
4. On process exit, monitor auto-completes the run.
5. Stale recovery requeues runs with no heartbeat beyond `stale_claim_timeout_seconds`.

### Webhook Delivery Lifecycle

```
  pending ──→ success   (2xx response)
      │
      ├──→ retrying ──→ success
      │        │
      │        └──→ failed   (max_retries exhausted)
      │
      └──→ failed   (non-retryable error)
```

Retry backoff: `retry_backoff_seconds * 2^(attempts - 1)`.

## Integration Points

| Integration | Direction | Mechanism |
|------------|-----------|-----------|
| REST API | Inbound | FastAPI HTTP routes at `/api/*` |
| MCP | Inbound | JSON-RPC 2.0 at `POST /mcp` |
| HTML Board UI | Inbound | Jinja2 templates at `GET /` |
| Webhooks | Outbound | HTTP POST to configured URLs |
| Telegram | Outbound | Bot API via `FLOW_TELEGRAM_BOT_TOKEN` |
| Discord | Outbound | Webhook URL via `FLOW_DISCORD_WEBHOOK_URL` |
| Agent Subprocesses | Outbound | `subprocess.Popen` with env injection |
| Automation Rules | Internal | Event evaluation in `rules_engine.py` |

## Agent Roles and Capabilities

See [Agent Roles and Capability Profiles](Modules/AgentRoles.md) for recommended role assignments, capability tags, and `dispatch_statuses` for common agent families.

## Important Invariants

- **`done` is terminal.** No role can move a task out of `done`.
- **`backlog` is protected.** Only admin and architect can move tasks to/from backlog.
- **API key shown once.** The raw key is returned only on creation; only the SHA-256 hash and prefix are stored.
- **Optimistic locking on tasks.** `cas_update_task()` uses `version` column to prevent lost updates.
- **Blocking link cycles rejected.** `has_cycle()` traverses blocking links before inserting.
- **Auto-promotion.** When a task moves to `done`, blocked children in `backlog` with all parents done are moved to `todo`.
- **Dispatch readiness.** A task is dispatchable only if unclaimed, not human_required, in an allowed status, and all blocking parents are `done`.
- **Schema migrations are one-way.** New columns are added but never removed. Old versions ignore unknown columns.
- **SSRF protection.** Webhook URLs must resolve to global unicast IPs; delivery pins the resolved IP to prevent DNS rebinding.
- **Webhook secrets encrypted at rest** when `FLOW_WEBHOOK_ENCRYPTION_KEY` is set (Fernet/AES-128-CBC + HMAC-SHA256).

## Performance and Safety Constraints

- **SQLite single-writer.** WAL mode allows concurrent readers but only one writer at a time. Use PostgreSQL for multi-writer deployments.
- **Busy timeout.** Default 5000ms (`FLOW_SQLITE_BUSY_TIMEOUT_MS`) helps brief lock contention succeed.
- **Page limits.** Default 100, max 500 (`DEFAULT_PAGE_LIMIT`, `MAX_PAGE_LIMIT` in `config.py`).
- **Webhook payload cap.** `FLOW_MAX_WEBHOOK_PAYLOAD_BYTES` (default 65536) and `FLOW_MAX_WEBHOOK_RESPONSE_BYTES` (default 4096).
- **Agent concurrency.** Each agent has `max_concurrency` (default 1) limiting simultaneous subprocess runs.
- **Heartbeat timeout.** Agent runs become stale after `stale_claim_timeout_seconds` (default 600) without heartbeat.
- **Command allowlist.** Agent `command_allowlist` restricts which commands can be dispatched.

## Diagnostics

| Endpoint | Purpose |
|----------|---------|
| `GET /healthz` | Returns `{"ok": true, "database": true}` or 503 |
| `GET /healthz/config` | Returns trusted_headers, session_auth_enabled, session_cookie_secure |

## Storage Format Notes

Several columns use legacy text formats rather than native types:

| Field | Table | Format | Access Helper |
|-------|-------|--------|---------------|
| `capabilities` | agents | comma-separated TEXT | `get_agent_capabilities()` |
| `dispatch_statuses` | agents | comma-separated TEXT | `get_agent_dispatch_statuses()` |
| `events` | webhook_configs | comma-separated TEXT | `get_webhook_events()` |
| `changed_files`, `commands_run`, etc. | task_handoffs | JSON-encoded TEXT | `get_handoff_json_field()` |
| `human_required`, `active`, `secret_encrypted` | various | INTEGER (0/1) | `get_bool_field()` |

All access goes through typed helpers in `flow_app/storage_helpers.py`. Direct attribute access should use these helpers.

## See Also

- [Operations](Operations.md) — deployment and configuration
- [Security](Modules/Security.md) — roles and permissions
- [REST API](Modules/REST-API.md) — endpoint reference
