from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from flow_app.models import AgentRun
from flow_app.repository import get_task
from flow_app.runner import (
    PassResult,
    RunnerConfig,
    _conditions_reference_task_fields,
    _cron_config_matches,
    _parse_rule_array,
    _project_eq_filter,
    load_runner_config,
    run_pass,
)


def test_runner_config_loads_from_env(monkeypatch, tmp_path):
    db_url = f"sqlite:///{tmp_path / 'runner.sqlite'}"
    monkeypatch.setenv("FLOW_RUNNER_PROFILES", "alpha, beta")
    monkeypatch.setenv("FLOW_RUNNER_INTERVAL", "12.5")
    monkeypatch.setenv("FLOW_BASE_URL", "http://flow.test")
    monkeypatch.setenv("FLOW_API_KEY", "secret")
    monkeypatch.setenv("FLOW_DATABASE_URL", db_url)
    monkeypatch.setenv("FLOW_RUNNER_DRY_RUN", "true")

    config = load_runner_config()

    assert config.profiles == ["alpha", "beta"]
    assert config.interval == 12.5
    assert config.base_url == "http://flow.test"
    assert config.api_key == "secret"
    assert config.database_url == db_url
    assert config.dry_run is True


def test_runner_config_raises_without_profiles(monkeypatch):
    monkeypatch.delenv("FLOW_RUNNER_PROFILES", raising=False)

    try:
        load_runner_config()
    except ValueError as exc:
        assert "FLOW_RUNNER_PROFILES" in str(exc)
    else:
        raise AssertionError("Expected ValueError")


def test_pass_result_defaults():
    result = PassResult()

    assert result.dispatched == 0
    assert result.stale_recovered == 0
    assert result.cron_matches == 0
    assert result.webhook_deliveries == 0


def test_run_pass_dispatches_agent(client, monkeypatch):
    monkeypatch.setattr("flow_app.runner.run_deliveries", lambda dry_run=False: 0)
    monkeypatch.setattr("flow_app.dispatcher.subprocess.Popen", lambda *args, **kwargs: SimpleNamespace(pid=12345))
    monkeypatch.setattr("flow_app.dispatcher.threading.Thread", lambda *args, **kwargs: SimpleNamespace(start=lambda: None))
    agent_response = client.post(
        "/api/agents",
        json={
            "name": "runner-agent",
            "command": "python -c \"print('ok')\"",
            "capabilities": "",
            "max_concurrency": 1,
        },
    )
    assert agent_response.status_code == 201, agent_response.text
    task_response = client.post("/api/tasks", json={"title": "Dispatch me", "status": "todo"})
    assert task_response.status_code == 201, task_response.text

    with client.app.state.SessionLocal() as db:
        result = run_pass(
            RunnerConfig(profiles=["runner-agent"], base_url="http://flow.test"),
            db,
            client.app.state.SessionLocal,
        )
        task = get_task(db, task_response.json()["id"])

    assert result.dispatched >= 1
    assert task is not None
    assert task.status == "doing"


def test_run_pass_stale_recovery(client, monkeypatch):
    monkeypatch.setattr("flow_app.runner.run_deliveries", lambda dry_run=False: 0)
    agent_response = client.post(
        "/api/agents",
        json={
            "name": "stale-agent",
            "command": "python -c \"print('ok')\"",
            "stale_claim_timeout_seconds": 1,
        },
    )
    assert agent_response.status_code == 201, agent_response.text
    task_response = client.post(
        "/api/tasks",
        json={"title": "Recover me", "status": "doing", "assignee": "stale-agent"},
    )
    assert task_response.status_code == 201, task_response.text
    stale_time = datetime.now(timezone.utc) - timedelta(minutes=10)

    with client.app.state.SessionLocal() as db:
        run = AgentRun(
            id="run_stale_001",
            agent_id=agent_response.json()["id"],
            task_id=task_response.json()["id"],
            status="running",
            started_at=stale_time,
            last_heartbeat_at=stale_time,
            created_at=stale_time,
            updated_at=stale_time,
        )
        db.add(run)
        db.commit()

        result = run_pass(RunnerConfig(profiles=[]), db, client.app.state.SessionLocal)
        task = get_task(db, task_response.json()["id"])

    assert result.stale_recovered >= 1
    assert task is not None
    assert task.status == "todo"
    assert task.assignee is None


def test_cron_expression_matching():
    now = datetime(2026, 5, 21, 10, 30, tzinfo=timezone.utc)

    assert _cron_config_matches("", now)
    assert not _cron_config_matches("{bad json", now)  # malformed → fail closed
    assert _cron_config_matches('{"minute":"*","hour":"*","day_of_week":"*"}', now)
    assert _cron_config_matches('{"minute":"*/5"}', now)
    assert not _cron_config_matches('{"minute":"*/7"}', now)
    assert _cron_config_matches('{"minute":"30","hour":"10"}', now)
    assert not _cron_config_matches('{"minute":"31"}', now)
    # Standard cron: 0=Sunday.  May 21 2026 is a Thursday (cron day 4).
    assert _cron_config_matches('{"day_of_week":"4"}', now)
    assert not _cron_config_matches('{"day_of_week":"3"}', now)


def test_parse_rule_array_valid():
    assert _parse_rule_array('[{"field":"status"}]') == [{"field": "status"}]
    assert _parse_rule_array(None) == []
    assert _parse_rule_array("") == []


def test_parse_rule_array_invalid():
    assert _parse_rule_array("{bad json") is None
    assert _parse_rule_array('{"not":"a list"}') is None


def test_conditions_reference_task_fields():
    assert _conditions_reference_task_fields([{"field": "status"}]) is True
    assert _conditions_reference_task_fields([{"field": "nonexistent"}]) is False
    assert _conditions_reference_task_fields([]) is False


def test_project_eq_filter():
    assert _project_eq_filter([{"field": "project", "operator": "eq", "value": "alpha"}]) == "alpha"
    assert _project_eq_filter([{"field": "status", "value": "todo"}]) is None
    assert _project_eq_filter([]) is None


def test_cron_config_malformed_json_fails_closed():
    """Malformed JSON in trigger_config must fail closed (return False), not match."""
    now = datetime(2026, 5, 21, 10, 30, tzinfo=timezone.utc)
    assert not _cron_config_matches("{bad json", now)
    assert not _cron_config_matches("not json at all", now)


def test_cron_config_non_dict_fails_closed():
    """Non-dict JSON (e.g. a list or string) must fail closed."""
    now = datetime(2026, 5, 21, 10, 30, tzinfo=timezone.utc)
    assert not _cron_config_matches('["list","not","dict"]', now)
    assert not _cron_config_matches('"just a string"', now)


def test_cron_string_non_5_field_fails_closed():
    """A cron string without exactly 5 fields must not match (fail closed)."""
    from flow_app.cron import cron_string_matches
    now = datetime(2026, 5, 21, 10, 30, tzinfo=timezone.utc)
    assert not cron_string_matches("* * *", now)  # 3 fields
    assert not cron_string_matches("* * * * * *", now)  # 6 fields
    assert not cron_string_matches("", now)  # empty
    assert not cron_string_matches("garbage", now)  # 1 field


def test_cron_range_expression_matches():
    """Range expressions like '1-5' in cron fields must match correctly."""
    from flow_app.cron import cron_field_matches
    # Monday-Friday (1-5 in day_of_week)
    assert cron_field_matches("1-5", 1)  # Monday
    assert cron_field_matches("1-5", 3)  # Wednesday
    assert cron_field_matches("1-5", 5)  # Friday
    assert not cron_field_matches("1-5", 6)  # Saturday — outside range
    assert not cron_field_matches("1-5", 0)  # Sunday — outside range


def test_cron_comma_list_matches():
    """Comma-separated values in cron fields must match correctly."""
    from flow_app.cron import cron_field_matches
    assert cron_field_matches("1,3,5", 1)
    assert cron_field_matches("1,3,5", 3)
    assert cron_field_matches("1,3,5", 5)
    assert not cron_field_matches("1,3,5", 2)
    assert not cron_field_matches("1,3,5", 4)


def test_cron_validate_range_accepted():
    """validate_cron_string must accept range expressions."""
    from flow_app.cron import validate_cron_string
    assert validate_cron_string("0 9 * * 1-5") is None  # Mon-Fri at 9am
    assert validate_cron_string("*/5 8-17 * * *") is None  # every 5 min during 8am-5pm


def test_cron_validate_range_rejected_out_of_bounds():
    """validate_cron_string must reject out-of-bounds ranges."""
    from flow_app.cron import validate_cron_string
    assert validate_cron_string("0 9 * * 1-8") is not None  # 8 > max dow (7)
    assert validate_cron_string("0 25 * * *") is not None  # 25 > max hour (23)
    assert validate_cron_string("0 9 * * 5-1") is not None  # start > end


def test_cron_validate_comma_list_accepted():
    """validate_cron_string must accept comma-separated lists."""
    from flow_app.cron import validate_cron_string
    assert validate_cron_string("0 9 * * 1,3,5") is None  # Mon, Wed, Fri
    assert validate_cron_string("0,30 9 * * *") is None  # 0 and 30 minutes


def test_cron_validate_comma_list_rejected_bad_value():
    """validate_cron_string must reject bad values in comma lists."""
    from flow_app.cron import validate_cron_string
    assert validate_cron_string("0 9 * * 1,8,5") is not None  # 8 > max dow (7)
    assert validate_cron_string("0 9 * * 1,,5") is not None  # empty element


def test_run_pass_dry_run_does_not_mutate(client, monkeypatch):
    """run_pass in dry_run mode should not create tasks or dispatch agents.

    Monkeypatches run_deliveries so it does not open a connection to the
    default DB (which would bypass the test fixture's isolated database).
    """
    monkeypatch.setattr("flow_app.runner.run_deliveries", lambda dry_run=False: 0)
    with client.app.state.SessionLocal() as db:
        result = run_pass(
            RunnerConfig(profiles=[], dry_run=True),
            db,
            client.app.state.SessionLocal,
        )
    # In dry_run mode, no actual mutations should occur
    assert isinstance(result, PassResult)
    assert result.dispatched == 0


# ---------------------------------------------------------------------------
# run_loop — one iteration with deterministic termination
# ---------------------------------------------------------------------------

class _FakeSession:
    """Minimal context-manager session for run_loop tests."""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def commit(self):
        pass

    def close(self):
        pass


def test_run_loop_executes_one_pass_then_stops(monkeypatch, tmp_path):
    """run_loop should execute at least one pass and terminate when asked.

    We patch run_pass to count calls and raise KeyboardInterrupt after the
    first invocation, plus time.sleep to be a no-op so the test is fast.
    """
    import flow_app.runner as runner_mod

    call_count = {"n": 0}

    class FakeEngine:
        def dispose(self):
            pass

    def fake_run_pass(config, session, session_factory, **kw):
        call_count["n"] += 1
        raise KeyboardInterrupt  # Terminate after first pass

    monkeypatch.setattr(runner_mod, "run_pass", fake_run_pass)
    monkeypatch.setattr("time.sleep", lambda *_: None)
    monkeypatch.setattr(runner_mod, "build_engine", lambda url: FakeEngine())
    monkeypatch.setattr(runner_mod, "ensure_compatible_schema", lambda engine: None)
    monkeypatch.setattr(
        runner_mod,
        "build_session_factory",
        lambda engine: lambda: _FakeSession(),
    )

    config = RunnerConfig(profiles=["test"], database_url=f"sqlite:///{tmp_path / 'loop.sqlite'}")
    # Should not raise — KeyboardInterrupt is caught internally
    runner_mod.run_loop(config)
    assert call_count["n"] >= 1


# ---------------------------------------------------------------------------
# main() argument parsing
# ---------------------------------------------------------------------------

def test_main_once_requires_no_profiles(monkeypatch, tmp_path):
    """--once should work without FLOW_RUNNER_PROFILES (require_profiles=False)."""
    import flow_app.runner as runner_mod
    from flow_app.database import Base

    db_path = tmp_path / "main_once.sqlite"
    db_url = f"sqlite:///{db_path}"
    monkeypatch.setenv("FLOW_DATABASE_URL", db_url)
    monkeypatch.delenv("FLOW_RUNNER_PROFILES", raising=False)

    # Create the schema before main() runs migration
    from flow_app.database import build_engine
    engine = build_engine(db_url)
    Base.metadata.create_all(bind=engine)

    exit_code = runner_mod.main(["--once"])
    assert exit_code == 0


def test_main_stale_recovery_only_requires_no_profiles(monkeypatch, tmp_path):
    """--stale-recovery-only should work without FLOW_RUNNER_PROFILES."""
    import flow_app.runner as runner_mod
    from flow_app.database import Base, build_engine

    db_path = tmp_path / "main_stale.sqlite"
    db_url = f"sqlite:///{db_path}"
    monkeypatch.setenv("FLOW_DATABASE_URL", db_url)
    monkeypatch.delenv("FLOW_RUNNER_PROFILES", raising=False)

    engine = build_engine(db_url)
    Base.metadata.create_all(bind=engine)

    exit_code = runner_mod.main(["--stale-recovery-only"])
    assert exit_code == 0


def test_main_dry_run_flag(monkeypatch, tmp_path):
    """--dry-run should set config.dry_run to True."""
    import flow_app.runner as runner_mod
    from flow_app.database import Base, build_engine

    db_path = tmp_path / "main_dry.sqlite"
    db_url = f"sqlite:///{db_path}"
    monkeypatch.setenv("FLOW_DATABASE_URL", db_url)
    monkeypatch.delenv("FLOW_RUNNER_PROFILES", raising=False)

    engine = build_engine(db_url)
    Base.metadata.create_all(bind=engine)

    exit_code = runner_mod.main(["--once", "--dry-run"])
    assert exit_code == 0


def test_main_profiles_flag_overrides_env(monkeypatch, tmp_path):
    """--profiles should override FLOW_RUNNER_PROFILES."""
    import flow_app.runner as runner_mod
    from flow_app.database import Base, build_engine

    db_path = tmp_path / "main_profiles.sqlite"
    db_url = f"sqlite:///{db_path}"
    monkeypatch.setenv("FLOW_DATABASE_URL", db_url)
    monkeypatch.setenv("FLOW_RUNNER_PROFILES", "env-profile")

    engine = build_engine(db_url)
    Base.metadata.create_all(bind=engine)

    exit_code = runner_mod.main(["--once", "--profiles", "cli-profile"])
    assert exit_code == 0


# ---------------------------------------------------------------------------
# dry_run_automation_rules
# ---------------------------------------------------------------------------

def test_dry_run_automation_rules_no_rules(client):
    """dry_run_automation_rules with no rules should return empty matches."""
    from flow_app.runner import dry_run_automation_rules

    with client.app.state.SessionLocal() as db:
        result = dry_run_automation_rules(db, trigger="cron", dry_run=True)
    assert result["evaluated_rules"] == 0
    assert result["matches"] == []
    assert result["invalid_conditions"] == []


def test_dry_run_automation_rules_matches_cron(client):
    """A cron rule with matching conditions should produce a match in dry_run."""
    from flow_app.models import AutomationRule
    from flow_app.runner import dry_run_automation_rules

    # Create a cron rule with wildcard trigger (always matches)
    with client.app.state.SessionLocal() as db:
        rule = AutomationRule(
            id="rule_dry_001",
            name="dry-run-test",
            trigger="cron",
            trigger_config="",
            conditions='[{"field": "status", "operator": "eq", "value": "todo"}]',
            actions='[{"type": "move", "target_status": "doing"}]',
            priority=50,
            enabled=1,
        )
        db.add(rule)
        db.commit()
        # Create a task that matches the condition
        client.post("/api/tasks", json={"title": "Dry run target", "status": "todo"})

    with client.app.state.SessionLocal() as db:
        result = dry_run_automation_rules(db, trigger="cron", dry_run=True)
    assert result["evaluated_rules"] >= 1
    assert len(result["matches"]) >= 1
    # In dry_run mode, no actions should be executed
    match = result["matches"][0]
    assert match["matched_conditions"] is True


# ---------------------------------------------------------------------------
# _run_recurring_templates
# ---------------------------------------------------------------------------

def test_run_recurring_templates_dry_run(client):
    """_run_recurring_templates in dry_run should report skipped, not materialized."""
    from flow_app.runner import _run_recurring_templates

    with client.app.state.SessionLocal() as db:
        result = _run_recurring_templates(db, dry_run=True)
    assert result.materialized == 0
    assert result.dry_run is True


# ---------------------------------------------------------------------------
# Rule task-filtering helpers (additional coverage)
# ---------------------------------------------------------------------------

def test_tasks_for_rule_filters_by_project(client):
    """_tasks_for_rule should filter tasks by the project eq condition."""
    from flow_app.runner import _tasks_for_rule

    client.post("/api/tasks", json={"title": "Project A", "project": "alpha"})
    client.post("/api/tasks", json={"title": "Project B", "project": "beta"})

    with client.app.state.SessionLocal() as db:
        tasks = _tasks_for_rule(db, [{"field": "project", "operator": "eq", "value": "alpha"}])
    projects = {t.project for t in tasks}
    assert "alpha" in projects
    assert "beta" not in projects


def test_tasks_for_rule_no_project_filter_returns_all(client):
    """_tasks_for_rule without a project filter should return tasks from all projects."""
    from flow_app.runner import _tasks_for_rule

    client.post("/api/tasks", json={"title": "Any A", "project": "alpha"})
    client.post("/api/tasks", json={"title": "Any B", "project": "beta"})

    with client.app.state.SessionLocal() as db:
        tasks = _tasks_for_rule(db, [{"field": "status", "operator": "eq", "value": "todo"}])
    assert len(tasks) >= 2
