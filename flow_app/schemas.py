from __future__ import annotations

from datetime import datetime
from enum import Enum
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .config import default_project
from .models import ApiKeyRole
from .ssrf import validate_webhook_url

TaskStatus = Literal["backlog", "todo", "doing", "review", "done"]
STATUSES: tuple[str, ...] = ("backlog", "todo", "doing", "review", "done")
NEXT_STATUS_ORDER: tuple[str, ...] = ("todo", "backlog")
SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,118}[a-z0-9]$|^[a-z0-9]$")
TASK_LINK_TYPES: set[str] = {"blocks", "depends_on", "related"}
BLOCKING_TASK_LINK_TYPES: set[str] = {"blocks", "depends_on"}
WORKSPACE_STRATEGIES: set[str] = {"git_worktree", "shared_dir", "scratch_dir"}


class AuditLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    actor_id: str
    action: str
    target_type: str
    target_id: str
    detail: str
    created_at: datetime


class AssigneeType(str, Enum):
    agent = "agent"
    human = "human"
    mixed = "mixed"


class Complexity(str, Enum):
    trivial = "trivial"
    small = "small"
    medium = "medium"
    large = "large"
    epic = "epic"


class Impact(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class Effort(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class Risk(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _clean_text(value: str | None) -> str:
    if value is None:
        return ""
    return value.strip()


def _clean_dispatch_statuses(value: str | None) -> str:
    cleaned = _clean_text(value)
    from .storage_helpers import get_comma_list

    statuses = get_comma_list(cleaned)
    invalid = [status for status in statuses if status not in STATUSES]
    if invalid:
        raise ValueError(f"Invalid dispatch status: {invalid[0]}.")
    return ",".join(statuses)


class TaskCreate(BaseModel):
    title: str
    status: TaskStatus = "backlog"
    priority: int = Field(default=50, ge=0, le=1000)
    project: str = Field(default_factory=default_project)
    assignee: str | None = None
    human_required: bool = False
    assignee_type: AssigneeType = AssigneeType.agent
    blocker_reason: str = ""
    complexity: Complexity = Complexity.small
    impact: Impact = Impact.medium
    effort: Effort = Effort.medium
    risk: Risk = Risk.low
    description: str = ""
    acceptance_criteria: str = ""
    source_filename: str | None = None
    source_line: int | None = Field(default=None, ge=1)
    import_batch_id: str | None = None
    source_title: str | None = None

    @field_validator("title", "project")
    @classmethod
    def require_nonempty(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Field is required.")
        return cleaned

    @field_validator("assignee")
    @classmethod
    def clean_assignee(cls, value: str | None) -> str | None:
        return _clean_optional(value)

    @field_validator("source_filename", "import_batch_id", "source_title")
    @classmethod
    def clean_optional_strings(cls, value: str | None) -> str | None:
        return _clean_optional(value)

    @field_validator("description", "acceptance_criteria", "blocker_reason")
    @classmethod
    def clean_multiline(cls, value: str | None) -> str:
        return _clean_text(value)


class TaskUpdate(BaseModel):
    title: str | None = None
    status: TaskStatus | None = None
    priority: int | None = Field(default=None, ge=0, le=1000)
    project: str | None = None
    assignee: str | None = None
    human_required: bool | None = None
    assignee_type: AssigneeType | None = None
    blocker_reason: str | None = None
    complexity: Complexity | None = None
    impact: Impact | None = None
    effort: Effort | None = None
    risk: Risk | None = None
    description: str | None = None
    acceptance_criteria: str | None = None
    source_filename: str | None = None
    source_line: int | None = Field(default=None, ge=1)
    import_batch_id: str | None = None
    source_title: str | None = None

    @field_validator("title", "project")
    @classmethod
    def clean_required_if_present(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Field cannot be blank.")
        return cleaned

    @field_validator("assignee")
    @classmethod
    def clean_assignee(cls, value: str | None) -> str | None:
        return _clean_optional(value)

    @field_validator("source_filename", "import_batch_id", "source_title")
    @classmethod
    def clean_optional_strings(cls, value: str | None) -> str | None:
        return _clean_optional(value)

    @field_validator("description", "acceptance_criteria", "blocker_reason")
    @classmethod
    def clean_multiline(cls, value: str | None) -> str:
        if value is None:
            return None
        return _clean_text(value)


class ClaimRequest(BaseModel):
    agent_name: str | None = None

    @field_validator("agent_name")
    @classmethod
    def clean_agent(cls, value: str | None) -> str | None:
        return _clean_optional(value)


class MoveRequest(BaseModel):
    status: TaskStatus


class NoteRequest(BaseModel):
    note: str
    author: str | None = None

    @field_validator("note")
    @classmethod
    def require_note(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Note is required.")
        return cleaned

    @field_validator("author")
    @classmethod
    def clean_author(cls, value: str | None) -> str | None:
        return _clean_optional(value)


class HandoffRequest(BaseModel):
    summary: str
    author: str | None = None
    changed_files: list[str] = Field(default_factory=list)
    commands_run: list[str] = Field(default_factory=list)
    tests_run: list[str] = Field(default_factory=list)
    artifacts: list[dict[str, str]] = Field(default_factory=list)
    attempted_but_failed: list[str] = Field(default_factory=list)
    remaining_work: str = ""
    outcome: str = "success"
    next_recommended_agent: str | None = None
    capabilities: list[str] = Field(default_factory=list)

    @field_validator("summary")
    @classmethod
    def require_summary(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Summary is required.")
        return cleaned

    @field_validator("author", "next_recommended_agent")
    @classmethod
    def clean_optional_strings(cls, value: str | None) -> str | None:
        return _clean_optional(value)

    @field_validator("remaining_work")
    @classmethod
    def clean_remaining_work(cls, value: str | None) -> str:
        return _clean_text(value)

    @field_validator("outcome")
    @classmethod
    def validate_outcome(cls, value: str) -> str:
        if value not in ("success", "partial", "failed"):
            raise ValueError("Outcome must be success, partial, or failed.")
        return value


class DoneRequest(BaseModel):
    summary: str
    author: str | None = None
    handoff: HandoffRequest | None = None

    @field_validator("summary")
    @classmethod
    def require_summary(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Summary is required.")
        return cleaned

    @field_validator("author")
    @classmethod
    def clean_author(cls, value: str | None) -> str | None:
        return _clean_optional(value)


class TaskNoteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    body: str
    author: str | None
    created_at: datetime


class HandoffResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    task_id: str
    author: str
    summary: str
    changed_files: list[str]
    commands_run: list[str]
    tests_run: list[str]
    artifacts: list[dict[str, str]]
    attempted_but_failed: list[str]
    remaining_work: str
    outcome: str
    next_recommended_agent: str | None
    capabilities: list[str]
    created_at: datetime


class TaskResponse(BaseModel):
    id: str
    title: str
    status: str
    priority: int
    project: str
    assignee: str | None
    human_required: bool
    assignee_type: str
    blocker_reason: str
    complexity: str
    impact: str
    effort: str
    risk: str
    description: str
    acceptance_criteria: str
    source_filename: str | None
    source_line: int | None
    import_batch_id: str | None
    source_title: str | None
    notes: list[TaskNoteResponse]
    latest_handoff: HandoffResponse | None = None
    created_at: datetime
    updated_at: datetime


class TaskListResponse(BaseModel):
    id: str
    title: str
    status: str
    priority: int
    project: str
    assignee: str | None
    human_required: bool
    assignee_type: str
    blocker_reason: str
    complexity: str
    impact: str
    effort: str
    risk: str
    description: str
    created_at: datetime
    updated_at: datetime


class PaginatedResponse(BaseModel):
    items: list
    total: int
    limit: int
    offset: int


class TaskLinkCreate(BaseModel):
    parent_id: str
    child_id: str
    link_type: str = "blocks"

    @field_validator("parent_id", "child_id")
    @classmethod
    def require_task_id(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Task ID is required.")
        return cleaned

    @field_validator("link_type")
    @classmethod
    def validate_link_type(cls, value: str) -> str:
        cleaned = value.strip()
        if cleaned not in TASK_LINK_TYPES:
            raise ValueError("Invalid task link type.")
        return cleaned

    @model_validator(mode="after")
    def reject_self_link(self):
        if self.parent_id == self.child_id:
            raise ValueError("Task cannot link to itself.")
        return self


class TaskLinkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    parent_id: str
    child_id: str
    link_type: str
    created_at: datetime


class TaskSummary(BaseModel):
    id: str
    title: str
    status: str
    priority: int
    project: str
    assignee: str | None = None


class WorkspaceConfigCreate(BaseModel):
    name: str
    strategy: str = "git_worktree"
    base_branch: str = "main"
    branch_prefix: str = "task-"
    root_dir: str = ""
    scratch_root: str = "/tmp/flow-scratch"
    description: str = ""
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def require_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Name is required.")
        return cleaned

    @field_validator("strategy")
    @classmethod
    def validate_strategy(cls, value: str) -> str:
        cleaned = value.strip()
        if cleaned not in WORKSPACE_STRATEGIES:
            raise ValueError("Invalid workspace strategy.")
        return cleaned

    @field_validator("base_branch", "branch_prefix", "root_dir", "scratch_root", "description")
    @classmethod
    def clean_text_fields(cls, value: str | None) -> str:
        return _clean_text(value)


class WorkspaceConfigUpdate(BaseModel):
    name: str | None = None
    strategy: str | None = None
    base_branch: str | None = None
    branch_prefix: str | None = None
    root_dir: str | None = None
    scratch_root: str | None = None
    description: str | None = None
    enabled: bool | None = None

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Name cannot be blank.")
        return cleaned

    @field_validator("strategy")
    @classmethod
    def validate_strategy(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if cleaned not in WORKSPACE_STRATEGIES:
            raise ValueError("Invalid workspace strategy.")
        return cleaned

    @field_validator("base_branch", "branch_prefix", "root_dir", "scratch_root", "description")
    @classmethod
    def clean_optional_text_fields(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _clean_text(value)


class WorkspaceConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    strategy: str
    base_branch: str
    branch_prefix: str
    root_dir: str
    scratch_root: str
    description: str
    enabled: bool
    created_at: datetime
    updated_at: datetime


class WebhookConfigCreate(BaseModel):
    name: str
    url: str
    events: list[str]
    project: str = "*"
    max_retries: int = 3
    retry_backoff_seconds: int = 60

    @field_validator("url")
    @classmethod
    def validate_url_safe(cls, value: str) -> str:
        return validate_webhook_url(value)


class WebhookConfigUpdate(BaseModel):
    name: str | None = None
    url: str | None = None
    events: list[str] | None = None
    active: int | None = None
    max_retries: int | None = None
    retry_backoff_seconds: int | None = None
    project: str | None = None

    @field_validator("url")
    @classmethod
    def validate_url_safe(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_webhook_url(value)


class WebhookConfigResponse(BaseModel):
    id: str
    name: str
    url: str
    events: list[str]
    active: int
    max_retries: int
    retry_backoff_seconds: int
    project: str
    created_at: datetime
    updated_at: datetime


class WebhookConfigCreateResponse(WebhookConfigResponse):
    secret: str


class WebhookDeliveryResponse(BaseModel):
    id: str
    webhook_id: str
    event: str
    status: str
    attempts: int
    next_attempt_at: datetime | None
    last_response_code: int | None
    last_response_body: str | None
    created_at: datetime
    updated_at: datetime


class WebhookDeliveryDetailResponse(WebhookDeliveryResponse):
    payload: str


class WebhookDeliveryRetryResponse(BaseModel):
    id: str
    status: str
    message: str


class NotificationProviderStatus(BaseModel):
    channel: str
    provider: str
    label: str
    configured: bool
    registered: bool
    config_keys: list[str]
    detail: str


class NotificationDeliveryResponse(BaseModel):
    id: str
    provider: str
    event: str
    task_id: str
    payload: str
    status: str
    attempts: int
    max_retries: int
    next_attempt_at: datetime | None
    last_response_code: int | None
    last_response_body: str | None
    created_at: datetime
    updated_at: datetime


class NotificationOverviewResponse(BaseModel):
    providers: list[NotificationProviderStatus]
    deliveries: list[NotificationDeliveryResponse]


class NotificationTestRequest(BaseModel):
    channel: str = "telegram"
    task_id: str
    message: str = "Flow notification test."

    @field_validator("channel", "task_id", "message")
    @classmethod
    def require_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Field is required.")
        return cleaned


class NotificationTestResponse(BaseModel):
    channel: str
    provider: str
    status: str
    message: str
    delivery: NotificationDeliveryResponse | None = None


class WorkspaceInfo(BaseModel):
    workspace_id: str
    strategy: str
    path: str
    branch: str | None
    ready: bool


class WorkspaceCleanupRequest(BaseModel):
    path: str
    strategy: str

    @field_validator("path", "strategy")
    @classmethod
    def require_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Field is required.")
        return cleaned


class DependencySummary(BaseModel):
    parents: list[TaskLinkResponse]
    children: list[TaskLinkResponse]
    blocked_by: list[TaskLinkResponse]
    blocking: list[TaskLinkResponse]
    parent_tasks: list[TaskSummary]
    child_tasks: list[TaskSummary]
    blocked_by_tasks: list[TaskSummary]
    blocking_tasks: list[TaskSummary]


class ProjectCreate(BaseModel):
    slug: str
    name: str | None = None
    description: str = ""
    repo_url: str = ""
    repo_path: str = ""
    default_branch: str = "main"
    notes: str = ""

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if not SLUG_PATTERN.fullmatch(cleaned):
            raise ValueError("Slug must use lowercase letters, numbers, and hyphens.")
        return cleaned

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str | None) -> str | None:
        return _clean_optional(value)

    @field_validator("description", "repo_url", "repo_path", "default_branch", "notes")
    @classmethod
    def clean_text_fields(cls, value: str | None) -> str:
        return _clean_text(value)


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    repo_url: str | None = None
    repo_path: str | None = None
    default_branch: str | None = None
    notes: str | None = None

    @field_validator("name", "description", "repo_url", "repo_path", "default_branch", "notes")
    @classmethod
    def clean_optional_text_fields(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _clean_text(value)


class ProjectResponse(BaseModel):
    slug: str
    name: str
    description: str
    repo_url: str
    repo_path: str
    default_branch: str
    notes: str
    created_at: datetime
    updated_at: datetime


class AgentApiKeyCreate(BaseModel):
    name: str
    description: str = ""
    role: ApiKeyRole = ApiKeyRole.read_only

    @field_validator("name")
    @classmethod
    def require_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Name is required.")
        return cleaned

    @field_validator("description")
    @classmethod
    def clean_description(cls, value: str | None) -> str:
        return _clean_text(value)


class AgentApiKeyResponse(BaseModel):
    id: str
    name: str
    description: str
    role: str
    key_prefix: str
    secret_ref: str | None = None
    created_at: datetime
    revoked_at: datetime | None


class AgentApiKeyCreateResponse(AgentApiKeyResponse):
    api_key: str


class IdeaCreate(BaseModel):
    title: str
    description: str = ""
    project: str = Field(default_factory=default_project)
    author: str | None = None

    @field_validator("title", "project")
    @classmethod
    def require_nonempty(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Field is required.")
        return cleaned

    @field_validator("author")
    @classmethod
    def clean_author(cls, value: str | None) -> str | None:
        return _clean_optional(value)

    @field_validator("description")
    @classmethod
    def clean_description(cls, value: str | None) -> str:
        return _clean_text(value)


class IdeaUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    project: str | None = None
    author: str | None = None

    @field_validator("title", "project")
    @classmethod
    def clean_required_if_present(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Field cannot be blank.")
        return cleaned

    @field_validator("author")
    @classmethod
    def clean_author(cls, value: str | None) -> str | None:
        return _clean_optional(value)

    @field_validator("description")
    @classmethod
    def clean_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _clean_text(value)


class PromoteTaskSpec(BaseModel):
    title: str
    status: TaskStatus = "backlog"
    priority: int = Field(default=50, ge=0, le=1000)
    description: str = ""
    acceptance_criteria: str = ""

    @field_validator("title")
    @classmethod
    def require_nonempty(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Field is required.")
        return cleaned

    @field_validator("description", "acceptance_criteria")
    @classmethod
    def clean_multiline(cls, value: str | None) -> str:
        return _clean_text(value)


class IdeaResponse(BaseModel):
    id: str
    title: str
    description: str
    project: str
    author: str | None
    archived_at: datetime | None
    promoted_task_ids: list[str] = []
    created_at: datetime
    updated_at: datetime


class RecurringTaskTemplateCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    project: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    description: str = ""
    acceptance_criteria: str = ""
    priority: int = 500
    status: str = "todo"
    complexity: str = "small"
    impact: str = "medium"
    effort: str = "medium"
    risk: str = "low"
    cadence: str = Field(..., min_length=1)
    next_run_at: datetime
    enabled: bool = True

    @field_validator("name", "project", "title")
    @classmethod
    def require_nonempty(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("must not be empty")
        return cleaned

    @field_validator("description", "acceptance_criteria")
    @classmethod
    def clean_text(cls, value: str | None) -> str:
        return _clean_text(value)

    @field_validator("cadence")
    @classmethod
    def validate_cadence(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("cadence must not be empty")
        return cleaned

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        if value not in STATUSES:
            raise ValueError(f"status must be one of {set(STATUSES)}")
        return value


class RecurringTaskTemplateUpdate(BaseModel):
    name: str | None = None
    project: str | None = None
    title: str | None = None
    description: str | None = None
    acceptance_criteria: str | None = None
    priority: int | None = None
    status: str | None = None
    complexity: str | None = None
    impact: str | None = None
    effort: str | None = None
    risk: str | None = None
    cadence: str | None = None
    next_run_at: datetime | None = None
    enabled: bool | None = None

    @field_validator("name", "project", "title", "description", "acceptance_criteria", "cadence")
    @classmethod
    def clean_optional(cls, value: str | None) -> str | None:
        return _clean_optional(value)

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value not in STATUSES:
            raise ValueError(f"status must be one of {set(STATUSES)}")
        return value


class RecurringTaskTemplateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    name: str
    project: str
    title: str
    description: str
    acceptance_criteria: str
    priority: int
    status: str
    complexity: str
    impact: str
    effort: str
    risk: str
    cadence: str
    next_run_at: datetime | None = None
    enabled: bool
    metadata: str = Field(validation_alias="metadata_")
    created_at: datetime | None = None
    updated_at: datetime | None = None


class MarkdownImportPreviewRequest(BaseModel):
    markdown: str
    source_filename: str | None = None
    default_project: str = Field(default_factory=default_project)
    default_status: TaskStatus = "backlog"
    default_priority: int = Field(default=50, ge=0, le=1000)

    @field_validator("markdown")
    @classmethod
    def require_markdown(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Markdown is required.")
        return value

    @field_validator("source_filename")
    @classmethod
    def clean_source_filename(cls, value: str | None) -> str | None:
        return _clean_optional(value)

    @field_validator("default_project")
    @classmethod
    def clean_default_project(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if not cleaned:
            raise ValueError("Default project is required.")
        return cleaned


class MarkdownImportItem(BaseModel):
    preview_id: str
    title: str
    status: TaskStatus
    priority: int = Field(ge=0, le=1000)
    project: str
    assignee: str | None = None
    description: str = ""
    acceptance_criteria: str = ""
    source_filename: str | None = None
    source_line: int | None = Field(default=None, ge=1)
    source_title: str | None = None
    duplicate: bool = False
    duplicate_task_id: str | None = None


class MarkdownImportPreviewResponse(BaseModel):
    items: list[MarkdownImportItem]


class MarkdownImportCommitRequest(BaseModel):
    items: list[MarkdownImportItem]


class MarkdownImportCommitResponse(BaseModel):
    import_batch_id: str
    created: list[TaskResponse]
    skipped: list[MarkdownImportItem]


class AgentCreate(BaseModel):
    name: str
    description: str = ""
    enabled: bool = True
    agent_type: str = "cli"
    capabilities: str = ""
    command: str = ""
    command_allowlist: str = ""
    env_allowlist: str = ""
    working_directory: str = ""
    max_concurrency: int = Field(default=1, ge=1)
    heartbeat_timeout_seconds: int = Field(default=300, ge=1)
    stale_claim_timeout_seconds: int = Field(default=600, ge=1)
    dispatch_statuses: str = "backlog,todo"

    @field_validator("name")
    @classmethod
    def require_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Name is required.")
        return cleaned

    @field_validator(
        "description",
        "capabilities",
        "command",
        "command_allowlist",
        "env_allowlist",
        "working_directory",
    )
    @classmethod
    def clean_text_fields(cls, value: str | None) -> str:
        return _clean_text(value)

    @field_validator("agent_type")
    @classmethod
    def validate_agent_type(cls, value: str | None) -> str:
        allowed = {"cli", "remote"}
        cleaned = (value or "cli").strip().lower()
        if cleaned not in allowed:
            raise ValueError(f"agent_type must be one of: {', '.join(sorted(allowed))}")
        return cleaned

    @field_validator("dispatch_statuses")
    @classmethod
    def validate_dispatch_statuses(cls, value: str | None) -> str:
        return _clean_dispatch_statuses(value)


class AgentUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    enabled: bool | None = None
    agent_type: str | None = None
    capabilities: str | None = None
    command: str | None = None
    command_allowlist: str | None = None
    env_allowlist: str | None = None
    working_directory: str | None = None
    max_concurrency: int | None = Field(default=None, ge=1)
    heartbeat_timeout_seconds: int | None = Field(default=None, ge=1)
    stale_claim_timeout_seconds: int | None = Field(default=None, ge=1)
    dispatch_statuses: str | None = None

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Name cannot be blank.")
        return cleaned

    @field_validator(
        "description",
        "capabilities",
        "command",
        "command_allowlist",
        "env_allowlist",
        "working_directory",
    )
    @classmethod
    def clean_optional_text_fields(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _clean_text(value)

    @field_validator("agent_type")
    @classmethod
    def validate_agent_type(cls, value: str | None) -> str | None:
        if value is None:
            return None
        allowed = {"cli", "remote"}
        cleaned = value.strip().lower()
        if cleaned not in allowed:
            raise ValueError(f"agent_type must be one of: {', '.join(sorted(allowed))}")
        return cleaned

    @field_validator("dispatch_statuses")
    @classmethod
    def validate_optional_dispatch_statuses(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _clean_dispatch_statuses(value)


class AgentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str
    enabled: bool
    agent_type: str
    capabilities: str
    command: str
    command_allowlist: str
    env_allowlist: str
    working_directory: str
    max_concurrency: int
    heartbeat_timeout_seconds: int
    stale_claim_timeout_seconds: int
    dispatch_statuses: str
    created_at: datetime
    updated_at: datetime


class RunnerCreate(BaseModel):
    name: str
    description: str = ""
    enabled: bool = True
    runner_type: str = "poll"
    capabilities: str = ""
    agent_names: str = ""
    lease_duration_seconds: int = Field(default=600, ge=60)
    heartbeat_interval_seconds: int = Field(default=60, ge=10)
    max_concurrent_leases: int = Field(default=1, ge=1)
    api_key_ref: str = ""

    @field_validator("name")
    @classmethod
    def require_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Name is required.")
        return cleaned

    @field_validator("description", "capabilities", "agent_names", "api_key_ref")
    @classmethod
    def clean_text_fields(cls, value: str | None) -> str:
        return _clean_text(value)

    @field_validator("runner_type")
    @classmethod
    def validate_runner_type(cls, value: str | None) -> str:
        allowed = {"poll", "push"}
        cleaned = (value or "poll").strip().lower()
        if cleaned not in allowed:
            raise ValueError(f"runner_type must be one of: {', '.join(sorted(allowed))}")
        return cleaned


class RunnerUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    enabled: bool | None = None
    runner_type: str | None = None
    capabilities: str | None = None
    agent_names: str | None = None
    lease_duration_seconds: int | None = Field(default=None, ge=60)
    heartbeat_interval_seconds: int | None = Field(default=None, ge=10)
    max_concurrent_leases: int | None = Field(default=None, ge=1)
    api_key_ref: str | None = None
    status: str | None = None

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Name cannot be blank.")
        return cleaned

    @field_validator("description", "capabilities", "agent_names", "api_key_ref")
    @classmethod
    def clean_optional_text_fields(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _clean_text(value)

    @field_validator("runner_type")
    @classmethod
    def validate_runner_type(cls, value: str | None) -> str | None:
        if value is None:
            return None
        allowed = {"poll", "push"}
        cleaned = value.strip().lower()
        if cleaned not in allowed:
            raise ValueError(f"runner_type must be one of: {', '.join(sorted(allowed))}")
        return cleaned

    @field_validator("status")
    @classmethod
    def validate_runner_status(cls, value: str | None) -> str | None:
        if value is None:
            return None
        allowed = {"online", "offline", "draining"}
        cleaned = value.strip().lower()
        if cleaned not in allowed:
            raise ValueError(f"status must be one of: {', '.join(sorted(allowed))}")
        return cleaned


class RunnerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str
    enabled: bool
    runner_type: str
    capabilities: str
    agent_names: str
    lease_duration_seconds: int
    heartbeat_interval_seconds: int
    max_concurrent_leases: int
    api_key_ref: str
    last_seen_at: datetime | None
    status: str
    created_at: datetime
    updated_at: datetime


class AgentRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    agent_id: str
    task_id: str
    status: str
    pid: int | None
    exit_code: int | None
    started_at: datetime | None
    finished_at: datetime | None
    last_heartbeat_at: datetime | None
    workspace_state: dict | None = None
    created_at: datetime
    updated_at: datetime


class LeaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    runner_id: str
    agent_run_id: str
    status: str
    leased_at: datetime
    expires_at: datetime
    last_heartbeat_at: datetime | None
    runner_pid: int | None
    runner_message: str
    completed_at: datetime | None
    task_id: str
    task_title: str
    agent_name: str
    agent_command: str
    agent_env_allowlist: str


class LeaseHeartbeatRequest(BaseModel):
    runner_pid: int | None = None
    message: str = ""


class LeaseCompleteRequest(BaseModel):
    exit_code: int = 0
    message: str = ""


AUTOMATION_TRIGGERS = {"task_created", "task_moved", "task_claimed", "task_completed", "task_blocked", "cron"}


def _validate_json_array(value: str | None, field_name: str) -> str:
    cleaned = _clean_text(value)
    try:
        parsed = json.loads(cleaned or "[]")
    except json.JSONDecodeError as exc:
        raise ValueError(f"{field_name} must be a valid JSON array.") from exc
    if not isinstance(parsed, list):
        raise ValueError(f"{field_name} must be a valid JSON array.")
    return cleaned or "[]"


class AutomationRuleCreate(BaseModel):
    name: str
    description: str = ""
    enabled: bool = True
    priority: int = Field(default=50, ge=0, le=1000)
    trigger: str
    trigger_config: str = ""
    conditions: str = "[]"
    actions: str = "[]"

    @field_validator("name")
    @classmethod
    def require_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Name is required.")
        return cleaned

    @field_validator("description", "trigger_config")
    @classmethod
    def clean_text_fields(cls, value: str | None) -> str:
        return _clean_text(value)

    @field_validator("trigger")
    @classmethod
    def validate_trigger(cls, value: str) -> str:
        cleaned = value.strip()
        if cleaned not in AUTOMATION_TRIGGERS:
            raise ValueError("Invalid automation trigger.")
        return cleaned

    @field_validator("conditions", "actions")
    @classmethod
    def validate_json_arrays(cls, value: str | None, info) -> str:
        return _validate_json_array(value, info.field_name)


class AutomationRuleUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    enabled: bool | None = None
    priority: int | None = Field(default=None, ge=0, le=1000)
    trigger: str | None = None
    trigger_config: str | None = None
    conditions: str | None = None
    actions: str | None = None

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Name cannot be blank.")
        return cleaned

    @field_validator("description", "trigger_config")
    @classmethod
    def clean_optional_text_fields(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _clean_text(value)

    @field_validator("trigger")
    @classmethod
    def validate_optional_trigger(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if cleaned not in AUTOMATION_TRIGGERS:
            raise ValueError("Invalid automation trigger.")
        return cleaned

    @field_validator("conditions", "actions")
    @classmethod
    def validate_optional_json_arrays(cls, value: str | None, info) -> str | None:
        if value is None:
            return None
        return _validate_json_array(value, info.field_name)


class AutomationRuleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str
    enabled: bool
    priority: int
    trigger: str
    trigger_config: str
    conditions: str
    actions: str
    last_run_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AutomationEvent(BaseModel):
    trigger: str
    task_id: str | None = None
    project: str | None = None
    data: dict | None = None

    @field_validator("trigger")
    @classmethod
    def validate_trigger(cls, value: str) -> str:
        cleaned = value.strip()
        if cleaned not in AUTOMATION_TRIGGERS:
            raise ValueError("Invalid automation trigger.")
        return cleaned

    @field_validator("task_id", "project")
    @classmethod
    def clean_optional_strings(cls, value: str | None) -> str | None:
        return _clean_optional(value)
