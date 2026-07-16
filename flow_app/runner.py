"""Unified automation runner for Flow."""
from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
import json
import logging
import os
import time
from collections.abc import Callable
from datetime import datetime

from sqlalchemy import select, update as sqlalchemy_update
from sqlalchemy.orm import Session

from .cron import cron_field_matches, cron_string_matches
from .database import build_engine, build_session_factory, default_database_url
from .dispatcher import DispatchError, _next_capable_task, complete_run, dispatch_one, stale_recovery
from .main import ensure_compatible_schema
from .models import Agent, AutomationRule, Task, utcnow
from .repository import serialize_task, task_query
from .realtime import publish_board_event
from .rules_engine import CONDITION_FIELDS, MatchResult, emit_event, evaluate_conditions, execute_actions, validate_conditions
from .storage_helpers import get_comma_list
from .webhook_cli import run_deliveries

logger = logging.getLogger("flow.runner")


@dataclass(frozen=True)
class RunnerConfig:
    profiles: list[str]
    interval: float = 30.0
    base_url: str = "http://127.0.0.1:8100"
    api_key: str = ""
    database_url: str = ""
    dry_run: bool = False


@dataclass
class PassResult:
    dispatched: int = 0
    stale_recovered: int = 0
    cron_matches: int = 0
    recurring_materialized: int = 0
    recurring_skipped: int = 0
    webhook_deliveries: int = 0


@dataclass
class CronMatchResult:
    rule_id: str
    rule_name: str
    task_id: str | None
    matched: bool
    actions: list[dict]
    skip_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "task_id": self.task_id,
            "matched_conditions": self.matched,
            "actions": self.actions,
            "skip_reason": self.skip_reason,
        }


def load_runner_config(*, require_profiles: bool = True) -> RunnerConfig:
    profiles = _parse_profiles(os.environ.get("FLOW_RUNNER_PROFILES", ""))
    if require_profiles and not profiles:
        raise ValueError("FLOW_RUNNER_PROFILES is required for runner loop mode.")
    return RunnerConfig(
        profiles=profiles,
        interval=_env_float("FLOW_RUNNER_INTERVAL", 30.0),
        base_url=_env("FLOW_BASE_URL", "http://127.0.0.1:8100"),
        api_key=_env("FLOW_API_KEY", ""),
        database_url=_env("FLOW_DATABASE_URL", default_database_url()),
        dry_run=_env_bool("FLOW_RUNNER_DRY_RUN", False),
    )


def run_pass(
    config: RunnerConfig,
    session: Session,
    session_factory: Callable[[], Session],
    *,
    stale_recovery_only: bool = False,
) -> PassResult:
    result = PassResult()

    if stale_recovery_only:
        result.stale_recovered = _run_stale_recovery(session, dry_run=config.dry_run)
        session.commit()
        return result

    dispatched_tasks = _run_dispatch(config, session, session_factory)
    result.dispatched = len(dispatched_tasks)
    session.commit()
    for task in dispatched_tasks:
        publish_board_event("task_claimed", task)

    result.stale_recovered = _run_stale_recovery(session, dry_run=config.dry_run)
    session.commit()

    result.cron_matches = _run_cron_rules(session, dry_run=config.dry_run)
    session.commit()

    sched_result = _run_recurring_templates(session, dry_run=config.dry_run)
    result.recurring_materialized = sched_result.materialized
    result.recurring_skipped = sched_result.skipped
    session.commit()

    result.webhook_deliveries = run_deliveries(dry_run=config.dry_run)
    logger.info("Webhook deliveries processed: %s", result.webhook_deliveries)
    return result


def run_loop(config: RunnerConfig) -> None:
    engine = build_engine(config.database_url)
    ensure_compatible_schema(engine)
    session_factory = build_session_factory(engine)
    try:
        while True:
            with session_factory() as session:
                result = run_pass(config, session, session_factory)
            _log_pass_summary(result)
            time.sleep(config.interval)
    except KeyboardInterrupt:
        logger.info("Shutting down")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Run Flow automation subsystems")
    parser.add_argument("--once", action="store_true", help="Run one pass and exit")
    parser.add_argument("--stale-recovery-only", action="store_true", help="Only run stale recovery")
    parser.add_argument("--dry-run", action="store_true", help="Log what would run without executing changes")
    parser.add_argument("--profiles", help="Comma-separated agent profile names")
    parser.add_argument("--interval", type=float, help="Seconds between runner passes")
    parser.add_argument("--base-url", help="Flow API base URL")
    parser.add_argument("--api-key", help="Flow API key for subprocess dispatch")
    args = parser.parse_args(argv)

    require_profiles = not args.once and not args.stale_recovery_only and not args.profiles
    try:
        config = load_runner_config(require_profiles=require_profiles)
    except ValueError as exc:
        parser.error(str(exc))

    profiles = _parse_profiles(args.profiles) if args.profiles is not None else config.profiles
    config = replace(
        config,
        profiles=profiles,
        interval=args.interval if args.interval is not None else config.interval,
        base_url=args.base_url if args.base_url is not None else config.base_url,
        api_key=args.api_key if args.api_key is not None else config.api_key,
        dry_run=args.dry_run or config.dry_run,
    )
    if not args.once and not args.stale_recovery_only and not config.profiles:
        parser.error("--profiles or FLOW_RUNNER_PROFILES is required for runner loop mode.")

    engine = build_engine(config.database_url)
    ensure_compatible_schema(engine)
    session_factory = build_session_factory(engine)

    if args.once:
        with session_factory() as session:
            result = run_pass(config, session, session_factory, stale_recovery_only=args.stale_recovery_only)
        _log_pass_summary(result)
        return 0

    if args.stale_recovery_only:
        with session_factory() as session:
            result = run_pass(config, session, session_factory, stale_recovery_only=True)
        _log_pass_summary(result)
        return 0

    run_loop(config)
    return 0


def _run_dispatch(config: RunnerConfig, session: Session, session_factory: Callable[[], Session]) -> list[Task]:
    dispatched: list[Task] = []
    for profile in config.profiles:
        agent = session.scalars(select(Agent).where(Agent.name == profile)).first()
        if agent is None:
            logger.warning("Agent profile not found: %s", profile)
            continue
        if not agent.enabled:
            logger.info("Skipping disabled agent profile: %s", profile)
            continue
        task = _next_capable_task(session, agent)
        if task is None:
            logger.debug("No dispatchable task for agent profile: %s", profile)
            continue
        if config.dry_run:
            logger.info("Would dispatch task %s to agent %s", task.id, agent.name)
            continue
        try:
            run = dispatch_one(
                session,
                agent,
                task,
                api_key=config.api_key,
                base_url=config.base_url,
                session_factory=session_factory,
            )
        except DispatchError as exc:
            logger.info("Skipping dispatch for agent %s: %s", agent.name, exc)
            continue
        dispatched.append(task)
        logger.info("Dispatched task %s to agent %s as run %s", task.id, agent.name, run.id)
    return dispatched


def _run_stale_recovery(session: Session, *, dry_run: bool) -> int:
    if dry_run:
        logger.info("Would run stale recovery")
        return 0
    recovered = stale_recovery(session)
    logger.info("Stale recovery recovered %s runs", len(recovered))
    return len(recovered)


def _run_cron_rules(session: Session, *, dry_run: bool) -> int:
    result = dry_run_automation_rules(session, trigger="cron", dry_run=dry_run)
    logger.info("Cron automation produced %s matches", len(result["matches"]))
    return len(result["matches"])


def dry_run_automation_rules(
    session: Session,
    *,
    trigger: str = "cron",
    rule_id: str | None = None,
    dry_run: bool = True,
) -> dict:
    rules = list(
        session.scalars(
            select(AutomationRule)
            .where(AutomationRule.enabled == 1)
            .where(AutomationRule.trigger == trigger)
            .order_by(AutomationRule.priority.desc())
        ).all()
    )
    if rule_id is not None:
        rules = [rule for rule in rules if rule.id == rule_id]

    evaluated_rules = 0
    matches: list[CronMatchResult] = []
    invalid_conditions: list[dict] = []
    now = utcnow()
    for rule in rules:
        if trigger == "cron" and not _cron_config_matches(rule.trigger_config, now):
            continue
        if not dry_run and trigger == "cron":
            # Atomic dedupe: only proceed if last_run_at is absent or before
            # the current minute. This prevents two runner instances (or an
            # embedded worker + external runner) from double-firing the same
            # cron rule in the same minute.
            minute_start = now.replace(second=0, microsecond=0)
            result = session.execute(
                sqlalchemy_update(AutomationRule)
                .where(
                    AutomationRule.id == rule.id,
                    AutomationRule.enabled == 1,
                )
                .where(
                    (AutomationRule.last_run_at.is_(None))
                    | (AutomationRule.last_run_at < minute_start)
                )
                .values(last_run_at=now)
            )
            if (result.rowcount or 0) == 0:
                continue
            session.expire(rule)
        evaluated_rules += 1
        conditions = _parse_rule_array(rule.conditions)
        if conditions is not None:
            condition_errors = validate_conditions(conditions)
            if condition_errors:
                invalid_conditions.append(
                    {
                        "rule_id": rule.id,
                        "rule_name": rule.name,
                        "errors": [
                            {"index": error.index, "field": error.field, "message": error.message}
                            for error in condition_errors
                        ],
                    }
                )
        rule_matches = _evaluate_scheduled_rule(session, rule, trigger=trigger, dry_run=dry_run)
        matches.extend(rule_matches)
        if not dry_run:
            all_succeeded = all(m.skip_reason == "" for m in rule_matches)
            if trigger == "cron":
                # Cron already stamped last_run_at atomically above.
                # If actions failed, clear it so the rule retries next pass.
                if not all_succeeded:
                    rule = session.get(AutomationRule, rule.id)
                    if rule:
                        rule.last_run_at = None
                        session.add(rule)
                        session.flush()
            else:
                # Non-cron: stamp last_run_at only if all actions succeeded.
                if all_succeeded:
                    rule.last_run_at = now
                    session.add(rule)
                    session.flush()

    return {
        "evaluated_rules": evaluated_rules,
        "matches": [match.to_dict() for match in matches],
        "invalid_conditions": invalid_conditions,
    }


def _evaluate_scheduled_rule(
    session: Session,
    rule: AutomationRule,
    *,
    trigger: str,
    dry_run: bool,
) -> list[CronMatchResult]:
    conditions = _parse_rule_array(rule.conditions)
    if conditions is None:
        return [CronMatchResult(rule.id, rule.name, None, False, [], "invalid_conditions")]
    actions = _parse_rule_array(rule.actions)
    if actions is None:
        return [CronMatchResult(rule.id, rule.name, None, False, [], "invalid_actions")]

    if not _conditions_reference_task_fields(conditions):
        if dry_run:
            matched = evaluate_conditions(conditions, {"rule_id": rule.id, "rule_name": rule.name})
            return [CronMatchResult(rule.id, rule.name, None, matched, actions)] if matched else []
        results = emit_event(session, trigger, data={"rule_id": rule.id, "rule_name": rule.name}, rule_id=rule.id)
        return [
            CronMatchResult(
                result["rule_id"],
                result["rule_name"],
                result.get("task_id"),
                True,
                result.get("actions", []),
            )
            for result in results
        ]

    rule_matches: list[CronMatchResult] = []
    for task in _tasks_for_rule(session, conditions):
        task_data = serialize_task(task).model_dump(mode="json")
        if not evaluate_conditions(conditions, task_data):
            continue
        rule_matches.append(CronMatchResult(rule.id, rule.name, task.id, True, actions))
        if dry_run:
            continue
        execute_actions(
            session,
            MatchResult(rule_id=rule.id, rule_name=rule.name, actions=actions, task_id=task.id),
            trigger=trigger,
            data={"rule_id": rule.id, "rule_name": rule.name},
        )
    return rule_matches


def _run_recurring_templates(session: Session, *, dry_run: bool):
    """Materialize due recurring task templates."""
    from .scheduler import materialize_due_templates

    result = materialize_due_templates(session, dry_run=dry_run)
    logger.info(
        "Recurring templates materialized=%s skipped=%s dry_run=%s",
        result.materialized,
        result.skipped,
        result.dry_run,
    )
    return result


def _parse_rule_array(raw: str | None) -> list[dict] | None:
    try:
        parsed = json.loads(raw or "[]")
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(parsed, list):
        return None
    return parsed


def _conditions_reference_task_fields(conditions: list[dict]) -> bool:
    return any(isinstance(condition, dict) and condition.get("field") in CONDITION_FIELDS for condition in conditions)


def _tasks_for_rule(session: Session, conditions: list[dict]) -> list[Task]:
    stmt = task_query()
    project = _project_eq_filter(conditions)
    if project:
        stmt = stmt.where(Task.project == project)
    return list(session.scalars(stmt).all())


def _project_eq_filter(conditions: list[dict]) -> str | None:
    for condition in conditions:
        if not isinstance(condition, dict):
            continue
        if condition.get("field") == "project" and condition.get("operator", "eq") == "eq":
            value = condition.get("value")
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _same_minute(left: datetime, right: datetime) -> bool:
    return (
        left.year == right.year
        and left.month == right.month
        and left.day == right.day
        and left.hour == right.hour
        and left.minute == right.minute
    )


def _cron_config_matches(trigger_config: str | None, now: datetime | None = None) -> bool:
    now = now or utcnow()
    if not trigger_config:
        return True
    try:
        config = json.loads(trigger_config)
    except (json.JSONDecodeError, TypeError):
        return True
    if not isinstance(config, dict):
        return True
    cron_expr = config.get("cron")
    if cron_expr:
        return _cron_string_matches(str(cron_expr), now)
    # Legacy format: {minute, hour, day_of_week} keys.
    # Convert Python weekday (Mon=0..Sun=6) to cron weekday (Sun=0..Sat=6)
    # for day_of_week to match standard cron semantics.
    cron_dow = (now.weekday() + 1) % 7
    return (
        _cron_field_matches(config.get("minute", "*"), now.minute)
        and _cron_field_matches(config.get("hour", "*"), now.hour)
        and _cron_field_matches_dow(config.get("day_of_week", "*"), cron_dow)
    )


def _cron_string_matches(expr: str, now: datetime) -> bool:
    return cron_string_matches(expr, now)


def _cron_field_matches(expression: object, value: int) -> bool:
    return cron_field_matches(expression, value)


def _cron_field_matches_dow(expression: object, value: int) -> bool:
    from .cron import cron_field_matches_dow
    return cron_field_matches_dow(expression, value)


def _log_pass_summary(result: PassResult) -> None:
    logger.info(
        "Runner pass complete: dispatched=%s stale_recovered=%s cron_matches=%s webhook_deliveries=%s",
        result.dispatched,
        result.stale_recovered,
        result.cron_matches,
        result.webhook_deliveries,
    )


def _parse_profiles(raw: str | None) -> list[str]:
    return get_comma_list(raw)


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip() or default


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        return default


if __name__ == "__main__":
    raise SystemExit(main())
