from __future__ import annotations

import re

from sqlalchemy import select

from flow_app.bootstrap import main as bootstrap_main
from flow_app.database import build_engine, build_session_factory
from flow_app.main import create_app
from flow_app.models import Agent, AgentApiKey, AutomationRule, Project, WorkspaceConfig
from flow_app.repository import hash_api_key


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
        assert len(_all(session, AgentApiKey)) == 2
        assert len(_all(session, Agent)) == 1
        assert len(_all(session, WorkspaceConfig)) == 1
        assert len(_all(session, AutomationRule)) == 2

        agent = session.scalars(select(Agent).where(Agent.name == "smoke-test")).one()
        assert agent.agent_type == "cli"
        assert agent.command == "echo smoke-test"
        assert agent.dispatch_statuses == "todo"
        assert agent.max_concurrency == 1

        workspace = session.scalars(select(WorkspaceConfig).where(WorkspaceConfig.name == "default")).one()
        assert workspace.strategy == "scratch_dir"


def test_bootstrap_is_idempotent(tmp_path, capsys):
    db_url = _db_url(tmp_path)

    assert bootstrap_main(["--database-url", db_url]) == 0
    first_output = capsys.readouterr().out
    assert len(_printed_keys(first_output)) == 2

    assert bootstrap_main(["--database-url", db_url]) == 0
    second_output = capsys.readouterr().out
    assert "already exists" in second_output
    assert not _printed_keys(second_output)

    with _session_factory(db_url)() as session:
        assert len(_all(session, Project)) == 1
        assert len(_all(session, AgentApiKey)) == 2
        assert len(_all(session, Agent)) == 1
        assert len(_all(session, WorkspaceConfig)) == 1
        assert len(_all(session, AutomationRule)) == 2
        assert len({key.name for key in _all(session, AgentApiKey)}) == 2
        assert len({rule.name for rule in _all(session, AutomationRule)}) == 2


def test_dry_run_prints_prefix_and_does_not_write_to_db(tmp_path, capsys):
    db_url = _db_url(tmp_path)

    assert bootstrap_main(["--database-url", db_url, "--dry-run"]) == 0

    output = capsys.readouterr().out
    lines = [line for line in output.splitlines() if line]
    assert lines
    assert all(line.startswith("[DRY RUN]") for line in lines)
    assert "would create" in output
    assert len(_printed_keys(output)) == 2

    engine = build_engine(db_url)
    assert not engine.dialect.has_table(engine.connect(), "projects")


def test_api_keys_are_printed_and_stored_as_hashes(tmp_path, capsys):
    db_url = _db_url(tmp_path)

    assert bootstrap_main(["--database-url", db_url]) == 0

    output = capsys.readouterr().out
    raw_keys = _printed_keys(output)
    assert len(raw_keys) == 2

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
        assert roles == {"admin-key": "admin", "impl-key": "implementer"}


def test_project_flag_creates_custom_project_slug(tmp_path):
    db_url = _db_url(tmp_path)

    assert bootstrap_main(["--database-url", db_url, "--project", "custom-project"]) == 0

    with _session_factory(db_url)() as session:
        assert session.get(Project, "custom-project") is not None
        assert session.get(Project, "default") is None
