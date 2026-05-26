"""Automation rules engine - condition evaluation and action execution."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ApiKeyRole, AutomationRule, Task, WebhookDelivery
from .repository import (
    add_note,
    get_agent,
    get_agent_by_name,
    get_task,
    list_agents,
    serialize_task,
    update_task,
)
from .schemas import STATUSES, TaskUpdate
from .security import Actor, is_valid_transition
from .storage_helpers import get_agent_capabilities

CONDITION_FIELDS = {
    "status",
    "project",
    "priority",
    "assignee",
    "assignee_type",
    "human_required",
    "title",
    "latest_handoff",
}
CONDITION_OPERATORS = {"eq", "ne", "in", "not_in", "contains", "gt", "lt", "gte", "lte", "exists", "not_exists"}
TRIGGERS = {"task_created", "task_moved", "task_claimed", "task_completed", "task_blocked", "cron"}


@dataclass
class MatchResult:
    rule_id: str
    rule_name: str
    actions: list[dict]
    task_id: str | None


@dataclass
class ActionResult:
    type: str
    success: bool
    message: str
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict:
        payload = {"type": self.type, "success": self.success, "message": self.message}
        if self.details:
            payload["details"] = self.details
        return payload


def evaluate_conditions(conditions: list[dict], task_data: dict) -> bool:
    """Evaluate condition objects against task data with AND logic."""
    for cond in conditions:
        field = cond.get("field", "")
        operator = cond.get("operator", "eq")
        value = cond.get("value")

        if field not in CONDITION_FIELDS:
            return False
        if operator not in CONDITION_OPERATORS:
            return False

        actual = task_data.get(field)

        if operator == "eq" and actual != value:
            return False
        if operator == "ne" and actual == value:
            return False
        if operator == "in" and actual not in (value if isinstance(value, list) else [value]):
            return False
        if operator == "not_in" and actual in (value if isinstance(value, list) else [value]):
            return False
        if operator == "contains" and not (
            isinstance(actual, str) and isinstance(value, str) and value.lower() in actual.lower()
        ):
            return False
        if operator == "gt" and (actual is None or actual <= value):
            return False
        if operator == "lt" and (actual is None or actual >= value):
            return False
        if operator == "gte" and (actual is None or actual < value):
            return False
        if operator == "lte" and (actual is None or actual > value):
            return False
        if operator == "exists" and actual is None:
            return False
        if operator == "not_exists" and actual is not None:
            return False
    return True


def match_rules(
    session: Session,
    trigger: str,
    task_id: str | None = None,
    data: dict | None = None,
    rule_id: str | None = None,
) -> list[MatchResult]:
    """Find all enabled rules matching the trigger and conditions."""
    query = session.query(AutomationRule).filter(AutomationRule.enabled == 1, AutomationRule.trigger == trigger)
    if rule_id is not None:
        query = query.filter(AutomationRule.id == rule_id)
    rules = query.order_by(AutomationRule.priority.desc()).all()

    results = []
    task_data = data or {}
    if task_id:
        task = get_task(session, task_id)
        if task:
            task_data = serialize_task(task).model_dump(mode="json")
            task_data.update(data or {})

    for rule in rules:
        try:
            conditions = json.loads(rule.conditions) if rule.conditions else []
        except (json.JSONDecodeError, TypeError):
            continue
        if evaluate_conditions(conditions, task_data):
            try:
                actions = json.loads(rule.actions) if rule.actions else []
            except (json.JSONDecodeError, TypeError):
                continue
            results.append(
                MatchResult(
                    rule_id=rule.id,
                    rule_name=rule.name,
                    actions=actions,
                    task_id=task_id,
                )
            )
    return results


def execute_actions(
    session: Session,
    match: MatchResult,
    *,
    trigger: str,
    actor: Actor | None = None,
    data: dict | None = None,
) -> list[dict]:
    """Execute actions for a matched rule and return serializable results."""
    results = []
    task = get_task(session, match.task_id) if match.task_id else None
    effective_actor = actor or _automation_actor()
    for action in match.actions:
        if not isinstance(action, dict):
            results.append(ActionResult("unknown", False, "Action must be an object.").to_dict())
            continue

        action_type = str(action.get("type") or "").strip()
        try:
            result = _execute_action(
                session,
                action_type,
                action,
                task=task,
                trigger=trigger,
                actor=effective_actor,
                data=data or {},
            )
        except Exception as exc:
            result = ActionResult(action_type or "unknown", False, str(exc))
        results.append(result.to_dict())

        if match.task_id:
            task = get_task(session, match.task_id)

    session.flush()
    return results


def _execute_action(
    session: Session,
    action_type: str,
    action: dict,
    *,
    task: Task | None,
    trigger: str,
    actor: Actor,
    data: dict,
) -> ActionResult:
    if action_type == "claim":
        return _execute_claim(session, task, action, actor)
    if action_type == "move":
        return _execute_move(session, task, action, actor)
    if action_type == "add_note":
        return _execute_add_note(session, task, action)
    if action_type in {"spawn", "dispatch"}:
        return _execute_spawn(session, task, action)
    if action_type == "notify":
        return _execute_notify(session, task, action)
    if action_type == "webhook":
        return _execute_webhook(session, task, action, trigger, data)
    return ActionResult(action_type or "unknown", False, f"Unsupported action type: {action_type or 'unknown'}.")


def _execute_claim(session: Session, task: Task | None, action: dict, actor: Actor) -> ActionResult:
    if task is None:
        return ActionResult("claim", False, "Task is required for claim action.")

    assignee = _first_present(action.get("assignee"), action.get("agent_name"), action.get("agent"), actor.name)
    if not assignee:
        return ActionResult("claim", False, "Assignee is required for claim action.")
    if task.assignee:
        return ActionResult("claim", False, f"Task is already claimed by {task.assignee}.")
    if task.status in {"backlog", "todo"}:
        if not is_valid_transition(actor, task.status, "doing"):
            return ActionResult(
                "claim",
                False,
                f"Role '{actor.role.value}' cannot move task from {task.status} to doing.",
            )
        task.status = "doing"
    task.assignee = assignee
    update_task(session, task, TaskUpdate())
    return ActionResult("claim", True, f"Task claimed by {assignee}.", {"assignee": assignee, "status": task.status})


def _execute_move(session: Session, task: Task | None, action: dict, actor: Actor) -> ActionResult:
    if task is None:
        return ActionResult("move", False, "Task is required for move action.")

    target_status = _first_present(action.get("status"), action.get("to_status"), action.get("target_status"))
    if not target_status:
        return ActionResult("move", False, "Target status is required for move action.")
    if target_status not in STATUSES:
        return ActionResult("move", False, f"Invalid target status: {target_status}.")
    if task.status == target_status:
        return ActionResult("move", True, f"Task is already {target_status}.", {"status": task.status})
    if not is_valid_transition(actor, task.status, target_status):
        return ActionResult(
            "move",
            False,
            f"Role '{actor.role.value}' cannot move task from {task.status} to {target_status}.",
        )

    old_status = task.status
    task.status = target_status
    update_task(session, task, TaskUpdate())
    return ActionResult(
        "move",
        True,
        f"Task moved from {old_status} to {target_status}.",
        {"from": old_status, "to": target_status},
    )


def _execute_add_note(session: Session, task: Task | None, action: dict) -> ActionResult:
    if task is None:
        return ActionResult("add_note", False, "Task is required for add_note action.")

    text = _first_present(action.get("text"), action.get("note"), action.get("body"), action.get("message"))
    if not text:
        return ActionResult("add_note", False, "Note text is required for add_note action.")
    author = _first_present(action.get("author"), "automation")
    if _has_note(task, text, author):
        return ActionResult("add_note", True, "Note already exists.", {"idempotent": True})
    note = add_note(session, task, text, author=author)
    return ActionResult("add_note", True, "Note added.", {"note_id": note.id})


def _execute_spawn(session: Session, task: Task | None, action: dict) -> ActionResult:
    if task is None:
        return ActionResult("spawn", False, "Task is required for spawn action.")

    agent = None
    agent_id = _first_present(action.get("agent_id"))
    agent_name = _first_present(action.get("agent_name"), action.get("agent"))
    if agent_id:
        agent = get_agent(session, agent_id)
    elif agent_name:
        agent = get_agent_by_name(session, agent_name)
    else:
        agent = _first_enabled_agent(session, _first_present(action.get("agent_capability"), action.get("capability")))

    if agent is None:
        return ActionResult("spawn", False, "Agent is required and must be registered.")
    if not agent.enabled:
        return ActionResult("spawn", False, f"Agent is disabled: {agent.name}.")
    if task.status == "review" and task.assignee and task.assignee != agent.name:
        task.assignee = None
        task.claimer_key_id = None
        update_task(session, task, TaskUpdate())

    from .dispatcher import DispatchError, dispatch_one

    try:
        run = dispatch_one(
            session,
            agent,
            task,
            api_key=_first_present(action.get("api_key")) or "",
            base_url=_first_present(action.get("base_url")) or "",
        )
    except DispatchError as exc:
        return ActionResult("spawn", False, str(exc), {"agent_id": agent.id})
    return ActionResult("spawn", True, f"Dispatched agent {agent.name}.", {"agent_id": agent.id, "run_id": run.id})


def _execute_notify(session: Session, task: Task | None, action: dict) -> ActionResult:
    if task is None:
        return ActionResult("notify", False, "Task is required for notify action.")

    channel = _first_present(action.get("channel"), "default")
    message = _first_present(action.get("message"), action.get("text")) or f"Notification sent to {channel}."
    body = f"[notification:{channel}] {message}"
    if _has_note(task, body, "automation"):
        return ActionResult("notify", True, "Notification already recorded.", {"channel": channel, "idempotent": True})
    note = add_note(session, task, body, author="automation")
    return ActionResult("notify", True, "Notification recorded.", {"channel": channel, "note_id": note.id})


def _execute_webhook(session: Session, task: Task | None, action: dict, trigger: str, data: dict) -> ActionResult:
    if task is None:
        return ActionResult("webhook", False, "Task is required for webhook action.")

    event_name = _first_present(action.get("event"), action.get("event_name"), trigger) or trigger
    from .webhooks import emit_event as emit_webhook_event

    before = _webhook_delivery_count(session, event_name, task.id)
    if before and action.get("idempotent", True):
        return ActionResult(
            "webhook",
            True,
            f"Webhook event already emitted: {event_name}.",
            {"event": event_name, "idempotent": True},
        )
    emit_webhook_event(session, event_name, task, {**data, **{k: v for k, v in action.items() if k != "type"}})
    created = _webhook_delivery_count(session, event_name, task.id) - before
    return ActionResult(
        "webhook",
        True,
        f"Webhook event emitted: {event_name}.",
        {"event": event_name, "deliveries_created": created},
    )


def _first_enabled_agent(session: Session, capability: str | None):
    for agent in list_agents(session, enabled_only=True):
        if capability is None:
            return agent
        capabilities = set(get_agent_capabilities(agent))
        if capability in capabilities:
            return agent
    return None


def _has_note(task: Task, body: str, author: str | None) -> bool:
    return any(note.body == body and note.author == author for note in task.notes)


def _webhook_delivery_count(session: Session, event_name: str, task_id: str) -> int:
    count = 0
    deliveries = session.scalars(select(WebhookDelivery).where(WebhookDelivery.event == event_name)).all()
    for delivery in deliveries:
        try:
            payload = json.loads(delivery.payload)
        except (json.JSONDecodeError, TypeError):
            continue
        if payload.get("task_id") == task_id:
            count += 1
    return count


def _first_present(*candidates: Any) -> str | None:
    for candidate in candidates:
        if candidate is None:
            continue
        value = str(candidate).strip()
        if value:
            return value
    return None


def _automation_actor() -> Actor:
    return Actor(name="automation", role=ApiKeyRole.admin, source="automation")


def emit_event(
    session: Session,
    trigger: str,
    task_id: str | None = None,
    data: dict | None = None,
    actor: Actor | None = None,
    rule_id: str | None = None,
) -> list[dict]:
    """Emit an event, execute matched rule actions, and return serializable results."""
    matches = match_rules(session, trigger, task_id, data, rule_id=rule_id)
    results = []
    for match in matches:
        rule = session.get(AutomationRule, match.rule_id)
        if rule:
            from .models import utcnow

            rule.last_run_at = utcnow()
            session.add(rule)
        action_results = execute_actions(session, match, trigger=trigger, actor=actor, data=data)
        results.append(
            {
                "rule_id": match.rule_id,
                "rule_name": match.rule_name,
                "actions": match.actions,
                "action_results": action_results,
                "task_id": match.task_id,
            }
        )
    session.flush()
    return results
