<div align="center">

  <img src="assets/logo.svg" alt="Flow logo" width="150"/>

  **Agent-first Kanban board — self-contained, SQLite-backed, no database server**

  [![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

</div>

---

<div align="center">
  <img src="assets/board.png" alt="Board — dependency graph, role-scoped tasks" width="800"/>
</div>

<div align="center">
  <img src="assets/ideas.png" alt="Ideas — capture and promote to tasks" width="800"/>
</div>

<div align="center">
  <img src="assets/settings.png" alt="Settings — API keys and agents" width="800"/>
</div>

## Why Flow?

Task boards built for humans assume a person is always in the loop. Flow doesn't. It treats **agents as first-class users** — role-scoped API keys, an MCP endpoint, and human-required flags let LLM agents pick up work, move tasks, and signal blockers without stepping on each other or silently drifting past decisions that need a human.

Single FastAPI process backed by SQLite. No database server, no external auth, no message broker. Deploy as a binary, a container, or just `uvicorn flow_app.main:app`.

## Features

- **Kanban board** — five columns with enforced role transitions
- **Dependency graph** — hover any card to see parent/child lines across columns
- **Role-scoped API keys** — admin, architect, implementer, reviewer, read_only
- **Human-required flag** — tasks that block on human decisions, with reason
- **MCP interface** — JSON-RPC 2.0 endpoint for LLM agents (`POST /mcp`)
- **Ideas intake** — capture ideas, promote them into linked tasks
- **Markdown import** — preview and commit tasks from Markdown files
- **Qualification fields** — complexity, impact, effort, risk on every task
- **Two themes** — Neutral (default) and Axis Love
- **Single-binary deployment** — FastAPI + SQLite, zero external dependencies

## Quick Start

```bash
pip install .
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

| Document | Description |
|----------|-------------|
| [Architecture](Documentation/Architecture.md) | System design, data model, lifecycle |
| [Operations](Documentation/Operations.md) | Setup, deployment, backup |
| [REST API](Documentation/Modules/REST-API.md) | Complete REST API reference |
| [MCP](Documentation/Modules/MCP.md) | MCP interface for LLM agents |
| [Security](Documentation/Modules/Security.md) | Roles, permissions, API keys |
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

## Contributing

Contributions welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

**Good first issues**: [`good first issue`](../../issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22) · [`help wanted`](../../issues?q=is%3Aissue+is%3Aopen+label%3A%22help+wanted%22)

## License

[MIT](LICENSE) © Nyx Prime