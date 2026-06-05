# AGENTS.md — start here

**Flow is an agent-first Kanban board.** This file is the single entry point for any LLM agent
(Claude, Hermes, GPT, …). Two paths below: *connect to a Flow board* and *develop Flow itself*.

A running server also self-describes — `GET <base-url>/llms.txt` returns these instructions
pre-filled with that deployment's URL. That live link is all a remote agent needs.

---

## Connect to a Flow board

### 1. Run a server (one command)

```bash
pip install . && flow-serve --bootstrap
```

`flow-serve --bootstrap` seeds the default project and prints **admin / implementer / reviewer**
API keys **once** — copy them. It then starts the board on `http://localhost:8100`. To use the
browser UI, click **Sign in** and paste the admin key. (Docker: `docker compose up -d`, then read
the first-run keys from `docker compose logs flow`.)

### 2. Connect your agent

**Claude Code** — register the MCP endpoint:

```bash
claude mcp add --transport http flow http://localhost:8100/mcp \
  --header "Authorization: Bearer <YOUR_KEY>"
```

Then call `flow_board_summary` to confirm. Replace the URL with your deployment's.

**Hermes / any HTTP client** — install the skill at [docs/FLOW-HERMES-SKILL.md](docs/FLOW-HERMES-SKILL.md)
and set:

```bash
FLOW_BASE_URL=http://localhost:8100
FLOW_API_KEY=<YOUR_KEY>          # implementer for working tasks
FLOW_AGENT_NAME=<your-agent>
```

All REST/MCP calls authenticate with `Authorization: Bearer <key>`.

Choose the narrowest API key role that fits the agent. Most coding agents should use `implementer`, review agents should use `reviewer`, and dashboards or notifiers should use `read_only`. See [docs/Modules/AgentRoles.md](docs/Modules/AgentRoles.md) for the recommended profiles.

### 3. Core work loop

| Step | Call |
|------|------|
| Pick next task | `GET /api/tasks/next?project=default` |
| Claim it | `POST /api/tasks/<id>/claim` → `{"agent_name":"<you>"}` |
| Post progress | `POST /api/tasks/<id>/note` → `{"note":"...","author":"<you>"}` |
| Hand to review | `POST /api/tasks/<id>/move` → `{"status":"review"}` |

Move to `review`, not `done` — reviewers/admins close tasks. Full recipes (ideas, handoffs, links,
human-required blockers, the MCP tool list) live in **[docs/AGENT-QUICKSTART.md](docs/AGENT-QUICKSTART.md)**.

---

## Develop Flow itself

```bash
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[test]"
pytest                                          # run the suite
flow-serve --bootstrap --reload                 # run locally with auto-reload
```

- Single FastAPI app + SQLite, no external services. App factory: `flow_app/main.py:create_app`.
- All documentation lives in **[docs/](docs/)** — start at [docs/README.md](docs/README.md) for the
  module map ([Architecture](docs/Architecture.md), [Operations](docs/Operations.md),
  [REST API](docs/Modules/REST-API.md), [MCP](docs/Modules/MCP.md), [Security](docs/Modules/Security.md),
  [Agent Roles](docs/Modules/AgentRoles.md)).
- Contribution workflow and code style: [CONTRIBUTING.md](CONTRIBUTING.md).
