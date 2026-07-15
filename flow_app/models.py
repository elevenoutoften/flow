from __future__ import annotations

from datetime import datetime, timezone
import enum

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ApiKeyRole(str, enum.Enum):
    admin = "admin"
    architect = "architect"
    implementer = "implementer"
    reviewer = "reviewer"
    read_only = "read_only"


class FlowCounter(Base):
    __tablename__ = "flow_counters"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Project(Base):
    __tablename__ = "projects"

    slug: Mapped[str] = mapped_column(String(120), primary_key=True)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    repo_url: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    repo_path: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    default_branch: Mapped[str] = mapped_column(String(120), nullable=False, default="main")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class AgentApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="read_only")
    key_prefix: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=50, index=True)
    project: Mapped[str] = mapped_column(String(120), nullable=False, default="default", index=True)
    assignee: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    claimer_key_id: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    human_required: Mapped[bool] = mapped_column(Integer, nullable=False, default=0)
    assignee_type: Mapped[str] = mapped_column(String(24), nullable=False, default="agent")
    blocker_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    complexity: Mapped[str] = mapped_column(String(24), nullable=False, default="small")
    impact: Mapped[str] = mapped_column(String(24), nullable=False, default="medium")
    effort: Mapped[str] = mapped_column(String(24), nullable=False, default="medium")
    risk: Mapped[str] = mapped_column(String(24), nullable=False, default="low")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    acceptance_criteria: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_filename: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    import_batch_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    source_template_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("recurring_task_templates.id"), nullable=True, default=None, index=True
    )
    metadata_: Mapped[str] = mapped_column("metadata", Text, nullable=False, default="{}", server_default="{}")
    source_title: Mapped[str | None] = mapped_column(String(240), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    notes: Mapped[list["TaskNote"]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        order_by="TaskNote.created_at",
    )
    source_template: Mapped["RecurringTaskTemplate | None"] = relationship(foreign_keys=[source_template_id])


class TaskNote(Base):
    __tablename__ = "task_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str | None] = mapped_column(String(120), nullable=True)
    author_key_id: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    task: Mapped[Task] = relationship(back_populates="notes")


class TaskHandoff(Base):
    __tablename__ = "task_handoffs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    author: Mapped[str] = mapped_column(String(120), nullable=False)
    author_key_id: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    changed_files: Mapped[str] = mapped_column(Text, nullable=False, default="")
    commands_run: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tests_run: Mapped[str] = mapped_column(Text, nullable=False, default="")
    artifacts: Mapped[str] = mapped_column(Text, nullable=False, default="")
    attempted_but_failed: Mapped[str] = mapped_column(Text, nullable=False, default="")
    remaining_work: Mapped[str] = mapped_column(Text, nullable=False, default="")
    outcome: Mapped[str] = mapped_column(String(24), nullable=False, default="success")
    next_recommended_agent: Mapped[str | None] = mapped_column(String(120), nullable=True)
    capabilities: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(180), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    enabled: Mapped[bool] = mapped_column(Integer, nullable=False, default=1)
    agent_type: Mapped[str] = mapped_column(String(24), nullable=False, default="cli")
    capabilities: Mapped[str] = mapped_column(Text, nullable=False, default="")
    command: Mapped[str] = mapped_column(Text, nullable=False, default="")
    command_allowlist: Mapped[str] = mapped_column(Text, nullable=False, default="")
    env_allowlist: Mapped[str] = mapped_column(Text, nullable=False, default="")
    working_directory: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    max_concurrency: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    heartbeat_timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    stale_claim_timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=600)
    dispatch_statuses: Mapped[str] = mapped_column(Text, nullable=False, default="backlog,todo")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending", index=True)
    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scoped_key_id: Mapped[str | None] = mapped_column(String(32), nullable=True, default=None)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    workspace_state: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class AutomationRule(Base):
    __tablename__ = "automation_rules"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    enabled: Mapped[bool] = mapped_column(Integer, nullable=False, default=1)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    trigger: Mapped[str] = mapped_column(String(60), nullable=False)
    trigger_config: Mapped[str] = mapped_column(Text, nullable=False, default="")
    conditions: Mapped[str] = mapped_column(Text, nullable=False, default="")
    actions: Mapped[str] = mapped_column(Text, nullable=False, default="")
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class TaskLink(Base):
    __tablename__ = "task_links"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    parent_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    child_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    link_type: Mapped[str] = mapped_column(String(24), nullable=False, default="blocks")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class WorkspaceConfig(Base):
    __tablename__ = "workspace_configs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(180), nullable=False, unique=True)
    strategy: Mapped[str] = mapped_column(String(24), nullable=False, default="git_worktree")
    base_branch: Mapped[str] = mapped_column(String(240), nullable=False, default="main")
    branch_prefix: Mapped[str] = mapped_column(String(120), nullable=False, default="task-")
    root_dir: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    scratch_root: Mapped[str] = mapped_column(String(500), nullable=False, default="/tmp/flow-scratch")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    enabled: Mapped[bool] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class WebhookConfig(Base):
    __tablename__ = "webhook_configs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    secret: Mapped[str] = mapped_column(String(512), nullable=False)
    secret_encrypted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    events: Mapped[str] = mapped_column(Text, nullable=False, default="")
    active: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    retry_backoff_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    project: Mapped[str] = mapped_column(String(120), nullable=False, default="*")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    webhook_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("webhook_configs.id"), nullable=False, index=True
    )
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_response_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_response_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class NotificationDelivery(Base):
    __tablename__ = "notification_deliveries"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    provider: Mapped[str] = mapped_column(String(24), nullable=False)
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    task_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_response_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_response_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class Runner(Base):
    __tablename__ = "runners"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(180), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    enabled: Mapped[bool] = mapped_column(Integer, nullable=False, default=1)
    runner_type: Mapped[str] = mapped_column(String(24), nullable=False, default="poll")
    capabilities: Mapped[str] = mapped_column(Text, nullable=False, default="")
    agent_names: Mapped[str] = mapped_column(Text, nullable=False, default="")
    lease_duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=600)
    heartbeat_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    max_concurrent_leases: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    api_key_ref: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="offline", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class RunnerLease(Base):
    __tablename__ = "runner_leases"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    runner_id: Mapped[str] = mapped_column(ForeignKey("runners.id", ondelete="CASCADE"), nullable=False, index=True)
    agent_run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active", index=True)
    leased_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    runner_pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    runner_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(48), nullable=False, default="")
    target_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)


class Idea(Base):
    __tablename__ = "ideas"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    project: Mapped[str] = mapped_column(String(120), nullable=False, default="default", index=True)
    author: Mapped[str | None] = mapped_column(String(120), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class RecurringTaskTemplate(Base):
    __tablename__ = "recurring_task_templates"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    project: Mapped[str] = mapped_column(String(120), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    acceptance_criteria: Mapped[str] = mapped_column(Text, default="", server_default="")
    priority: Mapped[int] = mapped_column(Integer, default=500)
    status: Mapped[str] = mapped_column(String(24), default="todo", server_default="todo")
    complexity: Mapped[str] = mapped_column(String(24), default="small", server_default="small")
    impact: Mapped[str] = mapped_column(String(24), default="medium", server_default="medium")
    effort: Mapped[str] = mapped_column(String(24), default="medium", server_default="medium")
    risk: Mapped[str] = mapped_column(String(24), default="low", server_default="low")
    cadence: Mapped[str] = mapped_column(String(60), nullable=False)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    enabled: Mapped[bool] = mapped_column(Integer, default=1, server_default="1")
    metadata_: Mapped[str] = mapped_column("metadata", Text, default="{}", server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
