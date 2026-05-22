# Flow Dogfood Runbook

## Overview

Flow dogfood dispatch lets Flow assign its own tasks to Hermes through the normal agent registry and dispatcher. The dispatcher starts `python -m flow_app.hermes_wrapper` as an agent subprocess, the wrapper claims the task, invokes the Hermes CLI, records notes and handoff data, and advances successful tasks to review.

## Architecture

```text
Flow dispatcher -> hermes_wrapper.py subprocess -> Hermes CLI -> Flow API
```

The dispatcher injects task and run metadata into the subprocess environment. The wrapper uses those values to call the Flow REST API, sends heartbeats for the active agent run, and completes the run with Hermes' exit code.

Use an internal Flow URL for `FLOW_BASE_URL`, for example `http://127.0.0.1:8100`. The public `example.com` URL is Cloudflare-protected and can reject non-browser clients.

## Required Environment Variables

| Variable | Required | Description |
| --- | --- | --- |
| `FLOW_TASK_ID` | Yes | Task ID assigned by the dispatcher. |
| `FLOW_PROJECT` | Yes | Project slug for the task. |
| `FLOW_BASE_URL` | Yes | Internal Flow API base URL. |
| `FLOW_API_KEY` | Yes | Role-scoped API key used by the wrapper. |
| `FLOW_RUN_ID` | Yes | Agent run ID for heartbeat and completion calls. |
| `HERMES_COMMAND` | No | Hermes command template. Defaults to `hermes run`. |
| `HERMES_TIMEOUT` | No | Hermes subprocess timeout in seconds. Defaults to `600`. |
| `HERMES_AGENT_NAME` | No | Author name for notes and handoffs. Defaults to `hermes`. |

## Agent Registration

Register Hermes as a Flow agent with an architect or admin key:

```bash
curl -s -X POST http://127.0.0.1:8100/api/agents \
  -H "Authorization: Bearer ${FLOW_ADMIN_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "hermes-delegator",
    "description": "Hermes dogfood agent for Flow tasks",
    "command": "python -m flow_app.hermes_wrapper",
    "capabilities": "planning,implementation,review,dogfood",
    "max_concurrency": 1,
    "heartbeat_timeout_seconds": 900
  }'
```

## Agent Command Template

Use this `command` value in the agent record:

```text
python -m flow_app.hermes_wrapper
```

Set `working_directory` to the Flow repository path when the agent should edit this repo:

```text
/opt/flow
```

If Hermes is not on `PATH`, set `HERMES_COMMAND` in the dispatcher environment or the agent environment allowlist, for example:

```text
/usr/local/bin/hermes run
```

## Task Lifecycle

1. A task is created in `backlog` or `todo`.
2. The dispatcher selects `hermes-delegator` and creates an agent run.
3. The dispatcher starts `python -m flow_app.hermes_wrapper` with `FLOW_TASK_ID`, `FLOW_PROJECT`, `FLOW_BASE_URL`, `FLOW_API_KEY`, and `FLOW_RUN_ID`.
4. The wrapper patches the task to `doing` and assigns it to `hermes-<user-or-host>`.
5. The wrapper posts a starting note and sends an initial heartbeat.
6. The wrapper fetches task details and builds a Hermes prompt from title, description, and acceptance criteria.
7. Hermes runs in the current workspace.
8. The wrapper posts Hermes output, writes a structured handoff, and sends final heartbeats.
9. On exit code `0`, the task moves to `review`.
10. On non-zero exit, the task remains in `doing` with an error note and failed handoff.
11. The wrapper completes the agent run with the Hermes exit code.

## Deployment Steps

1. Confirm Flow is serving on an internal address:

   ```bash
   curl -s http://127.0.0.1:8100/healthz
   ```

2. Create a role-scoped API key with permissions to claim, edit, move tasks, create handoffs, dispatch heartbeats, and complete runs. An `admin`, `architect`, or suitable `implementer` key can be used depending on local policy.

3. Ensure the dispatcher uses the internal base URL:

   ```bash
   export FLOW_BASE_URL=http://127.0.0.1:8100
   ```

4. Ensure Hermes is available:

   ```bash
   hermes --help
   ```

5. Register the agent using the payload above.

6. Dispatch one task manually:

   ```bash
   curl -s -X POST "http://127.0.0.1:8100/api/agents/<agent_id>/dispatch?task_id=<task_id>" \
     -H "Authorization: Bearer ${FLOW_API_KEY}"
   ```

7. Watch the task notes, handoff, status, and agent run:

   ```bash
   curl -s http://127.0.0.1:8100/api/tasks/<task_id> -H "Authorization: Bearer ${FLOW_API_KEY}"
   curl -s http://127.0.0.1:8100/api/agent-runs/<run_id> -H "Authorization: Bearer ${FLOW_API_KEY}"
   ```

## Troubleshooting

| Symptom | Likely Cause | Fix |
| --- | --- | --- |
| `Missing required environment variable` | Wrapper was run outside the dispatcher or env injection failed. | Run through dispatcher or export all required variables for a manual test. |
| HTTP 401 or 403 from Flow | API key missing, revoked, or under-scoped. | Create a role-scoped key with task, handoff, and dispatch permissions. |
| Hermes command not found | `hermes` is not on the dispatcher's `PATH`. | Set `HERMES_COMMAND` to an absolute command path. |
| Task stays in `doing` | Hermes exited non-zero. | Read the latest task note and handoff for stderr and remaining work. |
| Agent run becomes stale | Timeout too short or wrapper could not heartbeat. | Increase `heartbeat_timeout_seconds` and verify Flow is reachable on `FLOW_BASE_URL`. |
| Cloudflare or HTML response from API | Public URL used for `FLOW_BASE_URL`. | Use an internal URL such as `http://127.0.0.1:8100`. |

## Security Notes

Use a dedicated API key for Hermes and rotate it regularly. Store the key only in the dispatcher runtime environment or secret manager, not in the agent command string. Prefer the least role that can claim tasks, edit task notes, move tasks to review, create handoffs, heartbeat runs, and complete runs. Keep `max_concurrency` conservative until Hermes runs are predictable, and set `working_directory` deliberately so the agent only edits intended repositories.

## Fresh Install Bootstrap

```bash
# Clone and install
git clone https://github.com/elevenoutoften/flow.git
cd flow
pip install -e .

# Bootstrap (creates project, keys, agent, workspace, rules)
flow-bootstrap

# Save the printed API keys - they are shown only once.
# Start the server
uvicorn flow_app.main:app --port 8100

# Verify health
curl http://127.0.0.1:8100/healthz

# Dispatch the smoke-test agent
curl -s -X POST "http://127.0.0.1:8100/api/agents/<agent_id>/dispatch" \
  -H "Authorization: Bearer <admin-key>"
```

## Telegram Notifications

Set these environment variables to notify humans in Telegram when task events fire:

| Variable | Description |
| --- | --- |
| `FLOW_TELEGRAM_BOT_TOKEN` | Bot token from @BotFather. |
| `FLOW_TELEGRAM_CHAT_ID` | Chat ID for notifications, either a DM or group. |

```bash
export FLOW_TELEGRAM_BOT_TOKEN="123456:ABC-DEF..."
export FLOW_TELEGRAM_CHAT_ID="-1001234567890"
uvicorn flow_app.main:app --port 8100
```

Dogfood rule examples:

| Rule | Trigger | Condition | Action |
| --- | --- | --- | --- |
| Notify on blocked task | `task_blocked` | Any task requiring human help | Auto, via Telegram provider |
| Notify on review ready | `task_moved` | `to_status == "review"` | Auto, via Telegram provider |

## Review Loop

Flow includes production-ready review-loop automation rules out of the box. See [Review Loop](REVIEW_LOOP.md) for full documentation.

Quick summary:
- Tasks reaching `review` automatically dispatch the `reviewer-agent`
- Tasks arriving in `review` without a handoff get an automated warning
- Reviewer can approve (`→ done`) or reject (`→ todo` with `human_required`)
- New tasks in `backlog` are auto-promoted to `todo`
