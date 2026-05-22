from __future__ import annotations

import re
import json

from sqlalchemy import select

from flow_app.bootstrap import main as bootstrap_main
from flow_app.database import build_engine, build_session_factory
from flow_app.main import create_app
from flow_app.models import Agent, AgentApiKey, AutomationRule, Project, WorkspaceConfig
from flow_app.repository import create_task, get_task, hash_api_key
from flow_app.rules_engine import emit_event
from flow_app.schemas import TaskCreate


def _db_url(tmp_path, name: str = "flow.sqlite") -> str:
    return f"sqlite:///{tmp_path / name}"


def _session_factory(db_url: str):
    engine = build_engine(db_url)
    return build_session_factory(engine)


def _all(session, model):
    return list(session.scalars(select(model)).all())


def _printed_keys(output: str) -> list[str]:
    return re.findall(r"flow_[A-Za-z0-9_-]+", output)


def test_fresh_bootstrap_creates_default_records(tmp_path):
    db_url = _db_url(tmp_path)
    create_app(db_url, trusted_headers=True, session_secret="test-secret-for-testing")

    assert bootstrap_main(["--database-url", db_url]) == 0

    with _session_factory(db_url)() as session:
        assert [project.slug for project in _all(session, Project)] == ["default"]
        assert len(_all(session, AgentApiKey)) == 3
        assert len(_all(session, Agent)) == 2
        assert len(_all(session, WorkspaceConfig)) == 1
        assert len(_all(session, AutomationRule)) == 4

        agent = session.scalars(select(Agent).where(Agent.name == "smoke-test")).one()
        assert agent.agent_type == "cli"
        assert agent.command == "echo smoke-test"
        assert agent.dispatch_statuses == "todo"
        assert agent.max_concurrency == 1

        reviewer = session.scalars(select(Agent).where(Agent.name == "reviewer-agent")).one()
        assert reviewer.agent_type == "cli"
        assert reviewer.command == "echo reviewer-agent"
        assert reviewer.dispatch_statuses == "review"
        assert reviewer.max_concurrency == 1

        workspace = session.scalars(select(WorkspaceConfig).where(WorkspaceConfig.name == "default")).one()
        assert workspace.strategy == "scratch_dir"


def test_bootstrap_is_idempotent(tmp_path, capsys):
    db_url = _db_url(tmp_path)

    assert bootstrap_main(["--database-url", db_url]) == 0
    first_output = capsys.readouterr().out
    assert len(_printed_keys(first_output)) == 3

    assert bootstrap_main(["--database-url", db_url]) == 0
    second_output = capsys.readouterr().out
    assert "already exists" in second_output
    assert not _printed_keys(second_output)

    with _session_factory(db_url)() as session:
        assert len(_all(session, Project)) == 1
        assert len(_all(session, AgentApiKey)) == 3
        assert len(_all(session, Agent)) == 2
        assert len(_all(session, WorkspaceConfig)) == 1
        assert len(_all(session, AutomationRule)) == 4
        assert len({key.name for key in _all(session, AgentApiKey)}) == 3
        assert len({rule.name for rule in _all(session, AutomationRule)}) == 4


def test_dry_run_prints_prefix_and_does_not_write_to_db(tmp_path, capsys):
    db_url = _db_url(tmp_path)

    assert bootstrap_main(["--database-url", db_url, "--dry-run"]) == 0

    output = capsys.readouterr().out
    lines = [line for line in output.splitlines() if line]
    assert lines
    assert all(line.startswith("[DRY RUN]") for line in lines)
    assert "would create" in output
    assert len(_printed_keys(output)) == 3

    engine = build_engine(db_url)
    assert not engine.dialect.has_table(engine.connect(), "projects")


def test_api_keys_are_printed_and_stored_as_hashes(tmp_path, capsys):
    db_url = _db_url(tmp_path)

    assert bootstrap_main(["--database-url", db_url]) == 0

    output = capsys.readouterr().out
    raw_keys = _printed_keys(output)
    assert len(raw_keys) == 3

    with _session_factory(db_url)() as session:
        stored_keys = _all(session, AgentApiKey)
        stored_hashes = {key.key_hash for key in stored_keys}
        stored_prefixes = {key.key_prefix for key in stored_keys}
        assert stored_hashes == {hash_api_key(raw_key) for raw_key in raw_keys}
        for raw_key in raw_keys:
            assert raw_key not in stored_hashes
            assert raw_key not in stored_prefixes


def test_bootstrap_api_key_roles(tmp_path):
    db_url = _db_url(tmp_path)

    assert bootstrap_main(["--database-url", db_url]) == 0

    with _session_factory(db_url)() as session:
        roles = {key.name: key.role for key in _all(session, AgentApiKey)}
        assert roles == {"admin-key": "admin", "impl-key": "implementer", "reviewer-key": "reviewer"}


def test_bootstrap_review_rules_include_dispatch_credentials_and_handoff_condition(tmp_path, capsys):
    db_url = _db_url(tmp_path)

    assert bootstrap_main(["--database-url", db_url]) == 0

    output = capsys.readouterr().out
    reviewer_key = re.search(r"API key: reviewer-key .* - (flow_[A-Za-z0-9_-]+)", output).group(1)

    with _session_factory(db_url)() as session:
        route_rule = session.scalars(select(AutomationRule).where(AutomationRule.name == "route-review-tasks")).one()
        route_actions = json.loads(route_rule.actions)
        assert route_rule.trigger == "task_moved"
        assert json.loads(route_rule.conditions) == [{"field": "status", "operator": "eq", "value": "review"}]
        assert route_actions == [
            {
                "type": "dispatch",
                "agent_name": "reviewer-agent",
                "api_key": reviewer_key,
                "base_url": "http://localhost:8100",
            }
        ]

        handoff_rule = session.scalars(select(AutomationRule).where(AutomationRule.name == "warn-missing-handoff")).one()
        assert json.loads(handoff_rule.conditions) == [
            {"field": "status", "operator": "eq", "value": "review"},
            {"field": "latest_handoff", "operator": "not_exists"},
        ]


def test_project_flag_creates_custom_project_slug(tmp_path):
    db_url = _db_url(tmp_path)

    assert bootstrap_main(["--database-url", db_url, "--project", "custom-project"]) == 0

    with _session_factory(db_url)() as session:
        assert session.get(Project, "custom-project") is not None
        assert session.get(Project, "default") is None


def test_bootstrap_automation_rules_execute_correctly(tmp_path):
    db_url = _db_url(tmp_path)

    assert bootstrap_main(["--database-url", db_url]) == 0

    with _session_factory(db_url)() as session:
        task = create_task(session, TaskCreate(title="Promote me", status="backlog", project="default"))
        created_matches = emit_event(session, "task_created", task_id=task.id)
        session.commit()

        assert [match["rule_name"] for match in created_matches] == ["Auto-promote backlog tasks"]
        assert created_matches[0]["action_results"][0]["success"] is True
        assert created_matches[0]["action_results"][0]["details"] == {"from": "backlog", "to": "todo"}

        promoted = get_task(session, task.id)
        assert promoted is not None
        assert promoted.status == "todo"

        completed_matches = emit_event(session, "task_completed", task_id=task.id)
        session.commit()

        assert [match["rule_name"] for match in completed_matches] == ["Notify on task completion"]
