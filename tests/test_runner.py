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
    assert _cron_config_matches("{bad json", now)
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


def test_run_pass_dry_run_does_not_mutate(client):
    """run_pass in dry_run mode should not create tasks or dispatch agents."""
    with client.app.state.SessionLocal() as db:
        result = run_pass(
            RunnerConfig(profiles=[], dry_run=True),
            db,
            client.app.state.SessionLocal,
        )
    # In dry_run mode, no actual mutations should occur
    assert isinstance(result, PassResult)
    assert result.dispatched == 0
