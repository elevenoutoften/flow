"""Agent service layer: shared business logic for REST and MCP transports."""

from __future__ import annotations

from sqlalchemy.orm import Session

from flow_app.dispatcher import (
    _next_capable_task,
    complete_run,
    dispatch_one,
    heartbeat_run,
    stale_recovery,
)
from flow_app.models import Agent, AgentRun
from flow_app.repository import (
    create_agent,
    get_agent,
    get_agent_run,
    get_task,
    list_agent_runs,
    list_agents,
    update_agent,
)
from flow_app.realtime import publish_board_event
from flow_app.schemas import AgentCreate, AgentUpdate


class AgentError(Exception):
    """Base exception for agent service errors."""

    def __init__(self, message: str, error_type: str = "agent_error"):
        self.message = message
        self.error_type = error_type
        super().__init__(message)


class AgentNotFoundError(AgentError):
    def __init__(self, agent_id: str):
        super().__init__(f"Agent not found: {agent_id}", "not_found")


class AgentRunNotFoundError(AgentError):
    def __init__(self, run_id: str):
        super().__init__(f"Agent run not found: {run_id}", "not_found")


class AgentService:
    """Shared agent service operations."""

    def __init__(self, db: Session):
        self.db = db

    def list_agents(self, enabled_only: bool = False) -> list[Agent]:
        return list_agents(self.db, enabled_only=enabled_only)

    def get_agent(self, agent_id: str) -> Agent:
        return self._require_agent(agent_id)

    def create_agent(self, payload: AgentCreate) -> Agent:
        agent = create_agent(self.db, payload)
        self._commit()
        return agent

    def update_agent(self, agent_id: str, payload: AgentUpdate) -> Agent:
        agent = update_agent(self.db, self._require_agent(agent_id), payload)
        self._commit()
        return agent

    def list_runs(
        self,
        agent_id: str | None = None,
        status: str | None = None,
        task_id: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[AgentRun]:
        return list_agent_runs(self.db, agent_id=agent_id, task_id=task_id, status=status, limit=limit, offset=offset)

    def get_run(self, run_id: str) -> AgentRun:
        return self._require_run(run_id)

    def dispatch(
        self,
        agent_id: str,
        task_id: str | None,
        base_url: str,
        api_key_value: str,
        env_allowlist: str | None = None,
        command_allowlist: str | None = None,
    ) -> AgentRun:
        agent = self._require_agent(agent_id)
        if env_allowlist is not None:
            agent.env_allowlist = env_allowlist
        if command_allowlist is not None:
            agent.command_allowlist = command_allowlist
        task = get_task(self.db, task_id) if task_id else _next_capable_task(self.db, agent)
        if task is None:
            if task_id:
                raise AgentError(f"Task not found: {task_id}", "not_found")
            raise AgentError("No eligible task found for agent dispatch_statuses.", "not_found")
        run = dispatch_one(self.db, agent, task, api_key=api_key_value, base_url=base_url)
        self._commit()
        refreshed = get_task(self.db, run.task_id)
        if refreshed is not None:
            publish_board_event("task_claimed", refreshed, run_id=run.id)
        return run

    def heartbeat(self, run_id: str) -> AgentRun:
        run = heartbeat_run(self.db, self._require_run(run_id))
        self._commit()
        return run

    def complete_run(self, run_id: str, exit_code: int) -> AgentRun:
        run = complete_run(self.db, self._require_run(run_id), exit_code)
        self._commit()
        task = get_task(self.db, run.task_id)
        if task is not None:
            publish_board_event("task_updated", task, run_id=run.id, exit_code=exit_code)
        return run

    def stale_recovery(self) -> list[str]:
        recovered = stale_recovery(self.db)
        task_ids = [run.task_id for run_id in recovered if (run := get_agent_run(self.db, run_id)) is not None]
        self._commit()
        for task_id in task_ids:
            task = get_task(self.db, task_id)
            if task is not None:
                publish_board_event("task_updated", task, changed="stale_recovery")
        return recovered

    def _require_agent(self, agent_id: str) -> Agent:
        agent = get_agent(self.db, agent_id)
        if agent is None:
            raise AgentNotFoundError(agent_id)
        return agent

    def _require_run(self, run_id: str) -> AgentRun:
        run = get_agent_run(self.db, run_id)
        if run is None:
            raise AgentRunNotFoundError(run_id)
        return run

    def _commit(self) -> None:
        self.db.commit()
