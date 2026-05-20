from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import secrets

from sqlalchemy import Select, delete, select
from sqlalchemy.orm import Session, object_session, selectinload

from .models import (
    Agent,
    AgentApiKey,
    AgentRun,
    AutomationRule,
    FlowCounter,
    Idea,
    Project,
    Task,
    TaskHandoff,
    TaskLink,
    TaskNote,
    WebhookConfig,
    WebhookDelivery,
    WorkspaceConfig,
    utcnow,
)
from .schemas import (
    AgentCreate,
    AgentApiKeyCreate,
    AgentApiKeyCreateResponse,
    AgentApiKeyResponse,
    AgentResponse,
    AgentRunResponse,
    AgentUpdate,
    AutomationEvent,
    AutomationRuleCreate,
    AutomationRuleResponse,
    AutomationRuleUpdate,
    BLOCKING_TASK_LINK_TYPES,
    DependencySummary,
    IdeaCreate,
    IdeaResponse,
    IdeaUpdate,
    HandoffResponse,
    NEXT_STATUS_ORDER,
    PromoteTaskSpec,
    STATUSES,
    ProjectCreate,
    ProjectResponse,
    ProjectUpdate,
    TaskCreate,
    TaskLinkCreate,
    TaskLinkResponse,
    TaskNoteResponse,
    TaskResponse,
    TaskSummary,
    TaskUpdate,
    WebhookConfigResponse,
    WebhookDeliveryResponse,
    WorkspaceConfigCreate,
    WorkspaceConfigResponse,
    WorkspaceConfigUpdate,
)


def generate_task_id(session: Session) -> str:
    return generate_counter_id(session, "task", "flow")


def generate_idea_id(session: Session) -> str:
    return generate_counter_id(session, "idea", "idea")


def generate_api_key_id(session: Session) -> str:
    return generate_counter_id(session, "api_key", "key")


def generate_agent_id(session: Session) -> str:
    return generate_counter_id(session, "agent", "agent")


def generate_agent_run_id(session: Session) -> str:
    return generate_counter_id(session, "agent_run", "run")


def generate_rule_id(session: Session) -> str:
    return generate_counter_id(session, "automation_rule", "rule")


def generate_link_id(session: Session) -> str:
    return generate_counter_id(session, "task_link", "link")


def generate_workspace_config_id(session: Session) -> str:
    return generate_counter_id(session, "workspace_config", "ws")


def generate_webhook_id(session: Session) -> str:
    return generate_counter_id(session, "webhook", "webhook")


def generate_delivery_id(session: Session) -> str:
    return generate_counter_id(session, "delivery", "delivery")


def generate_handoff_id(session: Session) -> str:
    return generate_counter_id(session, "handoff", "handoff")


def generate_counter_id(session: Session, name: str, prefix: str) -> str:
    counter = session.get(FlowCounter, name)
    if counter is None:
        counter = FlowCounter(name=name, value=0)
        session.add(counter)
        session.flush()
    counter.value += 1
    session.flush()
    return f"{prefix}_{counter.value:06d}"


def hash_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def generate_api_key_secret() -> str:
    return "flow_" + secrets.token_urlsafe(32)


def task_query() -> Select[tuple[Task]]:
    return select(Task).options(selectinload(Task.notes))


def get_task(session: Session, task_id: str) -> Task | None:
    return session.scalars(task_query().where(Task.id == task_id)).first()


def require_task(session: Session, task_id: str) -> Task:
    task = get_task(session, task_id)
    if task is None:
        raise ValueError(f"Task not found: {task_id}")
    return task


def get_project(session: Session, slug: str) -> Project | None:
    return session.get(Project, slug)


def list_projects(session: Session) -> list[Project]:
    projects = list(session.scalars(select(Project)).all())
    projects.sort(key=lambda project: project.slug)
    return projects


def create_project(session: Session, payload: ProjectCreate) -> Project:
    existing = get_project(session, payload.slug)
    if existing:
        return existing

    now = utcnow()
    project = Project(
        slug=payload.slug,
        name=payload.name or payload.slug,
        description=payload.description,
        repo_url=payload.repo_url,
        repo_path=payload.repo_path,
        default_branch=payload.default_branch or "main",
        notes=payload.notes,
        created_at=now,
        updated_at=now,
    )
    session.add(project)
    session.flush()
    return project


def ensure_project(session: Session, slug: str) -> Project:
    project = get_project(session, slug)
    if project:
        return project
    return create_project(session, ProjectCreate(slug=slug, name=slug))


def update_project(session: Session, project: Project, payload: ProjectUpdate) -> Project:
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(project, field, value)
    if not project.name:
        project.name = project.slug
    if not project.default_branch:
        project.default_branch = "main"
    project.updated_at = utcnow()
    session.add(project)
    session.flush()
    return project


def list_agent_api_keys(session: Session) -> list[AgentApiKey]:
    items = list(session.scalars(select(AgentApiKey)).all())
    items.sort(
        key=lambda api_key: (
            api_key.revoked_at is not None,
            api_key.name.lower(),
            _ensure_datetime(api_key.created_at),
            api_key.id,
        )
    )
    return items


def get_agent_api_key(session: Session, api_key_id: str) -> AgentApiKey | None:
    return session.get(AgentApiKey, api_key_id)


def lookup_api_key_by_hash(session: Session, key_hash: str) -> AgentApiKey | None:
    return session.scalars(select(AgentApiKey).where(AgentApiKey.key_hash == key_hash)).first()


def create_agent_api_key(session: Session, payload: AgentApiKeyCreate) -> tuple[AgentApiKey, str]:
    raw_key = generate_api_key_secret()
    now = utcnow()
    api_key = AgentApiKey(
        id=generate_api_key_id(session),
        name=payload.name,
        description=payload.description,
        role=payload.role.value,
        key_prefix=raw_key[:16],
        key_hash=hash_api_key(raw_key),
        created_at=now,
        revoked_at=None,
    )
    session.add(api_key)
    session.flush()
    return api_key, raw_key


def revoke_agent_api_key(session: Session, api_key: AgentApiKey) -> AgentApiKey:
    if api_key.revoked_at is None:
        api_key.revoked_at = utcnow()
    session.add(api_key)
    session.flush()
    return api_key


def create_agent(session: Session, payload: AgentCreate) -> Agent:
    now = utcnow()
    agent = Agent(
        id=generate_agent_id(session),
        name=payload.name,
        description=payload.description,
        enabled=int(payload.enabled),
        agent_type=payload.agent_type,
        capabilities=payload.capabilities,
        command=payload.command,
        env_allowlist=payload.env_allowlist,
        working_directory=payload.working_directory,
        max_concurrency=payload.max_concurrency,
        heartbeat_timeout_seconds=payload.heartbeat_timeout_seconds,
        stale_claim_timeout_seconds=payload.stale_claim_timeout_seconds,
        dispatch_statuses=payload.dispatch_statuses,
        created_at=now,
        updated_at=now,
    )
    session.add(agent)
    session.flush()
    return agent


def get_agent(session: Session, agent_id: str) -> Agent | None:
    return session.get(Agent, agent_id)


def get_agent_by_name(session: Session, name: str) -> Agent | None:
    return session.scalars(select(Agent).where(Agent.name == name)).first()


def list_agents(session: Session, *, enabled_only: bool = False) -> list[Agent]:
    stmt = select(Agent)
    if enabled_only:
        stmt = stmt.where(Agent.enabled == 1)
    agents = list(session.scalars(stmt).all())
    agents.sort(key=lambda agent: (agent.name.lower(), agent.id))
    return agents


def update_agent(session: Session, agent: Agent, payload: AgentUpdate) -> Agent:
    changes = payload.model_dump(exclude_unset=True)
    if "enabled" in changes:
        changes["enabled"] = int(changes["enabled"])
    for field, value in changes.items():
        setattr(agent, field, value)
    agent.updated_at = utcnow()
    session.add(agent)
    session.flush()
    return agent


def serialize_agent(agent: Agent) -> AgentResponse:
    return AgentResponse(
        id=agent.id,
        name=agent.name,
        description=agent.description,
        enabled=bool(agent.enabled),
        agent_type=agent.agent_type,
        capabilities=agent.capabilities,
        command=agent.command,
        env_allowlist=agent.env_allowlist,
        working_directory=agent.working_directory,
        max_concurrency=agent.max_concurrency,
        heartbeat_timeout_seconds=agent.heartbeat_timeout_seconds,
        stale_claim_timeout_seconds=agent.stale_claim_timeout_seconds,
        dispatch_statuses=agent.dispatch_statuses,
        created_at=_ensure_datetime(agent.created_at),
        updated_at=_ensure_datetime(agent.updated_at),
    )


def create_agent_run(session: Session, *, agent_id: str, task_id: str, status: str = "pending") -> AgentRun:
    now = utcnow()
    run = AgentRun(
        id=generate_agent_run_id(session),
        agent_id=agent_id,
        task_id=task_id,
        status=status,
        created_at=now,
        updated_at=now,
    )
    session.add(run)
    session.flush()
    return run


def get_agent_run(session: Session, run_id: str) -> AgentRun | None:
    return session.get(AgentRun, run_id)


def list_agent_runs(
    session: Session,
    *,
    agent_id: str | None = None,
    task_id: str | None = None,
    status: str | None = None,
) -> list[AgentRun]:
    stmt = select(AgentRun)
    if agent_id:
        stmt = stmt.where(AgentRun.agent_id == agent_id)
    if task_id:
        stmt = stmt.where(AgentRun.task_id == task_id)
    if status:
        stmt = stmt.where(AgentRun.status == status)
    runs = list(session.scalars(stmt).all())
    runs.sort(key=lambda run: (-(_ensure_datetime(run.created_at).timestamp()), run.id))
    return runs


def serialize_agent_run(run: AgentRun) -> AgentRunResponse:
    return AgentRunResponse(
        id=run.id,
        agent_id=run.agent_id,
        task_id=run.task_id,
        status=run.status,
        pid=run.pid,
        exit_code=run.exit_code,
        started_at=_ensure_optional_datetime(run.started_at),
        finished_at=_ensure_optional_datetime(run.finished_at),
        last_heartbeat_at=_ensure_optional_datetime(run.last_heartbeat_at),
        workspace_state=_decode_workspace_state(run.workspace_state),
        created_at=_ensure_datetime(run.created_at),
        updated_at=_ensure_datetime(run.updated_at),
    )


def save_run_workspace_state(session: Session, run: AgentRun, workspace_result) -> AgentRun:
    state = {
        "workspace_id": workspace_result.workspace_id,
        "strategy": workspace_result.strategy,
        "path": workspace_result.path,
        "branch": workspace_result.branch,
        "ready": bool(workspace_result.ready),
        "provisioned_at": utcnow().isoformat(),
        "cleaned_up": False,
        "cleaned_at": None,
    }
    if workspace_result.error:
        state["error"] = workspace_result.error
    run.workspace_state = json.dumps(state, sort_keys=True)
    run.updated_at = utcnow()
    session.add(run)
    session.flush()
    return run


def mark_run_workspace_cleaned(session: Session, run: AgentRun, *, cleaned: bool) -> AgentRun:
    state = _decode_workspace_state(run.workspace_state)
    if not state:
        return run
    state["cleaned_up"] = bool(cleaned)
    state["cleaned_at"] = utcnow().isoformat()
    run.workspace_state = json.dumps(state, sort_keys=True)
    run.updated_at = utcnow()
    session.add(run)
    session.flush()
    return run


def get_task_workspace(session: Session, task_id: str) -> dict | None:
    for run in list_agent_runs(session, task_id=task_id):
        state = _decode_workspace_state(run.workspace_state)
        if state:
            return state
    return None


def find_capable_agents(session: Session, task: Task) -> list[Agent]:
    keywords = _task_keywords(task)
    capable = []
    for agent in list_agents(session, enabled_only=True):
        capabilities = [item.strip().lower() for item in agent.capabilities.split(",") if item.strip()]
        if not capabilities or any(capability in keywords for capability in capabilities):
            capable.append(agent)
    return capable


def create_automation_rule(session: Session, payload: AutomationRuleCreate) -> AutomationRule:
    now = utcnow()
    rule = AutomationRule(
        id=generate_rule_id(session),
        name=payload.name,
        description=payload.description,
        enabled=int(payload.enabled),
        priority=payload.priority,
        trigger=payload.trigger,
        trigger_config=payload.trigger_config,
        conditions=payload.conditions,
        actions=payload.actions,
        last_run_at=None,
        created_at=now,
        updated_at=now,
    )
    session.add(rule)
    session.flush()
    return rule


def get_automation_rule(session: Session, rule_id: str) -> AutomationRule | None:
    return session.get(AutomationRule, rule_id)


def list_automation_rules(session: Session, *, enabled_only: bool = False) -> list[AutomationRule]:
    stmt = select(AutomationRule)
    if enabled_only:
        stmt = stmt.where(AutomationRule.enabled == 1)
    rules = list(session.scalars(stmt).all())
    rules.sort(key=lambda rule: (-rule.priority, rule.name.lower(), rule.id))
    return rules


def update_automation_rule(session: Session, rule: AutomationRule, payload: AutomationRuleUpdate) -> AutomationRule:
    changes = payload.model_dump(exclude_unset=True)
    if "enabled" in changes:
        changes["enabled"] = int(changes["enabled"])
    for field, value in changes.items():
        setattr(rule, field, value)
    rule.updated_at = utcnow()
    session.add(rule)
    session.flush()
    return rule


def serialize_automation_rule(rule: AutomationRule) -> AutomationRuleResponse:
    return AutomationRuleResponse(
        id=rule.id,
        name=rule.name,
        description=rule.description,
        enabled=bool(rule.enabled),
        priority=rule.priority,
        trigger=rule.trigger,
        trigger_config=rule.trigger_config,
        conditions=rule.conditions,
        actions=rule.actions,
        last_run_at=_ensure_optional_datetime(rule.last_run_at),
        created_at=_ensure_datetime(rule.created_at),
        updated_at=_ensure_datetime(rule.updated_at),
    )


def create_workspace_config(session: Session, payload: WorkspaceConfigCreate) -> WorkspaceConfig:
    now = utcnow()
    config = WorkspaceConfig(
        id=generate_workspace_config_id(session),
        name=payload.name,
        strategy=payload.strategy,
        base_branch=payload.base_branch or "main",
        branch_prefix=payload.branch_prefix or "task/",
        root_dir=payload.root_dir,
        scratch_root=payload.scratch_root or "/tmp/flow-scratch",
        description=payload.description,
        enabled=int(payload.enabled),
        created_at=now,
        updated_at=now,
    )
    session.add(config)
    session.flush()
    return config


def get_workspace_config(session: Session, config_id: str) -> WorkspaceConfig | None:
    return session.get(WorkspaceConfig, config_id)


def get_workspace_config_by_name(session: Session, name: str) -> WorkspaceConfig | None:
    return session.scalars(select(WorkspaceConfig).where(WorkspaceConfig.name == name)).first()


def list_workspace_configs(session: Session, *, enabled_only: bool = False) -> list[WorkspaceConfig]:
    stmt = select(WorkspaceConfig)
    if enabled_only:
        stmt = stmt.where(WorkspaceConfig.enabled == 1)
    configs = list(session.scalars(stmt).all())
    configs.sort(key=lambda config: (config.name.lower(), config.id))
    return configs


def update_workspace_config(
    session: Session,
    config: WorkspaceConfig,
    payload: WorkspaceConfigUpdate,
) -> WorkspaceConfig:
    changes = payload.model_dump(exclude_unset=True)
    if "enabled" in changes:
        changes["enabled"] = int(changes["enabled"])
    for field, value in changes.items():
        setattr(config, field, value)
    if not config.base_branch:
        config.base_branch = "main"
    if not config.branch_prefix:
        config.branch_prefix = "task/"
    if not config.scratch_root:
        config.scratch_root = "/tmp/flow-scratch"
    config.updated_at = utcnow()
    session.add(config)
    session.flush()
    return config


def serialize_workspace_config(config: WorkspaceConfig) -> WorkspaceConfigResponse:
    return WorkspaceConfigResponse(
        id=config.id,
        name=config.name,
        strategy=config.strategy,
        base_branch=config.base_branch,
        branch_prefix=config.branch_prefix,
        root_dir=config.root_dir,
        scratch_root=config.scratch_root,
        description=config.description,
        enabled=bool(config.enabled),
        created_at=_ensure_datetime(config.created_at),
        updated_at=_ensure_datetime(config.updated_at),
    )


def create_webhook_config(
    session: Session,
    name: str,
    url: str,
    events: list[str],
    project: str,
    max_retries: int,
    retry_backoff_seconds: int,
) -> tuple[WebhookConfig, str]:
    raw_secret = secrets.token_urlsafe(32)
    now = utcnow()
    config = WebhookConfig(
        id=generate_webhook_id(session),
        name=name,
        url=url,
        secret=raw_secret,
        events=_join_webhook_events(events),
        active=1,
        max_retries=max_retries,
        retry_backoff_seconds=retry_backoff_seconds,
        project=project,
        created_at=now,
        updated_at=now,
    )
    session.add(config)
    session.flush()
    return config, raw_secret


def get_webhook_config(session: Session, webhook_id: str) -> WebhookConfig | None:
    return session.get(WebhookConfig, webhook_id)


def list_webhook_configs(session: Session, project: str | None = None) -> list[WebhookConfig]:
    stmt = select(WebhookConfig)
    if project:
        stmt = stmt.where(WebhookConfig.project == project)
    configs = list(session.scalars(stmt).all())
    configs.sort(key=lambda config: (config.name.lower(), config.id))
    return configs


def update_webhook_config(session: Session, config: WebhookConfig, updates_dict: dict) -> WebhookConfig:
    changes = dict(updates_dict)
    if "events" in changes and changes["events"] is not None:
        changes["events"] = _join_webhook_events(changes["events"])
    for field, value in changes.items():
        setattr(config, field, value)
    config.updated_at = utcnow()
    session.add(config)
    session.flush()
    return config


def delete_webhook_config(session: Session, config: WebhookConfig) -> None:
    session.execute(delete(WebhookDelivery).where(WebhookDelivery.webhook_id == config.id))
    session.delete(config)
    session.flush()


def serialize_webhook_config(config: WebhookConfig) -> WebhookConfigResponse:
    return WebhookConfigResponse(
        id=config.id,
        name=config.name,
        url=config.url,
        events=_split_webhook_events(config.events),
        active=config.active,
        max_retries=config.max_retries,
        retry_backoff_seconds=config.retry_backoff_seconds,
        project=config.project,
        created_at=_ensure_datetime(config.created_at),
        updated_at=_ensure_datetime(config.updated_at),
    )


def create_webhook_delivery(session: Session, webhook_id: str, event: str, payload: str) -> WebhookDelivery:
    now = utcnow()
    delivery = WebhookDelivery(
        id=generate_delivery_id(session),
        webhook_id=webhook_id,
        event=event,
        payload=payload,
        status="pending",
        attempts=0,
        next_attempt_at=None,
        last_response_code=None,
        last_response_body=None,
        created_at=now,
        updated_at=now,
    )
    session.add(delivery)
    session.flush()
    return delivery


def get_webhook_delivery(session: Session, delivery_id: str) -> WebhookDelivery | None:
    return session.get(WebhookDelivery, delivery_id)


def list_webhook_deliveries(session: Session, webhook_id: str, status: str | None = None) -> list[WebhookDelivery]:
    stmt = select(WebhookDelivery).where(WebhookDelivery.webhook_id == webhook_id)
    if status:
        stmt = stmt.where(WebhookDelivery.status == status)
    deliveries = list(session.scalars(stmt).all())
    deliveries.sort(key=lambda delivery: (-_ensure_datetime(delivery.created_at).timestamp(), delivery.id))
    return deliveries


def update_webhook_delivery(session: Session, delivery: WebhookDelivery, **kwargs) -> WebhookDelivery:
    for field, value in kwargs.items():
        setattr(delivery, field, value)
    delivery.updated_at = utcnow()
    session.add(delivery)
    session.flush()
    return delivery


def serialize_webhook_delivery(delivery: WebhookDelivery) -> WebhookDeliveryResponse:
    return WebhookDeliveryResponse(
        id=delivery.id,
        webhook_id=delivery.webhook_id,
        event=delivery.event,
        status=delivery.status,
        attempts=delivery.attempts,
        next_attempt_at=_ensure_optional_datetime(delivery.next_attempt_at),
        last_response_code=delivery.last_response_code,
        last_response_body=delivery.last_response_body,
        created_at=_ensure_datetime(delivery.created_at),
        updated_at=_ensure_datetime(delivery.updated_at),
    )


def create_task_link(session: Session, payload: TaskLinkCreate) -> TaskLink:
    require_task(session, payload.parent_id)
    require_task(session, payload.child_id)
    if payload.link_type in BLOCKING_TASK_LINK_TYPES and has_cycle(session, payload.parent_id, payload.child_id):
        raise ValueError("Blocking task link would create a cycle.")

    link = TaskLink(
        id=generate_link_id(session),
        parent_id=payload.parent_id,
        child_id=payload.child_id,
        link_type=payload.link_type,
        created_at=utcnow(),
    )
    session.add(link)
    session.flush()
    return link


def get_task_link(session: Session, link_id: str) -> TaskLink | None:
    return session.get(TaskLink, link_id)


def delete_task_link(session: Session, link_id: str) -> bool:
    link = get_task_link(session, link_id)
    if link is None:
        return False
    session.delete(link)
    session.flush()
    return True


def list_task_links(
    session: Session,
    *,
    task_id: str | None = None,
    parent_id: str | None = None,
    child_id: str | None = None,
    link_type: str | None = None,
) -> list[TaskLink]:
    stmt = select(TaskLink)
    if task_id:
        stmt = stmt.where((TaskLink.parent_id == task_id) | (TaskLink.child_id == task_id))
    if parent_id:
        stmt = stmt.where(TaskLink.parent_id == parent_id)
    if child_id:
        stmt = stmt.where(TaskLink.child_id == child_id)
    if link_type:
        stmt = stmt.where(TaskLink.link_type == link_type)
    links = list(session.scalars(stmt).all())
    links.sort(key=lambda link: (_ensure_datetime(link.created_at), link.id))
    return links


def serialize_task_link(link: TaskLink) -> TaskLinkResponse:
    return TaskLinkResponse(
        id=link.id,
        parent_id=link.parent_id,
        child_id=link.child_id,
        link_type=link.link_type,
        created_at=_ensure_datetime(link.created_at),
    )


def serialize_task_summary(task: Task) -> TaskSummary:
    return TaskSummary(
        id=task.id,
        title=task.title,
        status=task.status,
        priority=task.priority,
        project=task.project,
        assignee=task.assignee,
    )


def get_dependency_summary(session: Session, task_id: str) -> DependencySummary:
    require_task(session, task_id)
    parents = list_task_links(session, child_id=task_id)
    children = list_task_links(session, parent_id=task_id)
    blocked_by = [link for link in parents if link.link_type in BLOCKING_TASK_LINK_TYPES]
    blocking = [link for link in children if link.link_type in BLOCKING_TASK_LINK_TYPES]
    parent_tasks = [serialize_task_summary(task) for link in parents if (task := get_task(session, link.parent_id))]
    child_tasks = [serialize_task_summary(task) for link in children if (task := get_task(session, link.child_id))]
    blocked_by_tasks = [
        serialize_task_summary(task) for link in blocked_by if (task := get_task(session, link.parent_id))
    ]
    blocking_tasks = [
        serialize_task_summary(task) for link in blocking if (task := get_task(session, link.child_id))
    ]
    return DependencySummary(
        parents=[serialize_task_link(link) for link in parents],
        children=[serialize_task_link(link) for link in children],
        blocked_by=[serialize_task_link(link) for link in blocked_by],
        blocking=[serialize_task_link(link) for link in blocking],
        parent_tasks=parent_tasks,
        child_tasks=child_tasks,
        blocked_by_tasks=blocked_by_tasks,
        blocking_tasks=blocking_tasks,
    )


def has_cycle(session: Session, parent_id: str, child_id: str) -> bool:
    target = parent_id
    stack = [child_id]
    seen: set[str] = set()

    while stack:
        current = stack.pop()
        if current == target:
            return True
        if current in seen:
            continue
        seen.add(current)
        child_links = list_task_links(session, parent_id=current)
        stack.extend(link.child_id for link in child_links if link.link_type in BLOCKING_TASK_LINK_TYPES)
    return False


def auto_promote_unblocked_children(session: Session, parent_task_id: str) -> list[Task]:
    child_links = list_task_links(session, parent_id=parent_task_id)
    child_ids = {
        link.child_id
        for link in child_links
        if link.link_type in BLOCKING_TASK_LINK_TYPES
    }
    promoted: list[Task] = []

    for child_id in sorted(child_ids):
        child = get_task(session, child_id)
        if child is None or child.status != "backlog":
            continue

        parent_links = [
            link
            for link in list_task_links(session, child_id=child_id)
            if link.link_type in BLOCKING_TASK_LINK_TYPES
        ]
        parent_tasks = [get_task(session, link.parent_id) for link in parent_links]
        if parent_tasks and all(parent is not None and parent.status == "done" for parent in parent_tasks):
            child.status = "todo"
            update_task(session, child, TaskUpdate())
            add_note(
                session,
                child,
                f"All blocking dependencies are done; dependency {parent_task_id} unblocked this task and moved it to todo.",
                author="system",
            )
            promoted.append(child)

    return promoted


def evaluate_rules(session: Session, event: AutomationEvent, actor=None) -> list[dict]:
    from .rules_engine import emit_event

    data = dict(event.data or {})
    if event.project:
        data["project"] = event.project
    return emit_event(session, event.trigger, task_id=event.task_id, data=data, actor=actor)


def get_idea(session: Session, idea_id: str) -> Idea | None:
    return session.get(Idea, idea_id)


def require_idea(session: Session, idea_id: str) -> Idea:
    idea = get_idea(session, idea_id)
    if idea is None:
        raise ValueError(f"Idea not found: {idea_id}")
    return idea


def list_ideas(
    session: Session,
    *,
    project: str | None = None,
    archived: bool = False,
) -> list[Idea]:
    stmt = select(Idea)
    if project:
        stmt = stmt.where(Idea.project == project)
    if archived:
        stmt = stmt.where(Idea.archived_at.is_not(None))
    else:
        stmt = stmt.where(Idea.archived_at.is_(None))
    items = list(session.scalars(stmt).all())
    items.sort(key=lambda idea: (-(idea.created_at.timestamp() if idea.created_at else 0), idea.id))
    return items


def create_idea(session: Session, payload: IdeaCreate) -> Idea:
    now = utcnow()
    ensure_project(session, payload.project)
    idea = Idea(
        id=generate_idea_id(session),
        title=payload.title,
        description=payload.description,
        project=payload.project,
        author=payload.author,
        archived_at=None,
        created_at=now,
        updated_at=now,
    )
    session.add(idea)
    session.flush()
    return idea


def update_idea(session: Session, idea: Idea, payload: IdeaUpdate) -> Idea:
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(idea, field, value)
    if "project" in changes:
        ensure_project(session, idea.project)
    idea.updated_at = utcnow()
    session.add(idea)
    session.flush()
    return idea


def archive_idea(session: Session, idea: Idea) -> Idea:
    idea.archived_at = utcnow()
    idea.updated_at = utcnow()
    session.add(idea)
    session.flush()
    return idea


def unarchive_idea(session: Session, idea: Idea) -> Idea:
    idea.archived_at = None
    idea.updated_at = utcnow()
    session.add(idea)
    session.flush()
    return idea


def list_tasks(
    session: Session,
    *,
    project: str | None = None,
    status: str | None = None,
    assignee: str | None = None,
    unclaimed: bool = False,
) -> list[Task]:
    stmt = task_query()
    if project:
        stmt = stmt.where(Task.project == project)
    if status:
        stmt = stmt.where(Task.status == status)
    if assignee:
        stmt = stmt.where(Task.assignee == assignee)
    if unclaimed:
        stmt = stmt.where(Task.assignee.is_(None))

    items = list(session.scalars(stmt).unique().all())
    status_order = {name: index for index, name in enumerate(STATUSES)}
    items.sort(key=lambda task: (status_order.get(task.status, 99), -task.priority, task.created_at, task.id))
    return items


def create_task(session: Session, payload: TaskCreate) -> Task:
    now = utcnow()
    ensure_project(session, payload.project)
    task = Task(
        id=generate_task_id(session),
        title=payload.title,
        status=payload.status,
        priority=payload.priority,
        project=payload.project,
        assignee=payload.assignee,
        human_required=int(payload.human_required),
        assignee_type=payload.assignee_type.value,
        blocker_reason=payload.blocker_reason,
        complexity=payload.complexity.value,
        impact=payload.impact.value,
        effort=payload.effort.value,
        risk=payload.risk.value,
        description=payload.description,
        acceptance_criteria=payload.acceptance_criteria,
        source_filename=payload.source_filename,
        source_line=payload.source_line,
        import_batch_id=payload.import_batch_id,
        source_title=payload.source_title,
        created_at=now,
        updated_at=now,
    )
    session.add(task)
    session.flush()
    return task


def update_task(session: Session, task: Task, payload: TaskUpdate) -> Task:
    changes = payload.model_dump(exclude_unset=True)
    # Convert schema types to DB column types
    if "human_required" in changes:
        changes["human_required"] = int(changes["human_required"])
    if "assignee_type" in changes:
        changes["assignee_type"] = changes["assignee_type"].value if hasattr(changes["assignee_type"], "value") else changes["assignee_type"]
    for enum_field in ("complexity", "impact", "effort", "risk"):
        if enum_field in changes and hasattr(changes[enum_field], "value"):
            changes[enum_field] = changes[enum_field].value
    for field, value in changes.items():
        setattr(task, field, value)
    if "project" in changes:
        ensure_project(session, task.project)
    task.updated_at = utcnow()
    session.add(task)
    session.flush()
    return task


def next_task(session: Session, *, project: str | None = None) -> Task | None:
    for status in NEXT_STATUS_ORDER:
        stmt = (
            task_query()
            .where(Task.status == status)
            .where(Task.assignee.is_(None))
            .order_by(Task.priority.desc(), Task.created_at.asc(), Task.id.asc())
        )
        if project:
            stmt = stmt.where(Task.project == project)
        task = session.scalars(stmt).first()
        if task:
            return task
    return None


def add_note(session: Session, task: Task, body: str, *, author: str | None = None) -> TaskNote:
    note = TaskNote(task_id=task.id, body=body, author=author, created_at=utcnow())
    task.notes.append(note)
    task.updated_at = utcnow()
    session.add(task)
    session.flush()
    return note


def create_task_handoff(
    session: Session,
    task_id: str,
    author: str,
    summary: str,
    changed_files: list[str],
    commands_run: list[str],
    tests_run: list[str],
    artifacts: list[dict[str, str]],
    attempted_but_failed: list[str],
    remaining_work: str,
    outcome: str,
    next_recommended_agent: str | None,
    capabilities: list[str],
) -> TaskHandoff:
    require_task(session, task_id)
    handoff = TaskHandoff(
        id=generate_handoff_id(session),
        task_id=task_id,
        author=author,
        summary=summary,
        changed_files=json.dumps(changed_files),
        commands_run=json.dumps(commands_run),
        tests_run=json.dumps(tests_run),
        artifacts=json.dumps(artifacts),
        attempted_but_failed=json.dumps(attempted_but_failed),
        remaining_work=remaining_work,
        outcome=outcome,
        next_recommended_agent=next_recommended_agent,
        capabilities=json.dumps(capabilities),
        created_at=utcnow(),
    )
    session.add(handoff)
    session.flush()
    return handoff


def get_task_handoff(session: Session, handoff_id: str) -> TaskHandoff | None:
    return session.get(TaskHandoff, handoff_id)


def list_task_handoffs(session: Session, task_id: str) -> list[TaskHandoff]:
    handoffs = list(session.scalars(select(TaskHandoff).where(TaskHandoff.task_id == task_id)).all())
    handoffs.sort(key=lambda handoff: (-_ensure_datetime(handoff.created_at).timestamp(), handoff.id))
    return handoffs


def serialize_task_handoff(handoff: TaskHandoff) -> HandoffResponse:
    return HandoffResponse(
        id=handoff.id,
        task_id=handoff.task_id,
        author=handoff.author,
        summary=handoff.summary,
        changed_files=_load_json_list(handoff.changed_files),
        commands_run=_load_json_list(handoff.commands_run),
        tests_run=_load_json_list(handoff.tests_run),
        artifacts=_load_json_list(handoff.artifacts),
        attempted_but_failed=_load_json_list(handoff.attempted_but_failed),
        remaining_work=handoff.remaining_work,
        outcome=handoff.outcome,
        next_recommended_agent=handoff.next_recommended_agent,
        capabilities=_load_json_list(handoff.capabilities),
        created_at=_ensure_datetime(handoff.created_at),
    )


def serialize_task(task: Task) -> TaskResponse:
    notes = [
        TaskNoteResponse(
            id=note.id,
            body=note.body,
            author=note.author,
            created_at=_ensure_datetime(note.created_at),
        )
        for note in sorted(task.notes, key=lambda note: (_ensure_datetime(note.created_at), note.id))
    ]
    session = object_session(task)
    latest_handoff = None
    if session is not None:
        latest_handoff = next(iter(list_task_handoffs(session, task.id)), None)
    return TaskResponse(
        id=task.id,
        title=task.title,
        status=task.status,
        priority=task.priority,
        project=task.project,
        assignee=task.assignee,
        human_required=bool(task.human_required),
        assignee_type=task.assignee_type,
        blocker_reason=task.blocker_reason,
        complexity=task.complexity,
        impact=task.impact,
        effort=task.effort,
        risk=task.risk,
        description=task.description,
        acceptance_criteria=task.acceptance_criteria,
        source_filename=task.source_filename,
        source_line=task.source_line,
        import_batch_id=task.import_batch_id,
        source_title=task.source_title,
        notes=notes,
        latest_handoff=serialize_task_handoff(latest_handoff) if latest_handoff else None,
        created_at=_ensure_datetime(task.created_at),
        updated_at=_ensure_datetime(task.updated_at),
    )


def serialize_project(project: Project) -> ProjectResponse:
    return ProjectResponse(
        slug=project.slug,
        name=project.name,
        description=project.description,
        repo_url=project.repo_url,
        repo_path=project.repo_path,
        default_branch=project.default_branch,
        notes=project.notes,
        created_at=_ensure_datetime(project.created_at),
        updated_at=_ensure_datetime(project.updated_at),
    )


def serialize_agent_api_key(api_key: AgentApiKey) -> AgentApiKeyResponse:
    return AgentApiKeyResponse(
        id=api_key.id,
        name=api_key.name,
        description=api_key.description,
        role=api_key.role,
        key_prefix=api_key.key_prefix,
        created_at=_ensure_datetime(api_key.created_at),
        revoked_at=_ensure_optional_datetime(api_key.revoked_at),
    )


def serialize_created_agent_api_key(api_key: AgentApiKey, raw_key: str) -> AgentApiKeyCreateResponse:
    response = serialize_agent_api_key(api_key).model_dump()
    response["api_key"] = raw_key
    return AgentApiKeyCreateResponse(**response)


def get_tasks_by_idea(session: Session, idea_id: str) -> list[Task]:
    return list(session.scalars(task_query().where(Task.import_batch_id == idea_id)).all())


def promote_tasks(session: Session, idea: Idea, specs: list[PromoteTaskSpec]) -> tuple[list[Task], Idea]:
    created: list[Task] = []
    for spec in specs:
        task_payload = TaskCreate(
            title=spec.title,
            status=spec.status,
            priority=spec.priority,
            project=idea.project,
            assignee=idea.author,
            description=spec.description,
            acceptance_criteria=spec.acceptance_criteria,
            import_batch_id=idea.id,
        )
        created.append(create_task(session, task_payload))
    idea = archive_idea(session, idea)
    return created, idea


def serialize_idea(idea: Idea, session: Session) -> IdeaResponse:
    task_ids = [task.id for task in get_tasks_by_idea(session, idea.id)]
    return IdeaResponse(
        id=idea.id,
        title=idea.title,
        description=idea.description,
        project=idea.project,
        author=idea.author,
        archived_at=_ensure_optional_datetime(idea.archived_at),
        promoted_task_ids=task_ids,
        created_at=_ensure_datetime(idea.created_at),
        updated_at=_ensure_datetime(idea.updated_at),
    )


def find_import_duplicate(
    session: Session,
    *,
    project: str,
    title: str,
    source_filename: str | None = None,
    source_line: int | None = None,
) -> Task | None:
    if source_filename and source_line:
        task = session.scalars(
            task_query()
            .where(Task.project == project)
            .where(Task.source_filename == source_filename)
            .where(Task.source_line == source_line)
        ).first()
        if task:
            return task

    return session.scalars(
        task_query()
        .where(Task.project == project)
        .where(Task.title == title)
    ).first()


def _ensure_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _ensure_optional_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return _ensure_datetime(value)


def _decode_workspace_state(value: str | None) -> dict | None:
    if not value:
        return None
    try:
        decoded = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None
    return decoded if isinstance(decoded, dict) else None


def _join_webhook_events(events: list[str]) -> str:
    return ",".join(event.strip() for event in events if event.strip())


def _split_webhook_events(events: str) -> list[str]:
    return [event.strip() for event in events.split(",") if event.strip()]


def _load_json_list(value: str) -> list:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _task_keywords(task: Task) -> set[str]:
    words: set[str] = set()
    for value in (task.project, task.title, task.description):
        words.update(part.lower() for part in value.replace(",", " ").split() if part.strip())
    return words
