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

## Security Best Practices

- Never use the `admin` role for automated agents unless there is no narrower role that satisfies the workflow.
- Use `implementer` for coding agents; it can work tasks without editing task descriptions or managing API keys.
- Use `reviewer` for review agents so they stay limited to review-stage work and note posting.
- Reserve `architect` for agents that genuinely need backlog curation, task editing, or rule management.
- Minimize `env_allowlist` to only the variables the agent actually needs at runtime.
- Set `max_concurrency` conservatively to avoid resource exhaustion, runaway subprocesses, or workspace contention.
- Leave `dispatch_statuses` empty when an agent should only run through explicit dispatch or automation rules.
- Rotate and reissue API keys when an adapter changes hands, environment, or trust boundary.

## Selection Checklist

1. Start from the narrowest role that can complete the task.
2. Add only the capability tags that help operators understand the agent family.
3. Set `dispatch_statuses` only for queues the agent should consume automatically.
4. Limit `env_allowlist` and `max_concurrency` before enabling the agent.
5. Test with a non-admin key first and escalate only if the workflow proves it is necessary.

## See Also

- [Security](Security.md) for the exact permission model and API key roles
- [MCP Interface](MCP.md) for agent connectivity and tool exposure
- [Architecture](../Architecture.md) for lifecycle, invariants, and dispatch behavior
