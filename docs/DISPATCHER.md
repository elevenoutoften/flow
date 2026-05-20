# Agent Registry & Dispatcher

Flow's Agent Registry and Dispatcher enable autonomous agents to claim and execute tasks.

## Agent Model

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Auto-generated (`agent_NNNNNN`) |
| `name` | string | Unique agent name |
| `description` | text | Human-readable description |
| `enabled` | bool | Whether the agent is active |
| `agent_type` | string | Agent type (default: `cli`) |
| `capabilities` | text | Comma-separated capability tags |
| `command` | text | Shell command template with `{task_id}`, `{agent_id}`, `{run_id}` placeholders |
| `env_allowlist` | text | Comma-separated env var names to pass through |
| `working_directory` | string | CWD for the subprocess |
| `max_concurrency` | int | Max simultaneous runs (default: 1) |
| `heartbeat_timeout_seconds` | int | Heartbeat timeout (default: 300) |
| `stale_claim_timeout_seconds` | int | Stale recovery timeout (default: 600) |

## AgentRun Model

Tracks each agent execution:

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Auto-generated (`run_NNNNNN`) |
| `agent_id` | string | FK to `agents.id` |
| `task_id` | string | FK to `tasks.id` |
| `status` | string | `pending`, `running`, `done`, `crashed`, `stale` |
| `pid` | int? | Subprocess PID |
| `exit_code` | int? | Process exit code |
| `started_at` | datetime? | When the run started |
| `finished_at` | datetime? | When the run completed |
| `last_heartbeat_at` | datetime? | Last heartbeat timestamp |

## REST API

### Agents

- `GET /api/agents` — List agents (`?enabled_only=true`)
- `GET /api/agents/{id}` — Get agent
- `POST /api/agents` — Create agent
- `PATCH /api/agents/{id}` — Update agent

### Agent Runs

- `GET /api/agent-runs` — List runs (`?agent_id=&task_id=&status=`)
- `GET /api/agent-runs/{id}` — Get run
- `POST /api/agents/{id}/dispatch?task_id=` — Dispatch agent to task
- `POST /api/agent-runs/{id}/heartbeat` — Record heartbeat
- `POST /api/agent-runs/{id}/complete?exit_code=` — Complete run
- `POST /api/agent-runs/stale-recovery` — Recover stale runs

## MCP Tools

| Tool | Permission | Description |
|------|-----------|-------------|
| `flow_list_agents` | `agent:read` | List agents |
| `flow_get_agent` | `agent:read` | Get agent by ID |
| `flow_create_agent` | `agent:manage` | Register agent |
| `flow_update_agent` | `agent:manage` | Update agent |
| `flow_dispatch_agent` | `dispatch` | Dispatch agent to task |
| `flow_list_agent_runs` | `tasks:read` | List runs |
| `flow_heartbeat` | `dispatch` | Record heartbeat |
| `flow_complete_run` | `dispatch` | Complete a run |

## CLI

```bash
python -m flow_app.dispatcher_cli --agent codex-agent --api-key flow_... --base-url http://localhost:8000
python -m flow_app.dispatcher_cli --agent codex-agent --continuous --interval 10
python -m flow_app.dispatcher_cli --stale-recovery
```

## Environment Variables

Dispatched subprocesses receive:

| Variable | Description |
|----------|-------------|
| `FLOW_TASK_ID` | The task ID |
| `FLOW_PROJECT` | The task's project |
| `FLOW_AGENT_NAME` | The agent name |
| `FLOW_BASE_URL` | Flow API base URL |
| `FLOW_API_KEY` | API key for the subprocess |
| `FLOW_RUN_ID` | The run ID |

## Permissions

| Permission | admin | architect | implementer | reviewer | read_only |
|-----------|-------|-----------|-------------|----------|-----------|
| `agent:read` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `agent:manage` | ✓ | ✓ | — | — | — |
| `dispatch` | ✓ | ✓ | ✓ | — | — |

## Capability Matching

Agents with empty `capabilities` match all tasks. Otherwise, the comma-separated tags are matched against keywords extracted from the task's `project`, `title`, and `description` fields.