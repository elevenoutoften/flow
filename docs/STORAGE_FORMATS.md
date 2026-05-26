# Storage Format Staging Plan

This document tracks the planned migration from legacy storage formats to typed, validated storage.

## Current State (Stage 0)

| Field | Table | Current Format | Risk | Notes |
|---|---|---|---|---|
| `human_required` | tasks | INTEGER (0/1) | Low | Python `bool()` handles 0/1 correctly |
| `active` | webhook_configs | INTEGER (0/1) | Low | Only compared to 1 |
| `secret_encrypted` | webhook_configs | INTEGER (0/1) | Low | Only compared to 1 |
| `capabilities` | agents | TEXT (comma-sep) | Medium | Parsed by `_split_comma_list` |
| `capabilities` | agent_runs | TEXT (comma-sep) | Medium | Parsed by `_split_comma_list` |
| `dispatch_statuses` | agents | TEXT (comma-sep) | Medium | Parsed by `_split_comma_list` |
| `events` | webhook_configs | TEXT (comma-sep) | Medium | Parsed by `_split_comma_list` |
| `events` | automation_rules | TEXT (comma-sep) | Medium | Parsed by `_split_comma_list` |
| `changed_files` | task_handoffs | TEXT (JSON list) | Medium | Parsed by `_load_json_list` |
| `commands_run` | task_handoffs | TEXT (JSON list) | Medium | Parsed by `_load_json_list` |
| `tests_run` | task_handoffs | TEXT (JSON list) | Medium | Parsed by `_load_json_list` |
| `artifacts` | task_handoffs | TEXT (JSON list) | Medium | Parsed by `_load_json_list` |
| `attempted_but_failed` | task_handoffs | TEXT (JSON list) | Medium | Parsed by `_load_json_list` |
| `version` | automation_rules | TEXT (comma-sep) | Low | Simple version tag |

## Stage 1: Typed Access Layer (Current)

Wrap all legacy-format fields with typed access helpers that validate and normalize on read:

- `get_agent_capabilities(agent) -> list[str]` — wraps `_split_comma_list(agent.capabilities)`
- `get_agent_dispatch_statuses(agent) -> list[str]` — wraps `_split_comma_list(agent.dispatch_statuses)`
- `get_webhook_events(config) -> list[str]` — wraps `_split_comma_list(config.events)`
- `get_automation_events(rule) -> list[str]` — wraps `_split_comma_list(rule.events)`
- `get_handoff_list_field(handoff, field) -> list[str]` — wraps `_load_json_list(getattr(handoff, field))`
- `get_bool_field(value) -> bool` — wraps `bool(value)` for INTEGER boolean fields

These helpers are the ONLY way to read these fields. Direct attribute access in route/service code should use these helpers instead of raw `_split_comma_list` or `_load_json_list`.

## Stage 2: Write-Side Validation (Planned)

Add validation on write so malformed data cannot be persisted:

- `set_agent_capabilities(agent, values: list[str])` — joins and writes
- `set_webhook_events(config, values: list[str])` — validates against `WEBHOOK_EVENTS`
- `set_automation_events(rule, values: list[str])` — validates against known event names
- `set_handoff_list_field(handoff, field, values: list[str])` — JSON-encodes and writes

## Stage 3: Column Migration (Future)

Migrate TEXT columns to native SQLite JSON or separate junction tables. This is a breaking change that requires a migration script and is deferred until Stage 1 and 2 are proven stable.
