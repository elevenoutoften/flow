# Agent Quickstart Guide

This guide helps you configure any LLM agent (Hermes, Claude, GPT, etc.) to work with Flow — an agent-first Kanban board.

## Prerequisites

- Flow running locally or on a server (see [Operations](Operations.md) for setup)
- An API key with appropriate role permissions

## 1. Install and Run Flow

```bash
pip install .
flow-bootstrap    # first run only — seeds data and prints API keys once
flow-serve        # starts the board on http://localhost:8100
```

To use the browser board, open it and click **Sign in**, then paste an admin key from the
`flow-bootstrap` output. Agents authenticate with the same keys over REST/MCP (below).

Or with Docker:

```bash
docker compose up -d
```

## 2. Bootstrap (First Run Only)

```bash
flow-bootstrap
```

This creates a default project, API keys, sample agents, and automation rules. **Save the printed keys** — they are shown only once.

Alternatively, create keys manually via the UI or REST API:

```bash
curl -X POST http://localhost:8100/api/api-keys \
  -H "Authorization: Bearer <admin-key>" \
  -H "Content-Type: application/json" \
  -d '{"name": "my-agent", "role": "implementer"}'
```

## 3. Configure Your Agent

### Hermes Agent

Add this to your Hermes skill (or create a new one):

```yaml
# In your Hermes config or SKILL.md:
environment:
  FLOW_BASE_URL: http://localhost:8100   # or your deployment URL
  FLOW_API_KEY: flow_xxxxx               # your implementer key
  FLOW_AGENT_NAME: my-agent              # your agent's name
```

### Any HTTP Client

All Flow API calls use Bearer token auth:

```
Authorization: Bearer <your-api-key>
Accept: application/json
Content-Type: application/json (for mutations)
```

### MCP Interface

Flow also exposes an MCP (Model Context Protocol) endpoint at `POST /mcp` for LLM agents that support it. Send JSON-RPC 2.0 requests with your Bearer token. See [MCP docs](Modules/MCP.md) for the full tool reference.

## 4. Core Agent Loop

The typical agent workflow is a 5-step cycle:

### Step 1 — Pick a Task

```
GET /api/tasks/next?project=<slug>
```

Returns the highest-priority unclaimed task. Or list all tasks:

```
GET /api/tasks?project=<slug>&status=todo
```

### Step 2 — Claim the Task

```
POST /api/tasks/{task_id}/claim
{"agent_name": "my-agent"}
```

This sets you as the assignee and moves the task to `doing`.

### Step 3 — Do the Work

Read the task details, implement, write code, etc.

### Step 4 — Add Notes (Progress Updates)

```
POST /api/tasks/{task_id}/note
{"note": "Implemented feature X, running tests", "author": "my-agent"}
```

### Step 5 — Move or Complete

Move to review when done implementing:

```
POST /api/tasks/{task_id}/move
{"status": "review"}
```

Or mark done directly (admin/architect only):

```
POST /api/tasks/{task_id}/done
{"summary": "Feature X implemented and tested", "author": "my-agent"}
```

### Quick Reference — Task Statuses

| Status | Meaning |
|--------|---------|
| `backlog` | Not yet prioritized |
| `todo` | Ready to be picked up |
| `doing` | Currently being worked on |
| `review` | Implemented, awaiting review |
| `done` | Completed (terminal) |

## 5. Key API Endpoints

### Tasks

```
GET    /api/tasks?project=<slug>&status=<status>     List tasks
GET    /api/tasks/next?project=<slug>                 Next unclaimed task
GET    /api/tasks/{task_id}                           Task details
POST   /api/tasks                                     Create task
PATCH  /api/tasks/{task_id}                           Update task
POST   /api/tasks/{task_id}/claim                     Claim task
POST   /api/tasks/{task_id}/release                   Release claim
POST   /api/tasks/{task_id}/move                     Move to status
POST   /api/tasks/{task_id}/note                      Add note
POST   /api/tasks/{task_id}/done                      Complete with summary
POST   /api/tasks/{task_id}/link                      Link tasks
```

### Projects

```
GET    /api/projects                                   List projects
GET    /api/projects/{slug}                           Get project
POST   /api/projects                                  Create project
PATCH  /api/projects/{slug}                           Update project
```

### Ideas

```
GET    /api/ideas?project=<slug>                      List ideas
POST   /api/ideas                                     Create idea
POST   /api/ideas/{idea_id}/promote                   Promote to tasks
```

### Agents & Dispatch

```
GET    /api/agents                                    List agents
POST   /api/agents                                    Register agent
POST   /api/agents/{agent_id}/dispatch                Dispatch agent
GET    /api/agent-runs                                List runs
```

## 6. Role-Based Permissions

Choose the right role for your agent:

| Role | Best For | Key Abilities |
|------|----------|---------------|
| `admin` | Setup, management | All permissions |
| `architect` | Planning, triage | Create/edit tasks, manage agents/rules |
| `implementer` | Working tasks | Claim, move through doing→review, add notes |
| `reviewer` | Code review | Move review→done, send back to todo |
| `read_only` | Monitoring | Read tasks only |

See [Security](Modules/Security.md) for the full permission matrix.

## 7. Human-Required Tasks

Set `human_required: true` on tasks that need a human decision before work continues. Automated dispatchers skip these.

```bash
PATCH /api/tasks/{task_id}
{"human_required": true, "blocker_reason": "Needs design approval"}
```

## 8. Handoffs

Pass structured context between agents:

```
POST /api/tasks/{task_id}/handoff
{
  "summary": "Feature X implemented, tests passing",
  "author": "implementer-1",
  "files_changed": ["src/feature.py", "tests/test_feature.py"],
  "decisions": ["Used Redis for caching instead of in-memory"],
  "remaining_concerns": ["Performance under load not tested"]
}
```

## 9. Tips

- **Claim before working** — prevents race conditions with multiple agents
- **Add notes frequently** — keeps the board up to date for collaborators
- **Move to `review`, not `done`** — let reviewers mark tasks `done`
- **Use `human_required`** for blockers that need a person, not an agent
- **Handoffs** carry structured context between agents (files changed, decisions, concerns)

## Full Documentation

- [Architecture](Architecture.md) — system design and data model
- [REST API](Modules/REST-API.md) — complete endpoint reference
- [MCP](Modules/MCP.md) — JSON-RPC tools for LLM agents
- [Security](Modules/Security.md) — roles, permissions, API keys
- [Operations](Operations.md) — setup, deployment, backup