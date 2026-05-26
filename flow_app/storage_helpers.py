"""Typed access helpers for legacy storage formats.

These helpers wrap raw string/integer stored values and provide
validated, normalized Python types. They should be used instead
of direct attribute access for fields stored as comma-separated
strings, JSON text, or integer booleans.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import Agent, AutomationRule, TaskHandoff, WebhookConfig

_LOGGER = logging.getLogger(__name__)


def get_bool_field(value: int | bool | None) -> bool:
    """Convert an INTEGER boolean field (0/1) to Python bool."""
    if value is None:
        return False
    return bool(value)


def get_comma_list(value: str | None) -> list[str]:
    """Parse a comma-separated string into a list of stripped, non-empty items."""
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def get_json_list(value: str | None) -> list[str]:
    """Parse a JSON-list text field into a list of strings.

    Malformed JSON logs a warning and returns an empty list.
    Non-list JSON values also return an empty list.
    """
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        _LOGGER.warning("Malformed JSON list in stored data, returning empty list: %r", value[:100])
        return []
    if isinstance(parsed, list):
        return [str(item) for item in parsed]
    _LOGGER.warning("Non-list JSON value in stored data, returning empty list: %r", value[:100])
    return []


def set_comma_list(items: list[str]) -> str:
    """Join a list of strings into a comma-separated string for storage."""
    return ",".join(item.strip() for item in items if item.strip())


def set_json_list(items: list[str]) -> str:
    """Serialize a list of strings into a JSON text field for storage."""
    return json.dumps(items, separators=(",", ":"))


def get_agent_capabilities(agent: Agent) -> list[str]:
    """Get agent capabilities as a parsed list."""
    return get_comma_list(agent.capabilities)


def get_agent_dispatch_statuses(agent: Agent) -> list[str]:
    """Get agent dispatch statuses as a parsed list."""
    return get_comma_list(agent.dispatch_statuses)


def get_webhook_events(config: WebhookConfig) -> list[str]:
    """Get webhook config events as a parsed list."""
    return get_comma_list(config.events)


def get_automation_events(rule: AutomationRule) -> list[str]:
    """Get automation rule events as a parsed list."""
    return get_comma_list(getattr(rule, "events", None))


def get_handoff_json_field(handoff: TaskHandoff, field: str) -> list[str]:
    """Get a JSON-list field from a task handoff."""
    return get_json_list(getattr(handoff, field, None))


def get_active_bool(value: int | bool | None) -> bool:
    """Convert an 'active' INTEGER field to bool. Defaults to True for None."""
    if value is None:
        return True
    return bool(value)
