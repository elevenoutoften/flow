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

## See Also

- [REST API](REST-API.md) — HTTP endpoint reference
- [Security](Security.md) — roles and permissions
- [Architecture](../Architecture.md) — system design
