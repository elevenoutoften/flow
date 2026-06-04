# Flow — Documentation

**Status:** 2026-06-04
**Scope:** `flow_app/`, `tests/`, `scripts/`, `docker-compose.yml`, `Dockerfile`

Flow is an agent-first Kanban task board service. It is a single FastAPI application backed by SQLite, deployed as a standalone process or Docker container. Flow gives LLM agents a reliable source of truth for work items, enforces role-scoped API keys, tracks human-required blockers, and provides an MCP interface for programmatic access.

> **New here?** Start with [AGENTS.md](../AGENTS.md) (repo root) for the one-command setup and how to
> connect an agent. A running server also self-describes at `GET /llms.txt`.

## Recommended Reading Order

1. This page — orientation and module map
2. [Agent Quickstart](AGENT-QUICKSTART.md) — connect an agent and run the core work loop
3. [Architecture](Architecture.md) — system design, data model, lifecycle, invariants
4. [Operations](Operations.md) — setup, deployment, backup, runner, upgrade
5. Module pages below — deep dives into each subsystem

## High-Level Module Map

| Module | Responsibility | Entry Point |
|--------|---------------|-------------|
| REST API | HTTP CRUD for tasks, projects, ideas, agents, webhooks, rules, links, workspaces | `flow_app/routes/` |
| MCP Interface | JSON-RPC 2.0 endpoint for LLM agents | `flow_app/mcp/dispatch.py` |
| Security | Roles, permissions, bearer tokens, session cookies, trusted headers | `flow_app/security.py` |
| Repository | Database access layer — CRUD, serialization, ID generation | `flow_app/repository.py` |
| Models | SQLAlchemy ORM models for all tables | `flow_app/models.py` |
| Schemas | Pydantic request/response validation | `flow_app/schemas.py` |
| Dispatcher | Agent subprocess spawning, monitoring, stale recovery | `flow_app/dispatcher.py` |
| Runner | Unified automation loop — dispatch, cron, stale recovery, webhook delivery | `flow_app/runner.py` |
| Rules Engine | Condition evaluation and action execution for automation rules | `flow_app/rules_engine.py` |
| Webhooks | Outbound HTTP event delivery with retry, SSRF protection, secret encryption | `flow_app/webhooks.py`, `flow_app/ssrf.py` |
| Workspace | Isolated work directories for agent tasks (git worktree, shared, scratch) | `flow_app/workspace.py` |
| Notifications | Provider interface for Telegram, Discord, webhook notifications | `flow_app/notifications.py` |
| Migration | One-way schema migration on startup | `flow_app/migration.py` |
| Bootstrap | Fresh-install seeding of projects, keys, agents, rules | `flow_app/bootstrap.py` |
| HTML UI | Browser-based Kanban board | `flow_app/routes/ui.py`, `flow_app/templates/`, `flow_app/static/` |

## Module Index

### Getting Started

| Page | Description |
|------|-------------|
| [Agent Quickstart](AGENT-QUICKSTART.md) | Install, bootstrap, connect any LLM agent, and run the 5-step work loop |
| [Flow Hermes Skill](FLOW-HERMES-SKILL.md) | Drop-in skill file for Hermes agents (REST + MCP recipes) |

### Core

| Page | Description |
|------|-------------|
| [Architecture](Architecture.md) | System design, data model, lifecycle, integration points, invariants |
| [Operations](Operations.md) | Install, configure, deploy, backup, restore, runner, systemd, Docker |

### API and Interfaces

| Page | Description |
|------|-------------|
| [REST API](Modules/REST-API.md) | Complete REST endpoint reference |
| [MCP Interface](Modules/MCP.md) | JSON-RPC 2.0 tools for LLM agents |
| [Security](Modules/Security.md) | Roles, permissions, authentication, API key management |

### Subsystems

| Page | Description |
|------|-------------|
| [Tasks](Modules/Tasks.md) | Task model, board columns, status transitions, qualification fields |
| [Dispatcher](Modules/Dispatcher.md) | Agent registry, subprocess dispatch, heartbeats, stale recovery |
| [Automation Rules](Modules/AutomationRules.md) | Event-driven rules engine with conditions and actions |
| [Webhooks](Modules/Webhooks.md) | Outbound HTTP delivery, SSRF protection, secret encryption, retry |
| [Handoff](Modules/Handoff.md) | Structured inter-agent transition protocol |
| [Task Links](Modules/TaskLinks.md) | Task dependencies, cycle prevention, auto-promotion |
| [Workspace](Modules/Workspace.md) | Isolated work directories for parallel agent runs |
| [Web UI](Modules/Web-UI.md) | Browser board, ideas/settings overlays, theme, and engine-vs-UI capability gaps |

## Where Things Live

| Path | Purpose |
|------|---------|
| `flow_app/main.py` | FastAPI app factory, lifespan, router mounting, `/healthz`, `/mcp` |
| `flow_app/config.py` | Environment variable parsing, `FlowSettings` dataclass |
| `flow_app/database.py` | SQLAlchemy engine/session factory, SQLite PRAGMA setup |
| `flow_app/models.py` | All SQLAlchemy ORM models (14 tables) |
| `flow_app/schemas.py` | All Pydantic request/response schemas |
| `flow_app/repository.py` | Database CRUD, serialization, ID generation |
| `flow_app/security.py` | `Actor`, `Permission`, role matrix, auth resolution |
| `flow_app/routes/` | FastAPI routers: tasks, projects, ideas, agents, automation, webhooks, workspace, UI |
| `flow_app/services/` | Service layer: task, agent, automation, idea, webhook, workspace |
| `flow_app/mcp/dispatch.py` | MCP tool definitions and JSON-RPC dispatch |
| `flow_app/dispatcher.py` | Agent subprocess lifecycle management |
| `flow_app/runner.py` | Automation runner CLI and loop |
| `flow_app/rules_engine.py` | Automation condition evaluation and action execution |
| `flow_app/workspace.py` | Workspace provisioning and cleanup |
| `flow_app/webhooks.py` | Webhook event emission and delivery |
| `flow_app/ssrf.py` | Webhook URL validation and IP pinning |
| `flow_app/notifications.py` | Notification provider interface and registry |
| `flow_app/migration.py` | Schema migration (`ensure_compatible_schema`) |
| `flow_app/bootstrap.py` | Fresh-install bootstrap CLI |
| `flow_app/templates/` | Jinja2 HTML templates for board UI |
| `flow_app/static/` | CSS and JS for board UI |
| `tests/` | pytest test suite |
| `scripts/` | Utility scripts (e.g., `scan_secrets.py`) |
| `docker-compose.yml` | Docker Compose for web + runner |
| `Dockerfile` | Single-stage Python 3.12 container |

## Conventions

- **IDs** are generated from `flow_counters` table: `{prefix}_{NNNNNN}` (e.g., `flow_000001`, `key_000002`).
- **Timestamps** are UTC with timezone info (`datetime.now(timezone.utc)`).
- **Boolean columns** in SQLite are stored as `INTEGER` (0/1) and normalized through `storage_helpers.py`.
- **List columns** (capabilities, events, etc.) are stored as comma-separated `TEXT` and parsed through `storage_helpers.py`.
- **JSON list columns** (handoff fields) are stored as JSON-encoded `TEXT`.
- **API keys** are hashed with SHA-256 before storage; only the prefix and hash are persisted.
- **Schema migrations** are one-way (`ALTER TABLE ADD COLUMN`), run on startup in `ensure_compatible_schema()`.
- **All route handlers** use `require_permission()` dependency injection for auth.
- **Task concurrency** uses optimistic locking via `version` column and `cas_update_task()`.
- **Package version** (`FLOW_VERSION`) is a content hash of static files, not a semver string.

## Documentation Conventions

All project documentation lives in this `docs/` folder — there is no second docs tree. House style:

- **Top-level guides** use Title-case filenames (`Architecture.md`, `Operations.md`). The agent
  onboarding guides keep their established `AGENT-QUICKSTART.md` / `FLOW-HERMES-SKILL.md` names.
- **Module deep-dives** use PascalCase filenames under `Modules/` (`MCP.md`, `REST-API.md`).
- **Links between docs** are relative (`Modules/MCP.md`, `../AGENTS.md`) so the tree is portable.
- The repo-root [AGENTS.md](../AGENTS.md) is the single entry point; it points here, not vice-versa.
