# Adapter Templates

Adapter templates are built-in, in-memory presets for registering common Flow agents. They describe the adapter family, default command, capability tags, dispatch statuses, concurrency limits, environment allowlist, and setup notes without adding database tables or changing the Agent model.

Templates are useful when an operator wants a known-good starting point and still needs a normal persisted agent record for dispatch.

## Built-In Templates

| Name | Family | Agent Type | Recommended Role | Notes |
|------|--------|------------|------------------|-------|
| `hermes` | `hermes` | `cli` | `implementer` | Runs `flow_app.hermes_wrapper` and delegates to a configured agent CLI. |
| `codex` | `codex` | `cli` | `implementer` | Runs Codex CLI in autonomous mode. |
| `claude-code` | `claude-code` | `cli` | `implementer` or `reviewer` | Runs Claude Code CLI in autonomous mode. |
| `opencode` | `opencode` | `cli` | `implementer` | Runs OpenCode with a configured model. |
| `opencrawl` | `opencrawl` | `cli` | `read_only` or custom crawl role | Intended for explicit crawl/extract dispatches. |
| `mcp` | `mcp` | `remote` | role depends on connected tool | Represents protocol-connected agents instead of subprocess commands. |
| `custom-script` | `custom` | `cli` | narrowest role that can do the script work | Starting point for local scripts. |

See [Agent Roles](AgentRoles.md) for role and capability guidance.

## REST Usage

List templates:

```bash
curl -H "Authorization: Bearer $FLOW_API_KEY" \
  "$FLOW_BASE_URL/api/adapter-templates"
```

Get a template:

```bash
curl -H "Authorization: Bearer $FLOW_API_KEY" \
  "$FLOW_BASE_URL/api/adapter-templates/hermes"
```

Preview a template without creating an agent:

```bash
curl -X POST -H "Authorization: Bearer $FLOW_API_KEY" -H "Content-Type: application/json" \
  -d '{
    "name": "my-hermes-agent",
    "command": "python -m flow_app.hermes_wrapper --custom-flag"
  }' \
  "$FLOW_BASE_URL/api/adapter-templates/hermes/preview"
```

Preview returns the merged agent fields, the `source_template`, `overrides_applied`, and conflict metadata. When an agent name is already taken, `would_create` is `false` and `conflict_with` contains the existing agent ID. Nothing is persisted.

Instantiate a template into a persisted agent:

```bash
curl -X POST -H "Authorization: Bearer $FLOW_API_KEY" -H "Content-Type: application/json" \
  -d '{
    "name": "my-hermes-agent",
    "command": "python -m flow_app.hermes_wrapper --custom-flag",
    "dispatch_statuses": "backlog,todo",
    "env_allowlist": "FLOW_BASE_URL,FLOW_API_KEY,MY_EXTRA_VAR"
  }' \
  "$FLOW_BASE_URL/api/adapter-templates/hermes/instantiate"
```

The instantiate endpoint merges the selected template defaults with supplied overrides, validates the result as `AgentCreate`, and returns the created `AgentResponse`.

Collision strategies are controlled with `on_collision`:

| Strategy | Behavior |
|----------|----------|
| `error` | Default. Duplicate agent names return `409 Conflict`. |
| `skip` | Return the existing agent with `200 OK` and leave it unchanged. |
| `update` | Patch the existing agent with template defaults plus overrides and return `200 OK`. |

Example idempotent import:

```bash
curl -X POST -H "Authorization: Bearer $FLOW_API_KEY" -H "Content-Type: application/json" \
  -d '{"name": "my-codex", "on_collision": "skip"}' \
  "$FLOW_BASE_URL/api/adapter-templates/codex/instantiate"
```

## MCP Usage

Use `flow_list_adapter_templates` to list names, families, and descriptions. Use `flow_get_adapter_template` with a `name` parameter to fetch full template details.

Use `flow_preview_adapter_template` with `template`, `name`, and optional override fields to preview the merged agent payload. Use `flow_instantiate_adapter_template` with the same fields plus optional `on_collision` to create, skip, or update an agent.

Example tool call arguments:

```json
{"name": "flow_get_adapter_template", "arguments": {"name": "codex"}}
```

```json
{"name": "flow_preview_adapter_template", "arguments": {"template": "codex", "name": "my-codex"}}
```

```json
{"name": "flow_instantiate_adapter_template", "arguments": {"template": "codex", "name": "my-codex", "on_collision": "update"}}
```

Agents can use MCP to inspect, preview, and instantiate templates, or create a custom agent directly with `flow_create_agent`.

## CLI Usage

`flow-adapter-import` is a convenience wrapper around the REST API. It reads `FLOW_BASE_URL` and `FLOW_API_KEY`, with `FLOW_BASE_URL` defaulting to `http://localhost:8100`.

List templates:

```bash
flow-adapter-import --list
```

Preview an import:

```bash
flow-adapter-import --preview hermes --name my-hermes-agent
```

Import with a collision strategy:

```bash
flow-adapter-import codex --name my-codex --on-collision skip
```

Common template override flags are supported, including `--command`, `--dispatch-statuses`, `--env-allowlist`, `--max-concurrency`, and timeout fields.

## Custom Agents

For a small variation of a built-in family, instantiate the built-in template and override only the fields that differ, such as `name`, `command`, `dispatch_statuses`, or `env_allowlist`.

For an agent family that does not match the built-ins, either instantiate `custom-script` as a starting point or create an agent from scratch with `POST /api/agents` / `flow_create_agent`. Custom agent records still use the standard Agent fields and are persisted in the `agents` table.

## Security

Template commands are validated at import time and reject obvious destructive or injection-oriented patterns such as `rm -rf`, `sudo`, shell backticks, `| bash`, arithmetic command substitution, and writes to `/dev`. Template working directories reject `..` path traversal.

Keep `env_allowlist` minimal. Include only values the subprocess needs, such as Flow connection settings and provider credentials. Avoid passing broad host environment variables unless the command requires them.

Command validation is a baseline guard, not a substitute for operator review. Treat template overrides and custom scripts as privileged configuration and assign the narrowest API key role that can complete the work.
