"""Agent dispatcher module.

Lifecycle Contract:
- dispatch_one() spawns the agent command as a subprocess.
- A background monitor thread auto-completes the run when the process exits.
- Agents MAY call /agent-runs/{id}/heartbeat to update last_heartbeat_at.
- Agents MAY call /agent-runs/{id}/complete with exit_code for early completion.
- stale_recovery() requeues runs with no heartbeat for stale_timeout_minutes.
- The background monitor is a safety net: even if the agent never calls /complete,
  the run will be marked done/crashed when the process exits.
"""

from __future__ import annotations

from datetime import timedelta
import json
import logging
import os
import platform
import shlex
import subprocess
import sys
import threading
import time
from collections.abc import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Agent, AgentRun, Runner, RunnerLease, Task, utcnow
from .repository import (
    add_note,
    create_agent_run,
    find_capable_agents,
    get_agent_by_name,
    get_project,
    get_task,
    is_dispatch_ready,
    list_agent_runs,
    list_tasks,
    list_workspace_configs,
    mark_run_workspace_cleaned,
    save_run_workspace_state,
)
from .schemas import TaskUpdate
from .repository import update_task
from .storage_helpers import get_agent_dispatch_statuses, get_comma_list
from .workspace import cleanup_workspace, provision_workspace

logger = logging.getLogger(__name__)


class DispatchError(Exception):
    pass


_session_factory: Callable[[], Session] | None = None
_default_session_factory: Callable[[], Session] | None = None


def set_session_factory(factory: Callable[[], Session]) -> None:
    global _session_factory
    _session_factory = factory


def dispatch_one(
    session: Session,
    agent: Agent,
    task: Task,
    api_key: str,
    base_url: str,
    session_factory: Callable[[], Session] | None = None,
) -> AgentRun:
    if not agent.enabled:
        raise DispatchError(f"Agent is disabled: {agent.name}")
    if task.human_required:
        raise DispatchError(f"Task requires a human: {task.id}")
    if task.assignee and task.assignee != agent.name:
        raise DispatchError(f"Task is already claimed by {task.assignee}.")
    if not is_dispatch_ready(session, task):
        if task.assignee:
            raise DispatchError(f"Task is already claimed by {task.assignee}.")
        raise DispatchError("Task has unresolved blocking dependencies.")

    running = list_agent_runs(session, agent_id=agent.id, status="running")
    if len(running) >= agent.max_concurrency:
        raise DispatchError(f"Agent concurrency limit reached: {agent.name}")

    command_template = _agent_command(agent.command)
    preview_run = AgentRun(id="run_preview", agent_id=agent.id, task_id=task.id)
    command = _substitute_command(command_template, agent=agent, task=task, run=preview_run)
    if agent.command_allowlist:
        allowed = get_comma_list(agent.command_allowlist)
        if not any(command.strip().startswith(prefix) for prefix in allowed):
            raise DispatchError(f"Command not in allowlist for agent {agent.name}: {command.strip()[:80]}")

    task.assignee = agent.name
    if task.status in {"backlog", "todo"}:
        task.status = "doing"
    update_task(session, task, TaskUpdate())

    run = create_agent_run(session, agent_id=agent.id, task_id=task.id, status="running")
    now = utcnow()
    run.started_at = now
    run.last_heartbeat_at = now
    run.updated_at = now
    command = _substitute_command(command_template, agent=agent, task=task, run=run)

    if agent.agent_type == "remote":
        logger.info(
            "Remote agent %s dispatched to task %s (run %s); awaiting runner poll",
            agent.name,
            task.id,
            run.id,
        )
        add_note(session, task, f"Dispatched to remote agent {agent.name} as run {run.id}.", author="dispatcher")
        session.add(run)
        session.flush()
        return run

    env = _build_env(agent, task, run, api_key=api_key, base_url=base_url)
    workspace = _provision_run_workspace(session, task, run)
    if workspace is not None and not workspace.ready:
        run.status = "crashed"
        run.finished_at = utcnow()
        run.updated_at = run.finished_at
        error_msg = workspace.error or "Workspace provisioning failed."
        add_note(session, task, f"Workspace provisioning failed for {agent.name}: {error_msg}", author="dispatcher")
        _cleanup_run_workspace(session, run)
        session.add(run)
        session.flush()
        raise DispatchError(error_msg)

    run_cwd = None
    if workspace is not None and workspace.ready:
        env["FLOW_WORKSPACE_DIR"] = workspace.path
        run_cwd = workspace.path

    try:
        process = subprocess.Popen(
            shlex.split(command),
            cwd=run_cwd or agent.working_directory or None,
            env=env,
        )
    except OSError as exc:
        run.status = "crashed"
        run.finished_at = utcnow()
        run.updated_at = run.finished_at
        add_note(session, task, f"Agent dispatch failed for {agent.name}: {exc}", author="dispatcher")
        if workspace is not None:
            _cleanup_run_workspace(session, run)
        session.add(run)
        session.flush()
        raise DispatchError(str(exc)) from exc

    run.pid = process.pid
    run.updated_at = utcnow()
    add_note(session, task, f"Dispatched to agent {agent.name} as run {run.id}.", author="dispatcher")
    session.add(run)
    session.flush()
    monitor = threading.Thread(
        target=_monitor_process,
        args=(run.id, process, _resolve_session_factory(session_factory)),
        daemon=True,
        name=f"dispatch-monitor-{run.id}",
    )
    monitor.start()
    return run


def complete_run(session: Session, run: AgentRun, exit_code: int) -> AgentRun:
    task = session.get(Task, run.task_id)
    _cleanup_run_workspace(session, run)
    run.exit_code = exit_code
    run.status = "done" if exit_code == 0 else "crashed"
    run.finished_at = utcnow()
    run.updated_at = run.finished_at
    if task is not None:
        if exit_code == 0:
            add_note(session, task, f"Agent run {run.id} completed successfully.", author="dispatcher")
        else:
            if task.status == "doing":
                task.status = "todo"
            task.assignee = None
            add_note(session, task, f"Agent run {run.id} crashed with exit code {exit_code}.", author="dispatcher")
            update_task(session, task, TaskUpdate())
    session.add(run)
    session.flush()
    return run


def heartbeat_run(session: Session, run: AgentRun) -> AgentRun:
    now = utcnow()
    run.last_heartbeat_at = now
    run.updated_at = now
    session.add(run)
    session.flush()
    return run


def stale_recovery(session: Session) -> list[str]:
    recovered: list[str] = []
    now = utcnow()
    for run in list_agent_runs(session, status="running"):
        agent = session.get(Agent, run.agent_id)
        timeout = agent.stale_claim_timeout_seconds if agent else 600
        heartbeat = run.last_heartbeat_at or run.started_at or run.created_at
        if heartbeat and heartbeat.tzinfo is None:
            heartbeat = heartbeat.replace(tzinfo=now.tzinfo)
        if heartbeat and now - heartbeat <= timedelta(seconds=timeout):
            continue
        task = session.get(Task, run.task_id)
        run.status = "stale"
        run.updated_at = now
        if task is not None:
            if task.status == "doing":
                task.status = "todo"
            task.assignee = None
            add_note(session, task, f"Recovered stale agent run {run.id}; task released.", author="dispatcher")
            update_task(session, task, TaskUpdate())
        session.add(run)
        recovered.append(run.id)
        _cleanup_run_workspace(session, run)
    active_leases = session.scalars(select(RunnerLease).where(RunnerLease.status == "active")).all()
    for lease in active_leases:
        expires_at = lease.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=now.tzinfo)
        if expires_at >= now:
            continue
        lease.status = "expired"
        lease.updated_at = now
        run = session.get(AgentRun, lease.agent_run_id)
        if run and run.status == "running":
            task = session.get(Task, run.task_id)
            if task is not None:
                if task.status == "doing":
                    task.status = "todo"
                task.assignee = None
                add_note(session, task, f"Recovered expired runner lease {lease.id}; task released.", author="dispatcher")
                update_task(session, task, TaskUpdate())
            run.status = "stale"
            run.finished_at = now
            run.updated_at = now
            session.add(run)
            recovered.append(run.id)
        session.add(lease)
        session.flush()
        runner = session.get(Runner, lease.runner_id)
        if runner:
            active_remaining = session.scalar(
                select(func.count()).select_from(RunnerLease).where(
                    RunnerLease.runner_id == runner.id,
                    RunnerLease.status == "active",
                )
            )
            if active_remaining == 0:
                runner.status = "offline"
                runner.updated_at = now
                session.add(runner)
    session.flush()
    return recovered


def dispatch_loop(
    session: Session,
    agent_name: str,
    api_key: str,
    base_url: str,
    continuous: bool,
    interval: float,
    session_factory: Callable[[], Session] | None = None,
) -> list[AgentRun]:
    agent = get_agent_by_name(session, agent_name)
    if agent is None:
        raise DispatchError(f"Agent not found: {agent_name}")

    dispatched: list[AgentRun] = []
    while True:
        task = _next_capable_task(session, agent)
        if task is not None:
            dispatched.append(dispatch_one(session, agent, task, api_key, base_url, session_factory=session_factory))
            session.commit()
        if not continuous:
            return dispatched
        time.sleep(interval)


def _next_capable_task(session: Session, agent: Agent) -> Task | None:
    agent_statuses = (
        set(get_agent_dispatch_statuses(agent))
        if agent.dispatch_statuses
        else {"backlog", "todo"}
    )
    for task in list_tasks(session, unclaimed=True):
        if not is_dispatch_ready(session, task, allowed_statuses=agent_statuses):
            continue
        if agent in find_capable_agents(session, task):
            return task
    return None


def _build_env(agent: Agent, task: Task, run: AgentRun, *, api_key: str, base_url: str) -> dict[str, str]:
    env = {}
    for name in get_comma_list(agent.env_allowlist):
        if name in os.environ:
            env[name] = os.environ[name]
    env.update(
        {
            "FLOW_TASK_ID": task.id,
            "FLOW_PROJECT": task.project,
            "FLOW_AGENT_NAME": agent.name,
            "FLOW_BASE_URL": base_url,
            "FLOW_API_KEY": api_key,
            "FLOW_RUN_ID": run.id,
        }
    )
    return env


def _provision_run_workspace(session: Session, task: Task, run: AgentRun):
    config = _workspace_config_for_task(session, task)
    if config is None:
        return None
    project = get_project(session, task.project)
    repo_path = project.repo_path if project and project.repo_path else None
    workspace = provision_workspace(config, task.id, repo_path)
    save_run_workspace_state(session, run, workspace)
    return workspace


def _workspace_config_for_task(session: Session, task: Task):
    configs = list_workspace_configs(session, enabled_only=True)
    if not configs:
        return None
    for config in configs:
        if config.name == task.project:
            return config
    for config in configs:
        if config.name == "default":
            return config
    return configs[0]


def _cleanup_run_workspace(session: Session, run: AgentRun) -> bool | None:
    state = _workspace_state(run)
    if not state or state.get("cleaned_up"):
        return None
    if not state.get("ready"):
        return None
    workspace_id = state.get("workspace_id")
    strategy = state.get("strategy")
    path = state.get("path")
    if not workspace_id or not strategy or not path:
        return None
    task = get_task(session, run.task_id)
    if task is None:
        logger.warning("Skipping workspace cleanup for run %s: task %s not found.", run.id, run.task_id)
        return None
    config = _workspace_config_for_task(session, task)
    if config is None:
        logger.warning("Skipping workspace cleanup for run %s: no workspace config available.", run.id)
        return None
    cleaned = cleanup_workspace(str(workspace_id), str(strategy), str(path), config)
    mark_run_workspace_cleaned(session, run, cleaned=cleaned)
    return cleaned


def _workspace_state(run: AgentRun) -> dict | None:
    if not run.workspace_state:
        return None
    try:
        value = json.loads(run.workspace_state)
    except (TypeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _substitute_command(command: str, *, agent: Agent, task: Task, run: AgentRun) -> str:
    return command.format(task_id=task.id, agent_id=agent.id, run_id=run.id, command=_default_agent_command())


def _agent_command(command: str) -> str:
    command = command.strip()
    if not command or command == "echo hello":
        return "{command}"
    return command


def _default_agent_command() -> str:
    """Return a cross-platform command that exits 0 and prints a message."""
    if platform.system() == "Windows":
        return "cmd /c echo agent_dispatched"
    return f"{sys.executable} -c \"print('agent_dispatched')\""


def _resolve_session_factory(session_factory: Callable[[], Session] | None = None) -> Callable[[], Session]:
    global _default_session_factory
    if session_factory is not None:
        return session_factory
    if _session_factory is not None:
        return _session_factory
    if _default_session_factory is None:
        from .database import build_engine, build_session_factory, default_database_url

        _default_session_factory = build_session_factory(build_engine(default_database_url()))
    return _default_session_factory


def _monitor_process(run_id: str, process: subprocess.Popen, session_factory: Callable[[], Session]) -> None:
    """Monitor a spawned process and auto-complete the run when it exits."""
    try:
        while process.poll() is None:
            time.sleep(5)
        exit_code = process.returncode
    except Exception:
        exit_code = -1

    session = session_factory()
    try:
        run = session.get(AgentRun, run_id)
        if run and run.status == "running":
            complete_run(session, run, exit_code)
            session.commit()
    except Exception:
        session.rollback()
    finally:
        session.close()
