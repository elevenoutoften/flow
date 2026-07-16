# Automation Rules

## Source

| File | Role |
|------|------|
| `flow_app/rules_engine.py` | Condition evaluation, action execution |
| `flow_app/models.py:176-192` | `AutomationRule` model |
| `flow_app/routes/automation.py` | REST API router |
| `flow_app/services/automation.py` | Service layer |

## Model

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `id` | string (`rule_NNNNNN`) | auto | Primary key |
| `name` | string(180) | *(required)* | Rule name |
| `description` | text | `""` | Description |
| `enabled` | bool | `true` | Active flag |
| `priority` | int | `50` | Evaluation priority (higher = first) |
| `trigger` | string(60) | *(required)* | Event trigger |
| `trigger_config` | text (JSON) | `""` | Trigger-specific config (e.g., cron schedule) |
| `conditions` | text (JSON) | `""` | Condition array |
| `actions` | text (JSON) | `""` | Action array |
| `last_run_at` | datetime? | null | Last evaluation time |

## Triggers

| Trigger | When |
|---------|------|
| `task_created` | After a task is created |
| `task_moved` | After a task changes status |
| `task_claimed` | After a task is claimed |
| `task_completed` | After a task is marked done |
| `task_blocked` | When `human_required` changes to true |
| `cron` | Periodic evaluation by the runner |

## Conditions

Conditions are a JSON array. All conditions must match (AND logic).

```json
[
  {"field": "status", "operator": "eq", "value": "todo"},
  {"field": "priority", "operator": "gte", "value": 80}
]
```

**Supported fields:** `status`, `project`, `priority`, `assignee`, `assignee_type`, `human_required`, `title`, `latest_handoff`, `age_since_updated`, `age_since_created`, `age_since_claimed`

**Supported operators:** `eq`, `ne`, `in`, `not_in`, `contains`, `gt`, `lt`, `gte`, `lte`, `exists`, `not_exists`

## Actions

Actions are a JSON array returned when all conditions match. Flow validates JSON shape but does not execute all action types directly — some are consumed by the rules engine, others are returned for external dispatchers.

### Built-in Action Types

| Type | Description |
|------|-------------|
| `dispatch` | Dispatch an agent to the task |
| `notify` | Send notification via registered provider |
| `move` | Move task to a new status |
| `claim` | Claim the task on behalf of an agent |
| `add_note` | Add a note to the task |
| `webhook` | Trigger a registered webhook |

### Example Actions

```json
[
  {"type": "dispatch", "agent_capability": "backend"},
  {"type": "notify", "channel": "review"}
]
```

## Cron Trigger Config

```json
{
  "interval_seconds": 3600,
  "minute": "*/5",
  "hour": "*",
  "day_of_week": "*"
}
```

The runner evaluates cron rules each pass. `_cron_config_matches()` checks minute, hour, and day_of_week fields. Supports exact values and `*/N` divisors.

Cron rules with task conditions scan tasks and execute actions once per matching task. Age condition values are seconds since the task timestamp.

## Stale Task Policy Examples

### Notify when task is idle for 7 days

```json
{
  "name": "Stale task notification",
  "trigger": "cron",
  "trigger_config": "{\"minute\": \"0\", \"hour\": \"9\", \"day_of_week\": \"*\"}",
  "conditions": [
    {"field": "status", "operator": "in", "value": ["todo", "doing"]},
    {"field": "age_since_updated", "operator": "gt", "value": 604800}
  ],
  "actions": [
    {"type": "notify", "message": "Task {{task_id}} has not been updated in 7 days"}
  ]
}
```

### Add note when task has been in review for 3 days

```json
{
  "name": "Review stagnation alert",
  "trigger": "cron",
  "trigger_config": "{\"minute\": \"0\", \"hour\": \"9\", \"day_of_week\": \"*\"}",
  "conditions": [
    {"field": "status", "operator": "eq", "value": "review"},
    {"field": "age_since_updated", "operator": "gt", "value": 259200}
  ],
  "actions": [
    {"type": "add_note", "body": "This task has been in review for 3+ days without activity."}
  ]
}
```

### Escalate unclaimed tasks after 2 days

```json
{
  "name": "Unclaimed task escalation",
  "trigger": "cron",
  "trigger_config": "{\"minute\": \"*/30\", \"hour\": \"*\", \"day_of_week\": \"1-5\"}",
  "conditions": [
    {"field": "status", "operator": "eq", "value": "todo"},
    {"field": "assignee", "operator": "exists"},
    {"field": "age_since_created", "operator": "gt", "value": 172800}
  ],
  "actions": [
    {"type": "notify", "message": "Unclaimed task {{task_id}} is over 2 days old"}
  ]
}
```

## REST API

| Method | Path | Permission |
|--------|------|-----------|
| `GET` | `/api/automation-rules` | `rules:read` |
| `GET` | `/api/automation-rules/{id}` | `rules:read` |
| `POST` | `/api/automation-rules` | `rules:manage` |
| `PATCH` | `/api/automation-rules/{id}` | `rules:manage` |
| `POST` | `/api/automation-rules/evaluate` | `rules:evaluate` |
| `POST` | `/api/automation-rules/dry-run` | `rules:evaluate` |

## MCP Tools

| Tool | Permission |
|------|-----------|
| `flow_list_automation_rules` | `rules:read` |
| `flow_get_automation_rule` | `rules:read` |
| `flow_create_automation_rule` | `rules:manage` |
| `flow_update_automation_rule` | `rules:manage` |
| `flow_evaluate_rules` | `rules:evaluate` |
| `flow_dry_run_automation_rules` | `rules:evaluate` |

## Evaluation Flow

1. Event occurs (task created, moved, etc.).
2. `emit_event()` is called with trigger name and task data.
3. Rules matching the trigger are loaded, ordered by priority (descending).
4. For each enabled rule, conditions are evaluated against task data.
5. Matching rules produce action arrays.
6. Built-in actions (`dispatch`, `notify`, `move`, `claim`, `add_note`, `webhook`) are executed by the engine.
7. `last_run_at` is updated on matching rules.

## See Also

- [Dispatcher](Dispatcher.md) — agent dispatch from rules
- [Webhooks](Webhooks.md) — outbound event delivery
- [REST API](REST-API.md) — endpoint reference
