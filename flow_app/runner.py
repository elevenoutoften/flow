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

from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import build_engine, build_session_factory, default_database_url
from .dispatcher import DispatchError, _next_capable_task, complete_run, dispatch_one, stale_recovery
from .main import ensure_compatible_schema
from .models import Agent, AutomationRule, utcnow
from .rules_engine import emit_event
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
    webhook_deliveries: int = 0


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

    result.dispatched = _run_dispatch(config, session, session_factory)
    session.commit()

    result.stale_recovered = _run_stale_recovery(session, dry_run=config.dry_run)
    session.commit()

    result.cron_matches = _run_cron_rules(session, dry_run=config.dry_run)
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


def _run_dispatch(config: RunnerConfig, session: Session, session_factory: Callable[[], Session]) -> int:
    dispatched = 0
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
        dispatched += 1
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
    rules = list(
        session.scalars(
            select(AutomationRule)
            .where(AutomationRule.enabled == 1)
            .where(AutomationRule.trigger == "cron")
            .order_by(AutomationRule.priority.desc())
        ).all()
    )
    matches = 0
    now = utcnow()
    for rule in rules:
        if not _cron_config_matches(rule.trigger_config, now):
            continue
        if rule.last_run_at is not None and _same_minute(rule.last_run_at, now):
            continue
        if dry_run:
            logger.info("Would fire cron automation rule %s (%s)", rule.name, rule.id)
            continue
        results = _emit_single_cron_rule(session, rules, rule)
        matches += len(results)
    logger.info("Cron automation produced %s matches", matches)
    return matches


def _emit_single_cron_rule(session: Session, rules: list[AutomationRule], rule: AutomationRule) -> list[dict]:
    return emit_event(session, "cron", data={"rule_id": rule.id, "rule_name": rule.name}, rule_id=rule.id)


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
    return (
        _cron_field_matches(config.get("minute", "*"), now.minute)
        and _cron_field_matches(config.get("hour", "*"), now.hour)
        and _cron_field_matches(config.get("day_of_week", "*"), now.weekday())
    )


def _cron_field_matches(expression: object, value: int) -> bool:
    text = str(expression).strip()
    if not text or text == "*":
        return True
    if text.startswith("*/"):
        try:
            divisor = int(text[2:])
        except ValueError:
            return False
        return divisor > 0 and value % divisor == 0
    try:
        return value == int(text)
    except ValueError:
        return False


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
