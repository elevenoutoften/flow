# Automation Rules

Automation rules match task lifecycle events and return configured actions for downstream dispatchers or integrations.

## Triggers

- `task_created`
- `task_moved`
- `task_claimed`
- `task_completed`
- `task_blocked`
- `cron`

## Conditions

Conditions are a JSON array. All conditions must match.

```json
[
  {"field": "status", "operator": "eq", "value": "todo"},
  {"field": "priority", "operator": "gte", "value": 80}
]
```

Supported fields: `status`, `project`, `priority`, `assignee`, `assignee_type`, `human_required`, `title`.

Supported operators: `eq`, `ne`, `in`, `not_in`, `contains`, `gt`, `lt`, `gte`, `lte`, `exists`.

## Actions

Actions are stored as a JSON array and returned unchanged when a rule matches. Flow validates JSON shape but does not execute actions directly.

Example:

```json
[
  {"type": "dispatch", "agent_capability": "backend"},
  {"type": "notify", "channel": "review"}
]
```

## REST API

- `GET /api/automation-rules`
- `GET /api/automation-rules/{rule_id}`
- `POST /api/automation-rules`
- `PATCH /api/automation-rules/{rule_id}`
- `POST /api/automation-rules/evaluate`

Evaluate payload:

```json
{
  "trigger": "task_created",
  "task_id": "flow_000001",
  "data": {"priority": 90}
}
```

## MCP Tools

- `flow_list_automation_rules`
- `flow_get_automation_rule`
- `flow_create_automation_rule`
- `flow_update_automation_rule`
- `flow_evaluate_rules`

## Examples

Auto-dispatch high priority backend work:

```json
{
  "name": "Auto-dispatch backend",
  "trigger": "task_created",
  "priority": 100,
  "conditions": "[{\"field\":\"project\",\"operator\":\"eq\",\"value\":\"backend\"},{\"field\":\"priority\",\"operator\":\"gte\",\"value\":80}]",
  "actions": "[{\"type\":\"dispatch\",\"agent_capability\":\"backend\"}]"
}
```

Assign review notifications:

```json
{
  "name": "Review notification",
  "trigger": "task_moved",
  "conditions": "[{\"field\":\"status\",\"operator\":\"eq\",\"value\":\"review\"}]",
  "actions": "[{\"type\":\"notify\",\"channel\":\"review\"}]"
}
```

Stale alert from a cron evaluator:

```json
{
  "name": "Stale doing alert",
  "trigger": "cron",
  "trigger_config": "{\"interval_seconds\":3600}",
  "conditions": "[{\"field\":\"status\",\"operator\":\"eq\",\"value\":\"doing\"}]",
  "actions": "[{\"type\":\"notify\",\"channel\":\"stale-tasks\"}]"
}
```

Completion notification:

```json
{
  "name": "Completion notification",
  "trigger": "task_completed",
  "actions": "[{\"type\":\"notify\",\"channel\":\"done\"}]"
}
```
