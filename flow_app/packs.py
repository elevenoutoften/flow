"""Automation pack export/import logic.

Packs are JSON payloads containing automation rules, agents, webhook configs,
and notification channel metadata — with secrets redacted on export and
validated on import.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Agent, AutomationRule, Runner, WebhookConfig
from .repository import (
    create_agent,
    create_automation_rule,
    create_webhook_config,
    get_agent_by_name,
    list_agents,
    list_automation_rules,
    list_runners,
    list_webhook_configs,
    _redact_automation_actions,
    update_agent,
    update_automation_rule,
    update_webhook_config,
)
from .schemas import (
    AgentCreate,
    AgentUpdate,
    AutomationRuleCreate,
    AutomationRuleUpdate,
    WebhookConfigCreate,
)
from .secrets_resolver import redact_secret


PACK_SCHEMA_VERSION = 1
VALID_CONFLICT_POLICIES = {"update", "skip", "error"}

logger = logging.getLogger(__name__)


class PackImportConflict(Exception):
    """Raised when a pack import collision is rejected by conflict policy."""

    def __init__(self, section: str, name: str) -> None:
        self.section = section
        self.name = name
        super().__init__(f"{section[:-1]} '{name}' already exists")

# Fields that may contain secrets and must be redacted on export.
_SECRET_FIELDS: dict[str, list[str]] = {
    "rules": ["actions"],
    "webhook_configs": ["secret"],
    "notification_configs": ["api_key", "bot_token", "webhook_url"],
    "runners": ["api_key_ref"],
}


def export_pack(db: Session) -> dict[str, Any]:
    """Export an automation pack with secrets redacted."""
    rules = [
        _redact_entity(_serialize_rule_raw(r), "rules") for r in list_automation_rules(db, limit=10000)
    ]
    agents = [
        _redact_entity(_serialize_agent_raw(a), "agents") for a in list_agents(db)
    ]
    webhooks = [
        _redact_entity(_serialize_webhook_raw(w), "webhook_configs") for w in list_webhook_configs(db)
    ]
    runners = [
        _redact_entity(_serialize_runner_raw(r), "runners") for r in list_runners(db, limit=10000)
    ]
    notification_configs = _export_notification_configs()

    return {
        "schema_version": PACK_SCHEMA_VERSION,
        "name": "flow-pack",
        "description": "Exported automation pack",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "rules": rules,
        "agents": agents,
        "webhook_configs": webhooks,
        "runners": runners,
        "notification_configs": notification_configs,
    }


def _redact_entity(entity: dict, section: str) -> dict:
    """Redact secret fields in an entity dict."""
    secret_fields = _SECRET_FIELDS.get(section, [])
    for field in secret_fields:
        if field not in entity or not entity[field]:
            continue
        if section == "rules" and field == "actions":
            entity[field] = _redact_automation_actions(entity[field])
            continue
        entity[field] = redact_secret(str(entity[field]))
    return entity


def _export_notification_configs() -> list[dict]:
    """Export notification channel config from settings (secrets redacted)."""
    from .config import get_settings

    settings = get_settings()
    configs = []
    if settings.telegram_bot_token or settings.telegram_chat_id:
        configs.append({
            "channel": "telegram",
            "bot_token": redact_secret(settings.telegram_bot_token),
            "chat_id": settings.telegram_chat_id,
        })
    if settings.discord_webhook_url:
        configs.append({
            "channel": "discord",
            "webhook_url": redact_secret(settings.discord_webhook_url),
        })
    return configs


def validate_pack(pack: dict) -> list[str]:
    """Validate pack structure. Returns list of error messages (empty if valid)."""
    errors: list[str] = []

    if pack.get("schema_version") != PACK_SCHEMA_VERSION:
        errors.append(f"Unsupported schema_version: expected {PACK_SCHEMA_VERSION}, got {pack.get('schema_version')}")
        return errors  # Can't validate further with wrong version

    for section in ("rules", "agents", "webhook_configs", "notification_configs"):
        if section not in pack:
            errors.append(f"Missing required section: {section}")
            continue
        if not isinstance(pack[section], list):
            errors.append(f"Section '{section}' must be a list")
            continue

    # Validate individual entities
    for i, rule in enumerate(pack.get("rules", [])):
        if not isinstance(rule, dict):
            errors.append(f"rules[{i}]: must be an object")
        elif not rule.get("name"):
            errors.append(f"rules[{i}]: missing 'name'")

    for i, agent in enumerate(pack.get("agents", [])):
        if not isinstance(agent, dict):
            errors.append(f"agents[{i}]: must be an object")
        elif not agent.get("name"):
            errors.append(f"agents[{i}]: missing 'name'")

    for i, wh in enumerate(pack.get("webhook_configs", [])):
        if not isinstance(wh, dict):
            errors.append(f"webhook_configs[{i}]: must be an object")
        elif not wh.get("name"):
            errors.append(f"webhook_configs[{i}]: missing 'name'")

    return errors


def import_pack(
    db: Session,
    pack: dict,
    dry_run: bool = False,
    conflict_policy: str = "skip",
) -> dict[str, Any]:
    """Import a pack, upserting by name. Returns summary."""
    errors = validate_pack(pack)
    if conflict_policy not in VALID_CONFLICT_POLICIES:
        errors.append(
            f"Invalid conflict_policy: expected one of {sorted(VALID_CONFLICT_POLICIES)}, got {conflict_policy!r}"
        )
    if errors:
        return {"imported": {}, "skipped": [], "errors": errors}

    imported: dict[str, int] = {"rules": 0, "agents": 0, "webhook_configs": 0, "notification_configs": 0}
    skipped: list[str] = []

    entity_errors = _validate_pack_entities(db, pack)
    if entity_errors:
        return {"imported": {}, "skipped": [], "errors": entity_errors}

    if dry_run:
        for section in ("rules", "agents", "webhook_configs", "notification_configs"):
            entities = pack.get(section, [])
            for entity in entities:
                if isinstance(entity, dict) and entity.get("name"):
                    imported[section] = imported.get(section, 0) + 1
        return {"imported": imported, "skipped": skipped, "errors": []}

    # Import rules — upsert by name
    for rule_data in pack.get("rules", []):
        if not isinstance(rule_data, dict) or not rule_data.get("name"):
            skipped.append("rule: missing name")
            continue
        name = rule_data["name"]
        existing = _get_automation_rule_by_name(db, name)
        if existing and _handle_import_conflict("rules", name, conflict_policy, skipped):
            continue
        try:
            if existing:
                update_automation_rule(db, existing, AutomationRuleUpdate(**_pack_rule_updates(rule_data)))
                imported["rules"] += 1
            else:
                create_automation_rule(db, AutomationRuleCreate(**_pack_rule_create(rule_data)))
                imported["rules"] += 1
        except Exception as exc:
            skipped.append(f"rule '{name}': {exc}")

    # Import agents — upsert by name
    for agent_data in pack.get("agents", []):
        if not isinstance(agent_data, dict) or not agent_data.get("name"):
            skipped.append("agent: missing name")
            continue
        name = agent_data["name"]
        existing = get_agent_by_name(db, name)
        if existing and _handle_import_conflict("agents", name, conflict_policy, skipped):
            continue
        try:
            if existing:
                update_agent(db, existing, AgentUpdate(**_pack_agent_updates(agent_data)))
                imported["agents"] += 1
            else:
                create_agent(db, AgentCreate(**_pack_agent_create(agent_data)))
                imported["agents"] += 1
        except Exception as exc:
            skipped.append(f"agent '{name}': {exc}")

    # Import webhook configs — upsert by name
    for wh_data in pack.get("webhook_configs", []):
        if not isinstance(wh_data, dict) or not wh_data.get("name"):
            skipped.append("webhook_config: missing name")
            continue
        name = wh_data["name"]
        existing = _get_webhook_config_by_name(db, name)
        if existing and _handle_import_conflict("webhook_configs", name, conflict_policy, skipped):
            continue
        try:
            if existing:
                update_webhook_config(db, existing, _pack_webhook_updates(wh_data))
                imported["webhook_configs"] += 1
            else:
                create_payload = _pack_webhook_create(wh_data)
                create_webhook_config(
                    db,
                    name=create_payload["name"],
                    url=create_payload["url"],
                    events=create_payload["events"],
                    project=create_payload["project"],
                    max_retries=create_payload["max_retries"],
                    retry_backoff_seconds=create_payload["retry_backoff_seconds"],
                )
                imported["webhook_configs"] += 1
        except Exception as exc:
            skipped.append(f"webhook_config '{name}': {exc}")

    # Notification configs are settings-level, skip actual import
    notif_count = len(pack.get("notification_configs", []))
    if notif_count:
        imported["notification_configs"] = notif_count
        skipped.append("notification_configs: set via environment variables, not stored in DB")

    db.commit()
    return {"imported": imported, "skipped": skipped, "errors": []}


# --- Internal helpers for lookby-name that don't exist in repo yet ---

def _handle_import_conflict(section: str, name: str, conflict_policy: str, skipped: list[str]) -> bool:
    if conflict_policy == "update":
        return False
    if conflict_policy == "error":
        raise PackImportConflict(section, name)
    message = f"{section[:-1]} '{name}': skipped existing entity"
    logger.warning("Skipping existing pack import entity: section=%s name=%s", section, name)
    skipped.append(message)
    return True


def _validate_pack_entities(db: Session, pack: dict) -> list[str]:
    errors: list[str] = []

    for rule_data in pack.get("rules", []):
        if not isinstance(rule_data, dict) or not rule_data.get("name"):
            continue
        name = rule_data["name"]
        existing = _get_automation_rule_by_name(db, name)
        try:
            if existing:
                AutomationRuleUpdate(**_pack_rule_updates(rule_data))
            else:
                AutomationRuleCreate(**_pack_rule_create(rule_data))
        except ValidationError as exc:
            errors.append(f"rule '{name}': {exc}")

    for agent_data in pack.get("agents", []):
        if not isinstance(agent_data, dict) or not agent_data.get("name"):
            continue
        name = agent_data["name"]
        existing = get_agent_by_name(db, name)
        try:
            if existing:
                AgentUpdate(**_pack_agent_updates(agent_data))
            else:
                AgentCreate(**_pack_agent_create(agent_data))
        except ValidationError as exc:
            errors.append(f"agent '{name}': {exc}")

    for wh_data in pack.get("webhook_configs", []):
        if not isinstance(wh_data, dict) or not wh_data.get("name"):
            continue
        name = wh_data["name"]
        existing = _get_webhook_config_by_name(db, name)
        try:
            if existing:
                _validate_pack_webhook_update(existing, wh_data)
            else:
                WebhookConfigCreate(**_pack_webhook_create(wh_data))
        except ValidationError as exc:
            errors.append(f"webhook_config '{name}': {exc}")

    return errors

def _get_automation_rule_by_name(db: Session, name: str) -> AutomationRule | None:
    return db.scalars(select(AutomationRule).where(AutomationRule.name == name)).first()


def _get_webhook_config_by_name(db: Session, name: str) -> WebhookConfig | None:
    return db.scalars(select(WebhookConfig).where(WebhookConfig.name == name)).first()


def _split_events(raw: str | list) -> list[str]:
    """Convert events field to list for create_webhook_config."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str) and raw.strip():
        return [e.strip() for e in raw.split(",") if e.strip()]
    return []


# --- Raw serialization helpers (dict, not Pydantic response) ---

def _serialize_rule_raw(rule: AutomationRule) -> dict:
    return {
        "id": rule.id,
        "name": rule.name,
        "description": rule.description,
        "enabled": bool(rule.enabled),
        "priority": rule.priority,
        "trigger": rule.trigger,
        "trigger_config": rule.trigger_config,
        "conditions": rule.conditions,
        "actions": rule.actions,
    }


def _serialize_agent_raw(agent: Agent) -> dict:
    return {
        "id": agent.id,
        "name": agent.name,
        "description": agent.description,
        "enabled": bool(agent.enabled),
        "agent_type": agent.agent_type,
        "capabilities": agent.capabilities,
        "command": agent.command,
        "command_allowlist": agent.command_allowlist,
        "env_allowlist": agent.env_allowlist,
        "working_directory": agent.working_directory,
        "max_concurrency": agent.max_concurrency,
        "heartbeat_timeout_seconds": agent.heartbeat_timeout_seconds,
        "stale_claim_timeout_seconds": agent.stale_claim_timeout_seconds,
        "dispatch_statuses": agent.dispatch_statuses,
    }


def _serialize_webhook_raw(config: WebhookConfig) -> dict:
    return {
        "id": config.id,
        "name": config.name,
        "url": config.url,
        "secret": config.secret,
        "events": config.events,
        "active": bool(config.active),
        "max_retries": config.max_retries,
        "retry_backoff_seconds": config.retry_backoff_seconds,
        "project": config.project,
    }


def _serialize_runner_raw(runner: Runner) -> dict:
    return {
        "id": runner.id,
        "name": runner.name,
        "description": runner.description,
        "enabled": bool(runner.enabled),
        "runner_type": runner.runner_type,
        "capabilities": runner.capabilities,
        "agent_names": runner.agent_names,
        "lease_duration_seconds": runner.lease_duration_seconds,
        "heartbeat_interval_seconds": runner.heartbeat_interval_seconds,
        "max_concurrent_leases": runner.max_concurrent_leases,
        "api_key_ref": runner.api_key_ref,
        "last_seen_at": runner.last_seen_at.isoformat() if runner.last_seen_at else None,
        "status": runner.status,
    }


# --- Pack → create/update dict adapters ---

def _pack_rule_create(data: dict) -> dict:
    return {
        "name": data["name"],
        "description": data.get("description", ""),
        "enabled": data.get("enabled", True),
        "priority": data.get("priority", 50),
        "trigger": data.get("trigger", "task_created"),
        "trigger_config": data.get("trigger_config", ""),
        "conditions": data.get("conditions", "[]"),
        "actions": data.get("actions", "[]"),
    }


def _pack_rule_updates(data: dict) -> dict:
    updates = {}
    for field in ("description", "enabled", "priority", "trigger", "trigger_config", "conditions", "actions"):
        if field in data:
            updates[field] = data[field]
    return updates


def _pack_agent_create(data: dict) -> dict:
    return {
        "name": data["name"],
        "description": data.get("description", ""),
        "enabled": data.get("enabled", True),
        "agent_type": data.get("agent_type", "cli"),
        "capabilities": data.get("capabilities", ""),
        "command": data.get("command", ""),
        "command_allowlist": data.get("command_allowlist", ""),
        "env_allowlist": data.get("env_allowlist", ""),
        "working_directory": data.get("working_directory", ""),
        "max_concurrency": data.get("max_concurrency", 1),
        "heartbeat_timeout_seconds": data.get("heartbeat_timeout_seconds", 300),
        "stale_claim_timeout_seconds": data.get("stale_claim_timeout_seconds", 600),
        "dispatch_statuses": data.get("dispatch_statuses", "backlog,todo"),
    }


def _pack_agent_updates(data: dict) -> dict:
    updates = {}
    for field in (
        "description", "enabled", "agent_type", "capabilities", "command",
        "command_allowlist", "env_allowlist", "working_directory",
        "max_concurrency", "heartbeat_timeout_seconds", "stale_claim_timeout_seconds",
        "dispatch_statuses",
    ):
        if field in data:
            updates[field] = data[field]
    return updates


def _pack_webhook_updates(data: dict) -> dict:
    updates = {}
    for field in ("url", "secret", "events", "active", "max_retries", "retry_backoff_seconds", "project"):
        if field in data:
            updates[field] = data[field]
    return updates


def _pack_webhook_create(data: dict) -> dict:
    return {
        "name": data["name"],
        "url": data.get("url", ""),
        "events": _split_events(data.get("events", "")),
        "project": data.get("project", "*"),
        "max_retries": data.get("max_retries", 3),
        "retry_backoff_seconds": data.get("retry_backoff_seconds", 60),
    }


def _validate_pack_webhook_update(existing: WebhookConfig, data: dict) -> None:
    updates = _pack_webhook_updates(data)
    create_payload = {
        "name": existing.name,
        "url": existing.url,
        "events": _split_events(existing.events),
        "project": existing.project,
        "max_retries": existing.max_retries,
        "retry_backoff_seconds": existing.retry_backoff_seconds,
    }
    for field, value in updates.items():
        if field == "events":
            create_payload[field] = _split_events(value)
        elif field in create_payload:
            create_payload[field] = value
    WebhookConfigCreate(**create_payload)
