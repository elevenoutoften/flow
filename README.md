# Flow

Flow is an agent-first Kanban/task board service. It is intentionally self-contained — a single FastAPI application backed by SQLite — so it can be deployed anywhere as a standalone binary or Docker container.

**Flow is a task board**, not a full project management suite. It focuses on: giving agents a reliable source of truth for work items, enforcing role-scoped API keys, tracking human-required blockers, and providing an MCP interface for LLM agents.

## Features

- **Kanban board** with five columns: `backlog` → `todo` → `doing` → `review` → `done`
- **Role-scoped API keys** — admin, architect, implementer, reviewer, read_only
- **Human-required flag** — tasks can be marked as requiring human intervention with a blocker reason
- **Qualification fields** — complexity, impact, effort, risk on every task
- **Ideas intake** — capture ideas and promote them into linked tasks
- **MCP interface** — JSON-RPC 2.0 endpoint for LLM agents at `POST /mcp`
- **Markdown import** — preview and commit tasks from Markdown files
- **HTML board UI** — lightweight browser interface at `/`
- **Health check** — `GET /healthz`

## Quick Start

### Install

```bash
pip install .
```

Or run from source:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e ".[test]"
```

### Configure

Flow reads configuration from environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `FLOW_DATA_DIR` | `./data` | Directory for SQLite database |
| `FLOW_DATABASE_URL` | `sqlite:///{data_dir}/flow.sqlite` | Full database URL |
| `FLOW_DEFAULT_PROJECT` | `default` | Default project slug |
| `FLOW_HOST` | `0.0.0.0` | Bind address |
| `FLOW_PORT` | `8100` | Bind port |
| `FLOW_DEBUG` | `false` | Debug mode |

### Run locally

```bash
uvicorn flow_app.main:app --host 0.0.0.0 --port 8100
```

Open `http://localhost:8100` in your browser.

## Docker Compose Quick Start

A `docker-compose.yml` is included. Build and run:

```bash
docker compose up -d
```

The service will be available at `http://localhost:8100`. Data persists in the `data/` volume.

## Creating Your First API Key

1. Open `http://localhost:8100` in your browser
2. Click **API keys** in the board UI
3. Create a key with the desired role (start with `admin` for setup)
4. Copy the key — it is shown only once

## Making a Request

```bash
curl -H "Authorization: Bearer YOUR_KEY" \
     -H "Content-Type: application/json" \
     http://localhost:8100/api/tasks
```

## Documentation

| Document | Description |
|----------|-------------|
| [docs/API.md](docs/API.md) | Complete REST API reference |
| [docs/MCP.md](docs/MCP.md) | MCP interface for LLM agents |
| [docs/ROLES.md](docs/ROLES.md) | Roles, permissions, and API key management |
| [docs/MIGRATION.md](docs/MIGRATION.md) | Backup, restore, and schema migration |
| [docs/RELEASE-CHECKLIST.md](docs/RELEASE-CHECKLIST.md) | Public release checklist |

## Architecture

```
┌─────────────────────────────────────────┐
│  FastAPI Application (flow_app.main)    │
│  ┌───────────┬───────────┬───────────┐  │
│  │ REST API  │  MCP API  │  HTML UI  │  │
│  └─────┬─────┴─────┬─────┴─────┬─────┘  │
│        │           │           │         │
│  ┌─────┴───────────┴───────────┴─────┐  │
│  │         Repository Layer          │  │
│  └─────────────────┬─────────────────┘  │
│                    │                     │
│  ┌─────────────────┴─────────────────┐  │
│  │      SQLAlchemy + SQLite          │  │
│  └───────────────────────────────────┘  │
└─────────────────────────────────────────┘
```

- **FastAPI** — HTTP routing, validation, and serialization
- **SQLite** — embedded database, zero external dependencies
- **Single-binary deployment** — no separate database server needed
- **Agent-native** — MCP endpoint and role-scoped API keys built in from day one

## Core Board Columns

| Column | Purpose |
|--------|---------|
| `backlog` | Unprioritized work — agents cannot claim from here |
| `todo` | Ready for work — agents can claim and move to `doing` |
| `doing` | Actively being worked on |
| `review` | Waiting for review |
| `done` | Completed |

Valid transitions are enforced by role. See [docs/ROLES.md](docs/ROLES.md) for the full matrix.

## Themes

Flow ships with two built-in themes:

- **Neutral** - the default. Clean dark theme with sky-blue accents.
- **Axis Love** - warm dark theme with pink/magenta accents and monospace typography.

The server default theme is set via the `FLOW_THEME` environment variable (defaults to `neutral`). Users can switch themes at runtime using the theme selector in the topbar - this preference is stored in the browser's `localStorage` and persists across reloads. On a fresh session, the server-configured theme is used as the initial value.

## License

[MIT](LICENSE)