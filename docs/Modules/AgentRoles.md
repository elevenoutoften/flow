# Agent Roles and Capability Profiles

Flow supports role-scoped API keys and agent registration metadata so operators can give each agent only the access it needs. This guide maps common agent families to recommended Flow roles, capability tags, and `dispatch_statuses` settings.

## Recommended Roles by Agent Type

| Agent Type | Role | Permissions | `dispatch_statuses` | When to Use |
|------------|------|-------------|---------------------|-------------|
| Implementer (`Codex`, `Claude Code`, `OpenCode`) | `implementer` | Read, Create, Claim, Move, Note tasks | `todo` | Autonomous coding agents that pick up implementation work and hand off to review |
| Reviewer | `reviewer` | Read, Move, Note tasks | `review` | Code review agents that evaluate completed work, request rework, or close review-stage tasks |
| Planner / Architect | `architect` | Read, Create, Edit, Claim, Move, Note, Done tasks; manage rules | `backlog,todo` | Strategic agents that decompose ideas, reshape task scopes, and coordinate workflow policy |
| Observer / Monitor | `read_only` | Read tasks only | Empty or omitted | Dashboards, metrics collectors, audit watchers, and reporting bots |
| Runner / Dispatcher | `implementer` | Read, Create, Claim, Move, Note tasks | Varies by workflow | CI/CD runners, deployment agents, or execution bots that act on tasks but do not edit board policy |
| Notification Integration | `read_only` | Read tasks only | Empty or omitted | Slack, Telegram, email, or webhook notifiers that mirror task state elsewhere |

## Role Reference

Flow ships with these role names:

| Role | Intended Scope |
|------|----------------|
| `admin` | Full control, including API key and system management |
| `architect` | Planning, task shaping, backlog curation, automation management |
| `implementer` | Day-to-day task execution and coding work |
| `reviewer` | Review-stage evaluation and handback |
| `read_only` | Observation without mutation |

For the underlying permission matrix and transition rules, see [Security](Security.md).

## Capability Tags by Adapter Family

The `capabilities` field on an agent record is a comma-separated tag list. Use it to describe what the adapter can do and to help operators filter or audit agents.

| Adapter Family | Recommended Tag | Typical Meaning |
|----------------|-----------------|-----------------|
| Hermes | `hermes` | Uses Flow MCP tools or REST API for task management |
| Codex | `codex` | Autonomous coding, git operations, and test execution |
| Claude Code | `claude-code` | Interactive coding, code review, and debugging |
| OpenCode | `opencode` | Coding agent with selectable underlying models |
| Custom scripts | `custom` | Shell scripts, Python bots, cron jobs, or webhook receivers |
| MCP profiles | `mcp` | MCP tool servers with specific tool subscriptions |

### Tagging Guidance

- Keep capability tags short and stable so they remain useful for filtering over time.
- Use family tags for broad identification first, then add optional local tags only if they carry operational value.
- Avoid encoding secrets, model versions, or ephemeral runtime details in `capabilities`.
- Prefer a small, meaningful set such as `codex,mcp` over long descriptive strings.

## `dispatch_statuses` Guide

The `dispatch_statuses` field controls which task states an agent can be auto-dispatched from. It is stored as a comma-separated string.

| Value | Meaning | Typical Agents |
|-------|---------|----------------|
| `todo` | Agent picks up new work items | Implementers |
| `review` | Agent picks up completed work for evaluation | Reviewers |
| `backlog,todo` | Agent can promote or shape backlog items and also work normal queued tasks | Planners / architects |
| Empty or omitted | Agent requires explicit dispatch and is not part of the normal queue | Runners, observers, notifiers |

### Examples

- `todo`: a coding agent that claims ready implementation tasks.
- `review`: a review agent that only looks at tasks already handed off for review.
- `backlog,todo`: an architect agent that can both refine backlog items and work active queue items.
- Empty: a deployment runner triggered manually or by an automation rule for a specific task.

## Recommended Profiles

### Implementer Agents

- Role: `implementer`
- Capability tags: `codex`, `claude-code`, `opencode`, or `hermes` depending on adapter
- `dispatch_statuses`: `todo`
- Rationale: implementers need to claim, update, and move tasks, but they should not rewrite task definitions or manage credentials

### Reviewer Agents

- Role: `reviewer`
- Capability tags: adapter tag plus optional `mcp`
- `dispatch_statuses`: `review`
- Rationale: reviewers should focus on assessment, note-taking, and review-stage routing rather than direct planning or board administration

### Planner / Architect Agents

- Role: `architect`
- Capability tags: `hermes`, `custom`, or `mcp`
- `dispatch_statuses`: `backlog,todo`
- Rationale: planners often need to create tasks, edit descriptions, and manage automation rules as they shape work

### Observer and Integration Agents

- Role: `read_only`
- Capability tags: `custom` or `mcp`
- `dispatch_statuses`: empty
- Rationale: observers and notifiers should not mutate board state unless there is a specific operational need

### Runner / Dispatcher Agents

- Role: usually `implementer`
- Capability tags: `custom`, `mcp`, or family tag for the runner
- `dispatch_statuses`: workflow-specific or empty
- Rationale: execution agents often need task movement and note posting, but many should run only when explicitly dispatched

## Workspace Isolation

Each agent should run in its own workspace directory. Set `working_directory` on the agent record to a dedicated path. Do not share working directories between agents unless they are part of the same coordinated pipeline.

For CLI agents (Codex, Claude Code, OpenCode, custom scripts), the working directory should be the root of the project repository the agent operates on. For remote agents (MCP), working directory is often irrelevant since the agent connects over the network.

### Isolation Strategies

| Strategy | When to Use | How |
|----------|-------------|-----|
| Separate directories | Multiple agents on the same host | Set `working_directory` to per-agent checkout paths |
| Container isolation | Agents that need full environment separation | Run agents inside Docker containers with volume mounts |
| User/permission boundaries | Agents from different trust domains | Run agents under separate OS users with restricted permissions |
| Network namespaces | Agents that make outbound network calls | Use container networking or VPN segmentation |

### Preventing Workspace Contention

- Set `max_concurrency=1` when an agent writes to a shared repository to prevent concurrent write conflicts.
- Use `command_allowlist` to restrict agents to their intended CLI prefix (e.g., `codex` for Codex agents).
- Never store secrets in working directories — use `env:` or `file:` secret references instead.

## Secrets and Command Safety

### No Secrets in Commands

Never embed API keys, tokens, passwords, or other secrets directly in the `command` field. The command string is stored in the database and visible via the API. Use secret references instead:

- **`env:ENV_VAR_NAME`** — resolves from the process environment at dispatch time
- **`file:/path/to/secret`** — resolves from a file at dispatch time, restricted to allowed roots

Example — **incorrect**:
```
command: "curl -H 'Authorization: Bearer sk-abc123' https://api.example.com"
```

Example — **correct**:
```
command: "curl -H 'Authorization: Bearer' $(cat /etc/flow/webhook-secret)"
env_allowlist: "FLOW_API_KEY,WEBHOOK_SECRET"
```

Or, better yet, let the agent's own configuration read from the environment:

```
command: "codex exec --dangerously-bypass-approvals-and-sandbox -m gpt-5.5"
env_allowlist: "OPENAI_API_KEY,FLOW_BASE_URL,FLOW_API_KEY"
```

The `env_allowlist` field controls which environment variables are passed to the subprocess. Keep it minimal — only include variables the agent actually needs.

### Command Allowlist

Set `command_allowlist` to the CLI prefix(es) the agent is allowed to execute. This prevents an agent from running arbitrary commands outside its intended scope:

- `codex` → only commands starting with `codex`
- `python,hermes` → commands starting with `python` or `hermes`
- Empty → no restriction (use only for remote/MCP agents or when operators will set their own allowlist)

See [Adapter Templates](AdapterTemplates.md) for the built-in template allowlists.

## Security Best Practices

- Never use the `admin` role for automated agents unless there is no narrower role that satisfies the workflow.
- Use `implementer` for coding agents; it can work tasks without editing task descriptions or managing API keys.
- Use `reviewer` for review agents so they stay limited to review-stage work and note posting.
- Reserve `architect` for agents that genuinely need backlog curation, task editing, or rule management.
- Minimize `env_allowlist` to only the variables the agent actually needs at runtime.
- Set `max_concurrency` conservatively to avoid resource exhaustion, runaway subprocesses, or workspace contention.
- Leave `dispatch_statuses` empty when an agent should only run through explicit dispatch or automation rules.
- Rotate and reissue API keys when an adapter changes hands, environment, or trust boundary.
- Never embed secrets in `command` — use `env:` or `file:` references and `env_allowlist` to pass credentials to subprocesses.
- Set `command_allowlist` to restrict agents to their intended CLI prefix.
- Isolate agent workspaces with `working_directory` and container boundaries when running multiple agents on the same host.

## Selection Checklist

1. Start from the narrowest role that can complete the task.
2. Add only the capability tags that help operators understand the agent family.
3. Set `dispatch_statuses` only for queues the agent should consume automatically.
4. Limit `env_allowlist` and `max_concurrency` before enabling the agent.
5. Test with a non-admin key first and escalate only if the workflow proves it is necessary.

## See Also

- [Security](Security.md) for the exact permission model, API key roles, and secret reference guidance
- [Adapter Templates](AdapterTemplates.md) for built-in adapter presets with command allowlists
- [MCP Interface](MCP.md) for agent connectivity and tool exposure
- [Architecture](../Architecture.md) for lifecycle, invariants, and dispatch behavior
