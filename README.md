<div align="center">

  <img src="assets/logo.svg" alt="Flow" width="120"/>

  # Flow

  **Agent-first Kanban board — self-contained, single-binary, SQLite-backed**

  [![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

</div>

---

<div align="center">
  <img src="assets/board.png" alt="Board view" width="800"/>
</div>

<div align="center">
  <img src="assets/ideas.png" alt="Ideas overlay" width="380"/>
  &nbsp;
  <img src="assets/settings.png" alt="Settings — API keys" width="380"/>
</div>

## Why Flow?

Task boards built for humans assume a person is always in the loop. Flow is different — it treats **agents as first-class users**. Role-scoped API keys, an MCP endpoint, and human-required flags mean LLM agents can pick up work, move tasks, and signal blockers without stepping on each other or silently drifting past decisions that need a human.

Under the hood it's a single FastAPI app backed by SQLite. No database server, no Docker dependency for local dev, no external auth service. Deploy it as a binary, a container, or just `uvicorn flow_app.main:app`.

## Features

- **Kanban board** — five columns (`backlog` → `todo` → `doing` → `review` → `done`) with enforced role transitions
- **Role-scoped API keys** — admin, architect, implementer, reviewer, read_only
- **Dependency graph** — hover any card to see parent/child lines across columns
- **Human-required flag** — mark tasks that need a human decision, with blocker reason
- **Qualification fields** — complexity, impact, effort, risk on every task
- **Ideas intake** — capture ideas and promote them into linked tasks
- **MCP interface** — JSON-RPC 2.0 endpoint for LLM agents at `POST /mcp`
- **Markdown import** — preview and commit tasks from Markdown files
- **Two built-in themes** — Neutral (default) and Axis Love
- **Single-binary deployment** — FastAPI + SQLite, zero external dependencies

## Quick Start

```bash
# Install
pip install .

# Run
uvicorn flow_app.main:app --host 0.0.0.0 --port 8100
```

Open `http://localhost:8100` — the board UI is ready.

### Docker

```bash
# Web service only
docker compose up -d

# Web service + automation runner
docker compose --profile runner up -d
```

### First API Key

1. Open `http://localhost:8100`
2. Click **API keys** in the board UI
3. Create a key (start with `admin` for setup)
4. Copy it — shown only once

```bash
curl -H "Authorization: Bearer YOUR_KEY" \
     -H "Content-Type: application/json" \
     http://localhost:8100/api/tasks
```

## Documentation

Full docs are in [Documentation/](Documentation/README.md).

| Document | Description |
|----------|-------------|
| [Architecture](Documentation/Architecture.md) | System design, data model, lifecycle |
| [Operations](Documentation/Operations.md) | Setup, deployment, backup, runner |
| [REST API](Documentation/Modules/REST-API.md) | Complete REST API reference |
| [MCP](Documentation/Modules/MCP.md) | MCP interface for LLM agents |
| [Security](Documentation/Modules/Security.md) | Roles, permissions, API key management |
| [Web UI](Documentation/Modules/Web-UI.md) | Board, themes, overlays |

## Architecture

```
┌─────────────────────────────────────────┐
│  FastAPI Application (flow_app.main)    │
│  ┌───────────┬───────────┬───────────┐ │
│  │ REST API  │  MCP API  │  HTML UI  │ │
│  └─────┬─────┴─────┬─────┴─────┬─────┘ │
│        │           │           │        │
│  ┌─────┴───────────┴───────────┴─────┐  │
│  │         Repository Layer          │  │
│  └─────────────────┬─────────────────┘ │
│                    │                    │
│  ┌─────────────────┴─────────────────┐  │
│  │      SQLAlchemy + SQLite          │  │
│  └───────────────────────────────────┘  │
└─────────────────────────────────────────┘
```

Single process. SQLite file on disk. No external database, no auth service, no broker. One binary, one container, one `uvicorn` command.

## Contributing

Contributions welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

**Good first issues**: Search for [`good first issue`](../../issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22) or [`help wanted`](../../issues?q=is%3Aissue+is%3Aopen+label%3A%22help+wanted%22).

## License

[MIT](LICENSE) © Nyx Prime