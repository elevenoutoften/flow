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
| `command_allowlist` | text | Comma-separated allowed command prefixes; empty allows all commands |
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

## Trust Boundary

Agent dispatch runs server-side commands with the full privileges of the Flow process. The `FLOW_API_KEY` environment variable gives the subprocess API access equivalent to the agent's configured role.

**Guardrails:**

- `command_allowlist`: A comma-separated list of allowed command prefixes. When non-empty, only commands starting with one of the listed prefixes are executed. Empty (default) allows all commands. Set this to restrict agents to specific binaries or scripts (e.g., `codex,python3`).
- `env_allowlist`: Controls which host environment variables are passed through to the subprocess.
- Agent `enabled` flag: Disabled agents cannot be dispatched.
- Permission `dispatch`: Only admin, architect, and implementer roles can dispatch.

**Recommendation:** For production deployments, set `command_allowlist` on all agents to the minimum set of commands they need. Avoid granting `dispatch` permission to roles that should not trigger server-side execution. Consider running the Flow dispatcher in a containerized environment with network policies that limit what subprocesses can access.

**Scoped credentials:** The `FLOW_API_KEY` provided to the subprocess has the same role as the agent's API key. If the agent was created with a read-only key, the subprocess only has read access. Use role-specific API keys to limit subprocess permissions.

## Permissions

| Permission | admin | architect | implementer | reviewer | read_only |
|-----------|-------|-----------|-------------|----------|-----------|
| `agent:read` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `agent:manage` | ✓ | ✓ | — | — | — |
| `dispatch` | ✓ | ✓ | ✓ | — | — |

## Capability Matching

Agents with empty `capabilities` match all tasks. Otherwise, the comma-separated tags are matched against keywords extracted from the task's `project`, `title`, and `description` fields.
