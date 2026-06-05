"""Recurring task template scheduler - materialization and cadence parsing."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from .models import RecurringTaskTemplate
from .repository import add_note, create_task, list_recurring_task_templates, update_recurring_task_template
from .rules_engine import emit_event
from .schemas import RecurringTaskTemplateUpdate, TaskCreate

_LOGGER = logging.getLogger(__name__)

NAMED_CADENCES = {
    "daily": timedelta(days=1),
    "weekly": timedelta(weeks=1),
    "biweekly": timedelta(weeks=2),
    "monthly": timedelta(days=30),
    "quarterly": timedelta(days=90),
    "yearly": timedelta(days=365),
}

INTERVAL_CADENCE_PATTERN = re.compile(r"(\d+)\s*([mhdw])")


def parse_cadence(cadence: str) -> timedelta:
    """Parse a cadence string into a timedelta."""
    cadence = cadence.strip().lower()
    if cadence in NAMED_CADENCES:
        return NAMED_CADENCES[cadence]

    match = INTERVAL_CADENCE_PATTERN.fullmatch(cadence)
    if match:
        value = int(match.group(1))
        unit = match.group(2)
        if unit == "m":
            return timedelta(minutes=value)
        if unit == "h":
            return timedelta(hours=value)
        if unit == "d":
            return timedelta(days=value)
        if unit == "w":
            return timedelta(weeks=value)

    _LOGGER.warning("Unrecognized cadence '%s', defaulting to 1 day", cadence)
    return timedelta(days=1)


def validate_cadence(cadence: str) -> str:
    """Validate a cadence string for user-provided template payloads."""
    cleaned = cadence.strip().lower()
    if cleaned in NAMED_CADENCES or INTERVAL_CADENCE_PATTERN.fullmatch(cleaned):
        return cleaned
    valid_names = ", ".join(NAMED_CADENCES)
    raise ValueError(
        f"Invalid cadence '{cadence}'. Use a named cadence ({valid_names}) "
        "or an interval like '15m', '2h', '1d', '3w'."
    )


def compute_next_run(cadence: str, after: datetime) -> datetime:
    """Compute the next run_at timestamp after the given datetime."""
    return after + parse_cadence(cadence)


@dataclass
class MaterializationResult:
    """Result of a single template materialization."""

    template_id: str
    task_id: str | None = None
    skipped: bool = False
    skip_reason: str = ""


@dataclass
class SchedulerResult:
    """Aggregate result of a scheduler pass."""

    materialized: int = 0
    skipped: int = 0
    dry_run: bool = False
    details: list[MaterializationResult] = field(default_factory=list)


def materialize_due_templates(
    session: Session,
    now: datetime | None = None,
    dry_run: bool = False,
) -> SchedulerResult:
    """Materialize all due recurring task templates into tasks."""
    now = _ensure_aware(now or datetime.now(timezone.utc))
    result = SchedulerResult(dry_run=dry_run)
    templates = list_recurring_task_templates(session, enabled_only=True, limit=10000, offset=0)

    for template in templates:
        next_run_at = _ensure_aware(template.next_run_at)
        if next_run_at > now:
            continue
        if dry_run:
            materialized = MaterializationResult(template_id=template.id, skipped=True, skip_reason="dry_run")
            result.skipped += 1
        else:
            materialized = _materialize_one(session, template, now)
            if materialized.task_id:
                result.materialized += 1
            else:
                result.skipped += 1
        result.details.append(materialized)

    return result


def _materialize_one(session: Session, template: RecurringTaskTemplate, now: datetime) -> MaterializationResult:
    """Materialize a single template into a task and advance next_run_at."""
    try:
        task = create_task(
            session,
            TaskCreate(
                title=template.title,
                project=template.project,
                description=_task_description(template),
                acceptance_criteria=template.acceptance_criteria or "",
                priority=template.priority,
                status=template.status,
                complexity=template.complexity,
                impact=template.impact,
                effort=template.effort,
                risk=template.risk,
            ),
        )
        session.flush()
        emit_event(session, "task_created", data={"task_id": task.id, "project": task.project})

        update_recurring_task_template(
            session,
            template,
            RecurringTaskTemplateUpdate(next_run_at=compute_next_run(template.cadence, now)),
        )
        add_note(
            session,
            task,
            f"Created from recurring template {template.id} ({template.name})",
            author="scheduler",
        )
        session.commit()
        return MaterializationResult(template_id=template.id, task_id=task.id)
    except Exception as exc:
        session.rollback()
        _LOGGER.exception("Failed to materialize template %s: %s", template.id, exc)
        return MaterializationResult(template_id=template.id, skipped=True, skip_reason=str(exc))


def _task_description(template: RecurringTaskTemplate) -> str:
    source = f"Created from recurring template {template.id} ({template.name})."
    description = (template.description or "").strip()
    if not description:
        return source
    return f"{description}\n\n{source}"


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value
