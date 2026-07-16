# MCP Interface

Flow provides a Model Context Protocol (MCP) endpoint for LLM agents at `POST /mcp`. The protocol is JSON-RPC 2.0.

## Source

| File | Role |
|------|------|
| `flow_app/mcp/__init__.py` | Package exports |
| `flow_app/mcp/dispatch.py` | Tool definitions, JSON-RPC dispatch, request handlers |
| `flow_app/main.py:107-144` | Route mounting, auth resolution |

## Endpoint

```
POST /mcp
Content-Type: application/json
```

## Authentication

Same as the REST API: bearer token, session cookie, or trusted headers. Bearer tokens take precedence. Unauthenticated requests receive a permission error.

## Protocol Methods

| Method | Description |
|--------|-------------|
| `initialize` | Handshake — returns server capabilities and version |
| `ping` | Health check |
| `notifications/initialized` | Client notification (202, no response) |
| `tools/list` | Returns all available tools with input schemas |
| `tools/call` | Invoke a specific tool |

## Tool Reference

### Task Tools

| Tool | Permission | Description |
|------|-----------|-------------|
| `flow_list_tasks` | `tasks:read` | List tasks (filter by project, status) |
| `flow_get_task` | `tasks:read` | Get task by ID |
| `flow_create_task` | `tasks:create` | Create task |
| `flow_update_task` | `tasks:edit` | Update task fields |
| `flow_move_task` | `tasks:move` | Move task to new status |
| `flow_set_human_required` | `tasks:set_human_required` | Set/clear human-required blocker |
| `flow_board_summary` | `tasks:read` | Board counts by status + human-required tasks |
| `flow_next_task` | `tasks:read` | Get next unclaimed dispatchable task |

### Idea Tools

| Tool | Permission | Description |
|------|-----------|-------------|
| `flow_list_ideas` | `tasks:read` | List ideas |
| `flow_create_idea` | `tasks:create` | Create idea |
| `flow_update_idea` | `tasks:edit` | Update idea |
| `flow_archive_idea` | `tasks:edit` | Archive idea |
| `flow_promote_idea` | `tasks:create` | Promote idea to linked tasks |

### Agent Tools

| Tool | Permission | Description |
|------|-----------|-------------|
| `flow_list_agents` | `agent:read` | List agents |
| `flow_get_agent` | `agent:read` | Get agent by ID |
| `flow_create_agent` | `agent:manage` | Register agent |
| `flow_update_agent` | `agent:manage` | Update agent |
| `flow_dispatch_agent` | `dispatch` | Dispatch agent to task |
| `flow_list_agent_runs` | `tasks:read` | List agent runs |
| `flow_heartbeat` | `dispatch` | Record heartbeat |
| `flow_complete_run` | `dispatch` | Complete a run |

When registering agents for MCP-driven workflows, choose the narrowest Flow role that fits the adapter. See [Agent Roles and Capability Profiles](AgentRoles.md) for recommended role assignments, capability tags, and `dispatch_statuses`.

### Automation Tools

| Tool | Permission | Description |
|------|-----------|-------------|
| `flow_list_automation_rules` | `rules:read` | List rules |
| `flow_get_automation_rule` | `rules:read` | Get rule |
| `flow_create_automation_rule` | `rules:manage` | Create rule |
| `flow_update_automation_rule` | `rules:manage` | Update rule |
| `flow_evaluate_rules` | `rules:evaluate` | Evaluate rules |

### Link Tools

| Tool | Permission | Description |
|------|-----------|-------------|
| `flow_link_task` | `links:manage` | Create task link |
| `flow_unlink_task` | `links:manage` | Delete task link |
| `flow_get_dependencies` | `links:read` | Dependency summary |
| `flow_list_task_links` | `links:read` | List links |

### Handoff Tools

| Tool | Permission | Description |
|------|-----------|-------------|
| `flow_task_handoff` | `handoff:create` | Create structured handoff |
| `flow_get_task_handoffs` | `handoff:read` | List handoffs for task |

### Workspace Tools

| Tool | Permission | Description |
|------|-----------|-------------|
| `flow_list_workspace_configs` | `workspace:read` | List workspace configs |
| `flow_get_workspace_config` | `workspace:read` | Get config |
| `flow_create_workspace_config` | `workspace:manage` | Create config |
| `flow_update_workspace_config` | `workspace:manage` | Update config |
| `flow_provision_workspace` | `workspace:manage` | Provision workspace |
| `flow_cleanup_workspace` | `workspace:manage` | Cleanup workspace |

### Webhook Tools

| Tool | Permission | Description |
|------|-----------|-------------|
| `flow_list_webhooks` | `webhook:read` | List webhooks |
| `flow_create_webhook` | `webhook:manage` | Create webhook |
| `flow_update_webhook` | `webhook:manage` | Update webhook |
| `flow_delete_webhook` | `webhook:manage` | Delete webhook |

## Response Format

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "content": [{"type": "text", "text": "Found 5 Flow tasks."}],
    "structuredContent": {"tasks": [...], "count": 5},
    "isError": false
  }
}
```

Errors return:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "error": {"code": -32603, "message": "Insufficient permission."}
}
```

## Rate Limiting

Mutating MCP tool calls are rate-limited using the same token-bucket limiter as the REST API.

**How it works:**

- Each `tools/call` request is classified as **read-only** or **mutating** based on the tool name.
- Read-only tools (listed below) bypass the limiter and can be called freely.
- Mutating tools are throttled at **120 requests per 60 seconds** per client (configurable via `FLOW_RATE_LIMIT_MUTATIONS`).
- The limiter is keyed **per API key**. If no API key is present (e.g. session cookie auth), it falls back to the **client IP address**.
- Rate limiting can be disabled entirely with `FLOW_RATE_LIMIT_ENABLED=false`.

**Read-only tools (bypass the mutation limiter):**

`flow_list_tasks`, `flow_get_task`, `flow_get_dependencies`, `flow_list_task_links`, `flow_get_task_handoffs`, `flow_board_summary`, `flow_list_audit_logs`, `flow_list_ideas`, `flow_list_recurring_task_templates`, `flow_get_recurring_task_template`, `flow_list_agents`, `flow_list_adapter_templates`, `flow_get_adapter_template`, `flow_preview_adapter_template`, `flow_get_agent`, `flow_list_runners`, `flow_get_runner`, `flow_list_webhooks`, `flow_get_webhook`, `flow_list_webhook_deliveries`, `flow_get_webhook_delivery`, `flow_list_workspace_configs`, `flow_get_workspace_config`.

**Mutating tools (subject to the limiter):**

All tools not listed above — includes `flow_create_task`, `flow_update_task`, `flow_move_task`, `flow_create_agent`, `flow_dispatch_agent`, `flow_create_automation_rule`, `flow_link_task`, `flow_create_webhook`, `flow_pack_import`, etc.

**429 response shape:**

When the mutation limit is exceeded, the server returns HTTP 429 with a JSON-RPC error body:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "error": {
    "code": -32600,
    "message": "Rate limit exceeded. Try again later."
  }
}
```

The HTTP status code is `429 Too Many Requests`. MCP clients should parse the JSON-RPC error and back off before retrying.

**Configuration:**

| Env var | Default | Description |
|---------|---------|-------------|
| `FLOW_RATE_LIMIT_ENABLED` | `true` | Enable/disable all rate limiting |
| `FLOW_RATE_LIMIT_MUTATIONS` | `120` | Max mutating calls per 60s window per key/IP |
| `FLOW_RATE_LIMIT_KEY_CREATION` | `10` | Max API key creation calls per 60s window |

## See Also

- [REST API](REST-API.md) — HTTP endpoint reference
- [Security](Security.md) — roles and permissions
- [Architecture](../Architecture.md) — system design
