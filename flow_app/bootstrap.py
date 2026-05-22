from __future__ import annotations

import argparse
import json
from dataclasses import dataclass

from sqlalchemy import inspect, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from .database import Base, build_engine, build_session_factory, default_database_url
from .main import ensure_compatible_schema
from .models import AgentApiKey, ApiKeyRole, AutomationRule, Project
from .repository import (
    create_agent,
    create_agent_api_key,
    create_automation_rule,
    create_workspace_config,
    ensure_project,
    generate_api_key_secret,
    get_agent_by_name,
    get_project,
    get_workspace_config_by_name,
)
from .schemas import AgentApiKeyCreate, AgentCreate, AutomationRuleCreate, WorkspaceConfigCreate


ADMIN_KEY_NAME = "admin-key"
IMPLEMENTER_KEY_NAME = "impl-key"
AGENT_NAME = "smoke-test"
WORKSPACE_CONFIG_NAME = "default"
AUTOMATION_RULES = (
    # ── Review loop ──
    AutomationRuleCreate(
        name="Route review tasks to reviewer",
        description="When a task moves to review, dispatch the reviewer agent.",
        trigger="task_moved",
        conditions=json.dumps([{"field": "status", "operator": "eq", "value": "review"}]),
        actions=json.dumps([{"type": "dispatch", "agent_name": "reviewer-agent"}]),
        priority=90,
    ),
    AutomationRuleCreate(
        name="Block tasks missing handoff in review",
        description="If a task reaches review without a handoff note, flag it for human review.",
        trigger="task_moved",
        conditions=json.dumps(
            [
                {"field": "status", "operator": "eq", "value": "review"},
                {"field": "human_required", "operator": "eq", "value": False},
            ]
        ),
        actions=json.dumps(
            [{"type": "add_note", "text": "⚠️ No handoff provided — manual review required.", "author": "automation"}]
        ),
        priority=80,
    ),
    # ── Notifications ──
    AutomationRuleCreate(
        name="Notify on task completion",
        description="Trigger notification pipeline when a task is marked done.",
        trigger="task_completed",
        priority=50,
    ),
    # ── Backlog promotion ──
    AutomationRuleCreate(
        name="Auto-promote backlog tasks",
        description="Move newly created backlog tasks to todo.",
        trigger="task_created",
        conditions=json.dumps([{"field": "status", "operator": "eq", "value": "backlog"}]),
        actions=json.dumps([{"type": "move", "status": "todo"}]),
        priority=10,
    ),
)


@dataclass(frozen=True)
class BootstrapLine:
    label: str
    name: str
    status: str
    detail: str = ""

    def render(self) -> str:
        detail = f" {self.detail}" if self.detail else ""
        return f"{self.label}: {self.name}{detail} ({self.status})"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bootstrap a fresh Flow installation.")
    parser.add_argument("--database-url", default=None, help="Database URL to bootstrap.")
    parser.add_argument("--project", default="default", help="Project slug to create.")
    parser.add_argument("--dry-run", action="store_true", help="Print planned changes without writing to the database.")
    return parser.parse_args(argv)


def _active_api_key_by_name(session: Session, name: str) -> AgentApiKey | None:
    return session.scalars(
        select(AgentApiKey).where(AgentApiKey.name == name, AgentApiKey.revoked_at.is_(None))
    ).first()


def _automation_rule_by_name(session: Session, name: str) -> AutomationRule | None:
    return session.scalars(select(AutomationRule).where(AutomationRule.name == name)).first()


def _database_has_tables(engine) -> bool:
    try:
        return bool(inspect(engine).get_table_names())
    except OperationalError:
        return False


def _collect_dry_run_lines(engine, project_slug: str) -> list[BootstrapLine]:
    if not _database_has_tables(engine):
        return [
            BootstrapLine("Project", project_slug, "would create"),
            BootstrapLine("API key", ADMIN_KEY_NAME, "would create", f"({ApiKeyRole.admin.value}) - {generate_api_key_secret()}"),
            BootstrapLine(
                "API key",
                IMPLEMENTER_KEY_NAME,
                "would create",
                f"({ApiKeyRole.implementer.value}) - {generate_api_key_secret()}",
            ),
            BootstrapLine("Agent", AGENT_NAME, "would create"),
            BootstrapLine("Workspace config", WORKSPACE_CONFIG_NAME, "would create"),
            *[BootstrapLine("Automation rule", rule.name, "would create") for rule in AUTOMATION_RULES],
        ]

    session_factory = build_session_factory(engine)
    with session_factory() as session:
        return _collect_lines(session, project_slug, dry_run=True)


def _collect_lines(session: Session, project_slug: str, *, dry_run: bool) -> list[BootstrapLine]:
    lines: list[BootstrapLine] = []
    project_exists = get_project(session, project_slug) is not None
    if not dry_run and not project_exists:
        ensure_project(session, project_slug)
    lines.append(BootstrapLine("Project", project_slug, "already exists" if project_exists else _create_status(dry_run)))

    key_specs = (
        (ADMIN_KEY_NAME, ApiKeyRole.admin),
        (IMPLEMENTER_KEY_NAME, ApiKeyRole.implementer),
    )
    for name, role in key_specs:
        existing = _active_api_key_by_name(session, name)
        if existing is not None:
            lines.append(BootstrapLine("API key", name, "already exists", f"({role.value})"))
            continue
        if dry_run:
            raw_key = generate_api_key_secret()
            status = "would create"
        else:
            _, raw_key = create_agent_api_key(session, AgentApiKeyCreate(name=name, role=role))
            status = "created"
        lines.append(BootstrapLine("API key", name, status, f"({role.value}) - {raw_key}"))

    agent_exists = get_agent_by_name(session, AGENT_NAME) is not None
    if not dry_run and not agent_exists:
        create_agent(
            session,
            AgentCreate(
                name=AGENT_NAME,
                agent_type="cli",
                command="echo smoke-test",
                dispatch_statuses="todo",
                max_concurrency=1,
            ),
        )
    lines.append(BootstrapLine("Agent", AGENT_NAME, "already exists" if agent_exists else _create_status(dry_run)))

    workspace_exists = get_workspace_config_by_name(session, WORKSPACE_CONFIG_NAME) is not None
    if not dry_run and not workspace_exists:
        create_workspace_config(
            session,
            WorkspaceConfigCreate(name=WORKSPACE_CONFIG_NAME, strategy="scratch_dir"),
        )
    lines.append(
        BootstrapLine(
            "Workspace config",
            WORKSPACE_CONFIG_NAME,
            "already exists" if workspace_exists else _create_status(dry_run),
        )
    )

    for rule in AUTOMATION_RULES:
        rule_exists = _automation_rule_by_name(session, rule.name) is not None
        if not dry_run and not rule_exists:
            create_automation_rule(session, rule)
        lines.append(
            BootstrapLine("Automation rule", rule.name, "already exists" if rule_exists else _create_status(dry_run))
        )

    return lines


def _create_status(dry_run: bool) -> str:
    return "would create" if dry_run else "created"


def _print_lines(lines: list[BootstrapLine], *, dry_run: bool) -> None:
    prefix = "[DRY RUN] " if dry_run else ""
    print(f"{prefix}Flow bootstrap complete.")
    print()
    for line in lines:
        print(f"{prefix}{line.render()}")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    database_url = args.database_url or default_database_url()
    engine = build_engine(database_url)

    if args.dry_run:
        lines = _collect_dry_run_lines(engine, args.project)
        _print_lines(lines, dry_run=True)
        return 0

    Base.metadata.create_all(bind=engine)
    ensure_compatible_schema(engine)
    session_factory = build_session_factory(engine)
    with session_factory() as session:
        lines = _collect_lines(session, args.project, dry_run=False)
        session.commit()

    _print_lines(lines, dry_run=False)
    return 0
