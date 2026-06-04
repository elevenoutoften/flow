# Dispatcher

## Source

| File | Role |
|------|------|
| `flow_app/dispatcher.py` | Agent subprocess spawning, monitoring, stale recovery |
| `flow_app/runner.py` | Unified automation runner loop |
| `flow_app/dispatcher_cli.py` | CLI for manual dispatch |
| `flow_app/models.py:134-173` | `Agent`, `AgentRun` models |
| `flow_app/routes/agents.py` | REST API for agents and runs |

## Agent Model

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `id` | string | auto (`agent_NNNNNN`) | Primary key |
| `name` | string(180) | *(required, unique)* | Agent name |
| `description` | text | `""` | Description |
| `enabled` | bool | `true` | Active flag |
| `agent_type` | string(24) | `cli` | Agent type |
| `capabilities` | text | `""` | Comma-separated capability tags |
| `command` | text | `""` | Shell command template |
| `command_allowlist` | text | `""` | Comma-separated allowed command prefixes |
| `env_allowlist` | text | `""` | Comma-separated env var names to pass through |
| `working_directory` | string(500) | `""` | CWD for subprocess |
| `max_concurrency` | int | `1` | Max simultaneous runs |
| `heartbeat_timeout_seconds` | int | `300` | Heartbeat timeout |
| `stale_claim_timeout_seconds` | int | `600` | Stale recovery timeout |
| `dispatch_statuses` | text | `backlog,todo` | Statuses this agent picks up |

## AgentRun Model

| Field | Type | Description |
|-------|------|-------------|
| `id` | string (`run_NNNNNN`) | Primary key |
| `agent_id` | string | FK to agents |
| `task_id` | string | FK to tasks |
| `status` | string | `pending`, `running`, `done`, `crashed`, `stale` |
| `pid` | int? | Subprocess PID |
| `exit_code` | int? | Process exit code |
| `started_at` | datetime? | When the run started |
| `finished_at` | datetime? | When the run completed |
| `last_heartbeat_at` | datetime? | Last heartbeat |
| `workspace_state` | text (JSON) | Provisioned workspace info |

## Dispatch Lifecycle

```
  dispatch_one()
       │
       ├── Validate: agent enabled, task not claimed, not human_required,
       │             dispatch-ready, concurrency not exceeded, command allowed
       │
       ├── Claim task: set assignee, move to doing
       │
       ├── Create AgentRun (status=running)
       │
       ├── Provision workspace (if configured)
       │
       ├── Build environment variables
       │
       ├── subprocess.Popen(command, cwd, env)
       │
       ├── Start background monitor thread
       │
       └── Return AgentRun
```

### Background Monitor

A daemon thread polls `process.poll()` every 5 seconds. When the process exits, it calls `complete_run()` which:
- Sets `exit_code` and `status` (`done` if 0, `crashed` otherwise).
- On crash: moves task back to `todo`, clears assignee, adds error note.
- Cleans up workspace.

### Command Templates

The `command` field supports placeholders: `{task_id}`, `{agent_id}`, `{run_id}`, `{command}`.

### Environment Variables

Dispatched subprocesses receive:

| Variable | Description |
|----------|-------------|
| `FLOW_TASK_ID` | Task ID |
| `FLOW_PROJECT` | Task's project slug |
| `FLOW_AGENT_NAME` | Agent name |
| `FLOW_BASE_URL` | Flow API base URL |
| `FLOW_API_KEY` | API key for the subprocess |
| `FLOW_RUN_ID` | Run ID |
| `FLOW_WORKSPACE_DIR` | Provisioned workspace path (if any) |

Plus any host env vars listed in `env_allowlist`.

## Stale Recovery

`stale_recovery()` finds all `running` agent runs where `last_heartbeat_at` (or `started_at`) is older than `stale_claim_timeout_seconds`. For each:
- Sets run status to `stale`.
- Moves task back to `todo`, clears assignee.
- Adds a system note.
- Cleans up workspace.

## Capability Matching

Agents with empty `capabilities` match all tasks. Otherwise, comma-separated tags are matched against keywords extracted from the task's `project`, `title`, and `description` fields.

## Trust Boundary

Agent dispatch runs server-side commands with the full privileges of the Flow process. The `FLOW_API_KEY` gives the subprocess API access equivalent to the agent's configured role.

**Guardrails:**
- `command_allowlist`: restricts which commands can be executed.
- `env_allowlist`: controls which host env vars are passed through.
- `enabled` flag: disabled agents cannot be dispatched.
- `dispatch` permission: only admin, architect, and implementer roles can dispatch.

## Runner

The runner (`flow_app.runner`) is a separate process that runs periodic passes:

1. **Dispatch** — for each agent profile, find next capable task and dispatch.
2. **Stale recovery** — recover stale agent runs.
3. **Cron rules** — evaluate cron-triggered automation rules.
4. **Webhook delivery** — process pending webhook deliveries.

### CLI

```bash
python -m flow_app.runner --once                    # One pass
python -m flow_app.runner --once --stale-recovery-only
python -m flow_app.runner --profiles agent1,agent2  # Continuous loop
python -m flow_app.runner --dry-run                 # Preview only
```

## Dispatcher CLI

```bash
python -m flow_app.dispatcher_cli --agent codex-agent --api-key flow_... --base-url http://localhost:8100
python -m flow_app.dispatcher_cli --agent codex-agent --continuous --interval 10
python -m flow_app.dispatcher_cli --stale-recovery
```

## See Also

- [Workspace](Workspace.md) — workspace provisioning
- [Automation Rules](AutomationRules.md) — event-driven automation
- [Operations](../Operations.md) — runner deployment
- [Security](Security.md) — dispatch permissions
