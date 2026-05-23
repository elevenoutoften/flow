from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import Any

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import default_project
from ..dispatcher import DispatchError, complete_run, dispatch_one, heartbeat_run
from ..models import ApiKeyRole, Idea, Task
from ..repository import (
    archive_idea,
    auto_promote_unblocked_children,
    create_agent,
    create_automation_rule,
    create_task_handoff,
    create_webhook_config,
    create_idea,
    create_task,
    create_task_link,
    create_workspace_config,
    delete_webhook_config,
    delete_task_link,
    evaluate_rules,
    get_agent,
    get_agent_run,
    get_automation_rule,
    get_webhook_config,
    get_webhook_delivery,
    get_idea,
    get_task,
    get_dependency_summary,
    get_workspace_config,
    list_webhook_deliveries,
    list_webhook_configs,
    list_agent_runs,
    list_agents,
    list_automation_rules,
    list_ideas,
    list_task_links,
    list_task_handoffs,
    list_tasks,
    list_workspace_configs,
    promote_tasks,
    serialize_automation_rule,
    serialize_idea,
    serialize_agent,
    serialize_agent_run,
    serialize_task_link,
    serialize_task_handoff,
    serialize_task,
    serialize_task_list,
    serialize_webhook_config,
    serialize_webhook_delivery,
    serialize_workspace_config,
    update_agent,
    update_automation_rule,
    update_webhook_config,
    update_idea,
    update_task,
    update_workspace_config,
)
from ..schemas import (
    AutomationEvent,
    AutomationRuleCreate,
    AutomationRuleUpdate,
    IdeaCreate,
    IdeaUpdate,
    HandoffRequest,
    PromoteTaskSpec,
    STATUSES,
    AgentCreate,
    AgentUpdate,
    TaskCreate,
    TaskLinkCreate,
    TaskUpdate,
    WORKSPACE_STRATEGIES,
    WorkspaceConfigCreate,
    WorkspaceConfigUpdate,
    WebhookConfigCreate,
    WebhookConfigUpdate,
)
from ..security import Actor, Permission, PermissionDenied, authorize_task_update as _authorize_task_update, has_permission, is_valid_transition
from ..notifications import WebhookNotificationProvider
from ..rules_engine import emit_event as emit_rule_event
from ..services.task import (
    InvalidTransitionError,
    SendbackContractError,
    TaskConcurrentModificationError,
    TaskNotFoundError,
    TaskService,
)
from ..telegram import TelegramNotificationProvider
from ..webhooks import WEBHOOK_EVENTS
from ..workspace import cleanup_workspace, provision_workspace


def _authorize_task_update_mcp(actor: Actor | None, task: Task, payload) -> None:
    try:
        _authorize_task_update(actor, task, payload)
    except PermissionDenied as exc:
        raise JsonRpcError(-32603, exc.detail)

PROTOCOL_VERSION = "2025-11-25"
PROJECT_NAME = "flow"
TASK_UPDATE_FIELDS = set(TaskUpdate.model_fields)
IDEA_UPDATE_FIELDS = set(IdeaUpdate.model_fields)
AGENT_UPDATE_FIELDS = set(AgentUpdate.model_fields)
RULE_UPDATE_FIELDS = set(AutomationRuleUpdate.model_fields)
WORKSPACE_CONFIG_UPDATE_FIELDS = set(WorkspaceConfigUpdate.model_fields)
WEBHOOK_UPDATE_FIELDS = {"url", "events", "active", "project"}
_webhook_notifier = WebhookNotificationProvider()
_telegram_notifier = TelegramNotificationProvider()


def _commit(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise JsonRpcError(-32603, f"Database conflict: {exc.orig}") from exc
    except Exception as exc:
        db.rollback()
        raise JsonRpcError(-32603, "Internal server error.") from exc


def _make_task_service(db: Session) -> TaskService:
    return TaskService(
        db=db,
        commit_fn=_commit,
        webhook_notifier=_webhook_notifier,
        telegram_notifier=_telegram_notifier,
        rule_emitter=emit_rule_event,
    )

TOOLS: list[dict[str, Any]] = [
    {
        "name": "flow_list_tasks",
        "title": "List Flow Tasks",
        "description": "List Flow tasks, optionally filtered by project or status.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Optional project slug."},
                "status": {
                    "type": "string",
                    "enum": list(STATUSES),
                    "description": "Optional task status filter.",
                },
            },
        },
    },
    {
        "name": "flow_get_task",
        "title": "Get Flow Task",
        "description": "Read a single Flow task by ID.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Flow task ID, for example flow_000001."}
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "flow_create_task",
        "title": "Create Flow Task",
        "description": "Create a Flow task.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "status": {"type": "string", "enum": list(STATUSES), "default": "backlog"},
                "priority": {"type": "integer", "minimum": 0, "maximum": 1000, "default": 50},
                "project": {"type": "string", "default": "default"},
                "description": {"type": "string", "default": ""},
                "acceptance_criteria": {"type": "string", "default": ""},
            },
            "required": ["title"],
        },
    },
    {
        "name": "flow_update_task",
        "title": "Update Flow Task",
        "description": "Update a Flow task.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "title": {"type": "string"},
                "status": {"type": "string", "enum": list(STATUSES)},
                "priority": {"type": "integer", "minimum": 0, "maximum": 1000},
                "project": {"type": "string"},
                "assignee": {"type": ["string", "null"]},
                "human_required": {"type": "boolean"},
                # Deprecated: use human_required instead.
                "assignee_type": {"type": "string", "enum": ["agent", "human", "mixed"]},
                "blocker_reason": {"type": "string"},
                "complexity": {"type": "string", "enum": ["trivial", "small", "medium", "large", "epic"]},
                "impact": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
                "effort": {"type": "string", "enum": ["low", "medium", "high"]},
                "risk": {"type": "string", "enum": ["low", "medium", "high"]},
                "description": {"type": "string"},
                "acceptance_criteria": {"type": "string"},
                "source_filename": {"type": ["string", "null"]},
                "source_line": {"type": ["integer", "null"], "minimum": 1},
                "import_batch_id": {"type": ["string", "null"]},
                "source_title": {"type": ["string", "null"]},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "flow_move_task",
        "title": "Move Flow Task",
        "description": "Move a Flow task to another status.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "status": {"type": "string", "enum": list(STATUSES)},
            },
            "required": ["task_id", "status"],
        },
    },
    {
        "name": "flow_link_task",
        "title": "Link Flow Tasks",
        "description": "Create a Flow task dependency or relationship link.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "parent_id": {"type": "string"},
                "child_id": {"type": "string"},
                "link_type": {"type": "string", "enum": ["blocks", "depends_on", "related"], "default": "blocks"},
            },
            "required": ["parent_id", "child_id"],
        },
    },
    {
        "name": "flow_unlink_task",
        "title": "Unlink Flow Task",
        "description": "Delete a Flow task link by ID.",
        "inputSchema": {
            "type": "object",
            "properties": {"link_id": {"type": "string"}},
            "required": ["link_id"],
        },
    },
    {
        "name": "flow_get_dependencies",
        "title": "Get Flow Dependencies",
        "description": "Read dependency summary for a Flow task.",
        "inputSchema": {
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
            "required": ["task_id"],
        },
    },
    {
        "name": "flow_list_task_links",
        "title": "List Flow Task Links",
        "description": "List Flow task links touching a task.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "link_type": {"type": "string", "enum": ["blocks", "depends_on", "related"]},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "flow_task_handoff",
        "description": "Submit a structured handoff record for a task, reporting what changed, what was tested, and what remains",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task ID"},
                "summary": {"type": "string", "description": "Summary of what was done"},
                "changed_files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of files changed",
                },
                "commands_run": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Commands executed",
                },
                "tests_run": {"type": "array", "items": {"type": "string"}, "description": "Tests run"},
                "outcome": {
                    "type": "string",
                    "enum": ["success", "partial", "failed"],
                    "description": "Outcome status",
                },
                "remaining_work": {"type": "string", "description": "Work that remains"},
                "next_recommended_agent": {
                    "type": "string",
                    "description": "Recommended next agent type",
                },
            },
            "required": ["task_id", "summary", "outcome"],
        },
    },
    {
        "name": "flow_get_task_handoffs",
        "description": "Get handoff records for a task",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task ID"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "flow_set_human_required",
        "title": "Set Flow Human Required",
        "description": "Set or clear the human-required blocker state on a Flow task.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "human_required": {"type": "boolean"},
                "blocker_reason": {"type": "string"},
                # Deprecated: use human_required instead.
                "assignee_type": {"type": "string", "enum": ["agent", "human", "mixed"]},
            },
            "required": ["task_id", "human_required"],
        },
    },
    {
        "name": "flow_board_summary",
        "title": "Flow Board Summary",
        "description": "Summarize Flow board task counts and human-required tasks.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Optional project slug."},
            },
        },
    },
    {
        "name": "flow_list_ideas",
        "title": "List Flow Ideas",
        "description": "List Flow ideas, optionally filtered by project and archive state.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Optional project slug."},
                "archived": {"type": "boolean", "default": False},
            },
        },
    },
    {
        "name": "flow_create_idea",
        "title": "Create Flow Idea",
        "description": "Create a Flow idea.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "description": {"type": "string", "default": ""},
                "project": {"type": "string", "default": "default"},
                "author": {"type": ["string", "null"]},
            },
            "required": ["title"],
        },
    },
    {
        "name": "flow_update_idea",
        "title": "Update Flow Idea",
        "description": "Update a Flow idea.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "idea_id": {"type": "string"},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "project": {"type": "string"},
                "author": {"type": ["string", "null"]},
            },
            "required": ["idea_id"],
        },
    },
    {
        "name": "flow_archive_idea",
        "title": "Archive Flow Idea",
        "description": "Archive a Flow idea.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "idea_id": {"type": "string"},
            },
            "required": ["idea_id"],
        },
    },
    {
        "name": "flow_promote_idea",
        "title": "Promote Flow Idea",
        "description": "Promote a Flow idea into linked tasks.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "idea_id": {"type": "string"},
                "tasks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "status": {"type": "string", "enum": list(STATUSES), "default": "backlog"},
                            "priority": {"type": "integer", "minimum": 0, "maximum": 1000, "default": 50},
                            "description": {"type": "string", "default": ""},
                            "acceptance_criteria": {"type": "string", "default": ""},
                        },
                        "required": ["title"],
                    },
                },
            },
            "required": ["idea_id", "tasks"],
        },
    },
    {
        "name": "flow_list_agents",
        "title": "List Flow Agents",
        "description": "List registered Flow agents.",
        "inputSchema": {
            "type": "object",
            "properties": {"enabled_only": {"type": "boolean", "default": False}},
        },
    },
    {
        "name": "flow_get_agent",
        "title": "Get Flow Agent",
        "description": "Read a single Flow agent by ID.",
        "inputSchema": {
            "type": "object",
            "properties": {"agent_id": {"type": "string"}},
            "required": ["agent_id"],
        },
    },
    {
        "name": "flow_create_agent",
        "title": "Create Flow Agent",
        "description": "Register a Flow agent.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "description": {"type": "string", "default": ""},
                "enabled": {"type": "boolean", "default": True},
                "agent_type": {"type": "string", "default": "cli"},
                "capabilities": {"type": "string", "default": ""},
                "command": {"type": "string", "default": ""},
                "env_allowlist": {"type": "string", "default": ""},
                "working_directory": {"type": "string", "default": ""},
                "max_concurrency": {"type": "integer", "minimum": 1, "default": 1},
                "heartbeat_timeout_seconds": {"type": "integer", "minimum": 1, "default": 300},
                "stale_claim_timeout_seconds": {"type": "integer", "minimum": 1, "default": 600},
                "dispatch_statuses": {
                    "type": "string",
                    "default": "backlog,todo",
                    "description": "Comma-separated task statuses this agent can dispatch.",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "flow_update_agent",
        "title": "Update Flow Agent",
        "description": "Update a registered Flow agent.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string"},
                "name": {"type": "string"},
                "description": {"type": "string"},
                "enabled": {"type": "boolean"},
                "agent_type": {"type": "string"},
                "capabilities": {"type": "string"},
                "command": {"type": "string"},
                "env_allowlist": {"type": "string"},
                "working_directory": {"type": "string"},
                "max_concurrency": {"type": "integer", "minimum": 1},
                "heartbeat_timeout_seconds": {"type": "integer", "minimum": 1},
                "stale_claim_timeout_seconds": {"type": "integer", "minimum": 1},
                "dispatch_statuses": {
                    "type": "string",
                    "description": "Comma-separated task statuses this agent can dispatch.",
                },
            },
            "required": ["agent_id"],
        },
    },
    {
        "name": "flow_list_workspace_configs",
        "title": "List Flow Workspace Configs",
        "description": "List Flow workspace isolation configs.",
        "inputSchema": {
            "type": "object",
            "properties": {"enabled_only": {"type": "boolean", "default": False}},
        },
    },
    {
        "name": "flow_create_webhook",
        "description": "Create a webhook config for event notifications",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Webhook name"},
                "url": {"type": "string", "description": "Target URL"},
                "events": {
                    "type": "array",
                    "items": {"type": "string", "enum": WEBHOOK_EVENTS},
                    "description": "Event types to subscribe to",
                },
                "project": {"type": "string", "description": "Project scope (default: *)"},
            },
            "required": ["name", "url", "events"],
        },
    },
    {
        "name": "flow_list_webhooks",
        "description": "List webhook configs",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Filter by project"},
            },
        },
    },
    {
        "name": "flow_get_webhook",
        "description": "Get a webhook config by ID",
        "inputSchema": {
            "type": "object",
            "properties": {"webhook_id": {"type": "string", "description": "Webhook config ID"}},
            "required": ["webhook_id"],
        },
    },
    {
        "name": "flow_update_webhook",
        "description": "Update webhook URL, events, active flag, or project scope",
        "inputSchema": {
            "type": "object",
            "properties": {
                "webhook_id": {"type": "string", "description": "Webhook config ID"},
                "url": {"type": "string", "description": "Target URL"},
                "events": {
                    "type": "array",
                    "items": {"type": "string", "enum": WEBHOOK_EVENTS},
                    "description": "Event types to subscribe to",
                },
                "active": {"type": "boolean", "description": "Whether delivery creation is enabled"},
                "project": {"type": "string", "description": "Project scope, or * for all projects"},
            },
            "required": ["webhook_id"],
        },
    },
    {
        "name": "flow_disable_webhook",
        "description": "Disable a webhook config",
        "inputSchema": {
            "type": "object",
            "properties": {"webhook_id": {"type": "string", "description": "Webhook config ID"}},
            "required": ["webhook_id"],
        },
    },
    {
        "name": "flow_delete_webhook",
        "description": "Delete a webhook config and its delivery logs",
        "inputSchema": {
            "type": "object",
            "properties": {"webhook_id": {"type": "string", "description": "Webhook config ID"}},
            "required": ["webhook_id"],
        },
    },
    {
        "name": "flow_list_webhook_deliveries",
        "description": "List delivery log entries for a webhook",
        "inputSchema": {
            "type": "object",
            "properties": {
                "webhook_id": {"type": "string", "description": "Webhook config ID"},
                "limit": {"type": "integer", "minimum": 1, "description": "Maximum deliveries to return"},
            },
            "required": ["webhook_id"],
        },
    },
    {
        "name": "flow_get_webhook_delivery",
        "description": "Get a webhook delivery log entry by ID",
        "inputSchema": {
            "type": "object",
            "properties": {"delivery_id": {"type": "string", "description": "Webhook delivery ID"}},
            "required": ["delivery_id"],
        },
    },
    {
        "name": "flow_get_workspace_config",
        "title": "Get Flow Workspace Config",
        "description": "Read a single workspace isolation config by ID.",
        "inputSchema": {
            "type": "object",
            "properties": {"config_id": {"type": "string"}},
            "required": ["config_id"],
        },
    },
    {
        "name": "flow_create_workspace_config",
        "title": "Create Flow Workspace Config",
        "description": "Create a workspace isolation config.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "strategy": {"type": "string", "enum": list(WORKSPACE_STRATEGIES), "default": "git_worktree"},
                "base_branch": {"type": "string", "default": "main"},
                "branch_prefix": {"type": "string", "default": "task/"},
                "root_dir": {"type": "string", "default": ""},
                "scratch_root": {"type": "string", "default": "/tmp/flow-scratch"},
                "description": {"type": "string", "default": ""},
                "enabled": {"type": "boolean", "default": True},
            },
            "required": ["name"],
        },
    },
    {
        "name": "flow_update_workspace_config",
        "title": "Update Flow Workspace Config",
        "description": "Update a workspace isolation config.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "config_id": {"type": "string"},
                "name": {"type": "string"},
                "strategy": {"type": "string", "enum": list(WORKSPACE_STRATEGIES)},
                "base_branch": {"type": "string"},
                "branch_prefix": {"type": "string"},
                "root_dir": {"type": "string"},
                "scratch_root": {"type": "string"},
                "description": {"type": "string"},
                "enabled": {"type": "boolean"},
            },
            "required": ["config_id"],
        },
    },
    {
        "name": "flow_provision_workspace",
        "title": "Provision Flow Workspace",
        "description": "Provision an isolated workspace for a Flow task.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "config_id": {"type": "string"},
                "task_id": {"type": "string"},
                "repo_path": {"type": "string"},
            },
            "required": ["config_id", "task_id"],
        },
    },
    {
        "name": "flow_cleanup_workspace",
        "title": "Cleanup Flow Workspace",
        "description": "Clean up a provisioned Flow workspace.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace_id": {"type": "string"},
                "strategy": {"type": "string", "enum": list(WORKSPACE_STRATEGIES)},
                "path": {"type": "string"},
            },
            "required": ["workspace_id", "strategy", "path"],
        },
    },
    {
        "name": "flow_dispatch_agent",
        "title": "Dispatch Flow Agent",
        "description": "Dispatch a registered agent to a task.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string"},
                "task_id": {"type": "string"},
                "api_key": {"type": "string", "default": ""},
                "base_url": {"type": "string", "default": ""},
            },
            "required": ["agent_id", "task_id"],
        },
    },
    {
        "name": "flow_list_agent_runs",
        "title": "List Flow Agent Runs",
        "description": "List agent runs with optional filters.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string"},
                "task_id": {"type": "string"},
                "status": {"type": "string"},
            },
        },
    },
    {
        "name": "flow_heartbeat",
        "title": "Heartbeat Flow Agent Run",
        "description": "Update an agent run heartbeat.",
        "inputSchema": {
            "type": "object",
            "properties": {"run_id": {"type": "string"}},
            "required": ["run_id"],
        },
    },
    {
        "name": "flow_complete_run",
        "title": "Complete Flow Agent Run",
        "description": "Mark an agent run complete.",
        "inputSchema": {
            "type": "object",
            "properties": {"run_id": {"type": "string"}, "exit_code": {"type": "integer"}},
            "required": ["run_id", "exit_code"],
        },
    },
    {
        "name": "flow_list_automation_rules",
        "title": "List Flow Automation Rules",
        "description": "List Flow automation rules.",
        "inputSchema": {
            "type": "object",
            "properties": {"enabled_only": {"type": "boolean", "default": False}},
        },
    },
    {
        "name": "flow_get_automation_rule",
        "title": "Get Flow Automation Rule",
        "description": "Read a single Flow automation rule by ID.",
        "inputSchema": {
            "type": "object",
            "properties": {"rule_id": {"type": "string"}},
            "required": ["rule_id"],
        },
    },
    {
        "name": "flow_create_automation_rule",
        "title": "Create Flow Automation Rule",
        "description": "Create a Flow automation rule.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "description": {"type": "string", "default": ""},
                "enabled": {"type": "boolean", "default": True},
                "priority": {"type": "integer", "minimum": 0, "maximum": 1000, "default": 50},
                "trigger": {"type": "string"},
                "trigger_config": {"type": "string", "default": ""},
                "conditions": {"type": "string", "default": "[]"},
                "actions": {"type": "string", "default": "[]"},
            },
            "required": ["name", "trigger"],
        },
    },
    {
        "name": "flow_update_automation_rule",
        "title": "Update Flow Automation Rule",
        "description": "Update a Flow automation rule.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "rule_id": {"type": "string"},
                "name": {"type": "string"},
                "description": {"type": "string"},
                "enabled": {"type": "boolean"},
                "priority": {"type": "integer", "minimum": 0, "maximum": 1000},
                "trigger": {"type": "string"},
                "trigger_config": {"type": "string"},
                "conditions": {"type": "string"},
                "actions": {"type": "string"},
            },
            "required": ["rule_id"],
        },
    },
    {
        "name": "flow_evaluate_rules",
        "title": "Evaluate Flow Automation Rules",
        "description": "Evaluate matching Flow automation rules for an event.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "trigger": {"type": "string"},
                "task_id": {"type": ["string", "null"]},
                "project": {"type": ["string", "null"]},
                "data": {"type": ["object", "null"]},
            },
            "required": ["trigger"],
        },
    },
]


class JsonRpcError(Exception):
    def __init__(self, code: int, message: str, data: Any | None = None):
        self.code = code
        self.message = message
        self.data = data
        super().__init__(message)


def package_version() -> str:
    try:
        return version(PROJECT_NAME)
    except PackageNotFoundError:
        return "0.1.0"


def handle_mcp_message(db: Session, message: Any, actor: Actor | None) -> Any | None:
    if isinstance(message, list):
        responses = [
            response
            for item in message
            if (response := handle_mcp_message(db, item, actor)) is not None
        ]
        return responses

    if not isinstance(message, dict):
        raise JsonRpcError(-32600, "Invalid JSON-RPC request.")

    request_id = message.get("id")
    method = message.get("method")
    params = message.get("params") or {}
    if message.get("jsonrpc") != "2.0" or not isinstance(method, str):
        raise JsonRpcError(-32600, "Invalid JSON-RPC request.")
    if not isinstance(params, dict):
        raise JsonRpcError(-32602, "Request params must be an object.")

    try:
        result = handle_mcp_request(db, method, params, actor)
    except JsonRpcError as exc:
        return error_response(request_id, exc)

    if request_id is None:
        return None
    return success_response(request_id, result)


def handle_mcp_request(db: Session, method: str, params: dict[str, Any], actor: Actor | None) -> dict[str, Any]:
    if method == "initialize":
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": PROJECT_NAME, "version": package_version()},
        }

    if method == "ping":
        return {}

    if method == "notifications/initialized":
        return {}

    if method == "tools/list":
        return {"tools": TOOLS}

    if method == "tools/call":
        return call_tool(db, params, actor)

    raise JsonRpcError(-32601, f"Unsupported MCP method: {method}")


def call_tool(db: Session, params: dict[str, Any], actor: Actor | None) -> dict[str, Any]:
    name = require_string(params.get("name"), "name")
    arguments = params.get("arguments") or {}
    if not isinstance(arguments, dict):
        raise JsonRpcError(-32602, "Tool arguments must be an object.")

    if name == "flow_list_tasks":
        require_tool_permission(actor, Permission.TASKS_READ)
        project = optional_string(arguments.get("project"))
        status = optional_string(arguments.get("status"))
        if status and status not in STATUSES:
            raise JsonRpcError(-32602, "Invalid task status.")
        tasks = [task_list_to_json(task) for task in list_tasks(db, project=project, status=status)]
        return tool_result(
            {"tasks": tasks, "count": len(tasks)},
            f"Found {len(tasks)} Flow task{'s' if len(tasks) != 1 else ''}.",
        )

    if name == "flow_get_task":
        require_tool_permission(actor, Permission.TASKS_READ)
        task_id = require_string(arguments.get("task_id"), "task_id")
        task = get_task(db, task_id)
        if task is None:
            raise JsonRpcError(-32602, f"Task not found: {task_id}")
        payload = task_to_json(task)
        return tool_result({"task": payload}, f"Found Flow task {payload['id']}: {payload['title']}")

    if name == "flow_create_task":
        actor = require_tool_permission(actor, Permission.TASKS_CREATE)
        try:
            payload = TaskCreate(
                title=arguments.get("title"),
                status=arguments.get("status", "backlog"),
                priority=arguments.get("priority", 50),
                project=arguments.get("project", default_project()),
                description=arguments.get("description", ""),
                acceptance_criteria=arguments.get("acceptance_criteria", ""),
            )
        except ValidationError as exc:
            raise JsonRpcError(-32602, "Invalid task payload.", exc.errors()) from exc
        task = _make_task_service(db).create_task(payload, actor)
        data = task_to_json(task)
        return tool_result({"task": data}, f"Created Flow task {data['id']}: {data['title']}")

    if name == "flow_update_task":
        task_id = require_string(arguments.get("task_id"), "task_id")
        task = require_task(db, task_id)
        try:
            payload = TaskUpdate(**update_arguments(arguments, TASK_UPDATE_FIELDS, "task_id"))
        except ValidationError as exc:
            raise JsonRpcError(-32602, "Invalid task payload.", exc.errors()) from exc
        _authorize_task_update_mcp(actor, task, payload)
        try:
            task = _make_task_service(db).update_task(task_id, payload, actor)
        except TaskNotFoundError as exc:
            raise JsonRpcError(-32602, exc.message) from exc
        except TaskConcurrentModificationError as exc:
            raise JsonRpcError(-32603, exc.message) from exc
        data = task_to_json(task)
        return tool_result({"task": data}, f"Updated Flow task {data['id']}: {data['title']}")

    if name == "flow_move_task":
        actor = require_tool_permission(actor, Permission.TASKS_MOVE)
        task_id = require_string(arguments.get("task_id"), "task_id")
        status = require_string(arguments.get("status"), "status")
        if status not in STATUSES:
            raise JsonRpcError(-32602, "Invalid task status.")
        try:
            task = _make_task_service(db).move_task(task_id, status, actor)
        except TaskNotFoundError as exc:
            raise JsonRpcError(-32602, exc.message) from exc
        except (InvalidTransitionError, SendbackContractError) as exc:
            raise JsonRpcError(-32603, exc.message) from exc
        except TaskConcurrentModificationError as exc:
            raise JsonRpcError(-32603, exc.message) from exc
        data = task_to_json(task)
        return tool_result({"task": data}, f"Moved Flow task {data['id']} to {data['status']}.")

    if name == "flow_link_task":
        require_tool_permission(actor, Permission.LINKS_MANAGE)
        try:
            payload = TaskLinkCreate(
                parent_id=arguments.get("parent_id"),
                child_id=arguments.get("child_id"),
                link_type=arguments.get("link_type", "blocks"),
            )
        except ValidationError as exc:
            raise JsonRpcError(-32602, "Invalid task link payload.", exc.errors()) from exc
        try:
            link = create_task_link(db, payload)
        except ValueError as exc:
            raise JsonRpcError(-32603, str(exc)) from exc
        db.commit()
        data = task_link_to_json(link)
        return tool_result({"link": data}, f"Linked Flow task {data['parent_id']} to {data['child_id']}.")

    if name == "flow_unlink_task":
        require_tool_permission(actor, Permission.LINKS_MANAGE)
        link_id = require_string(arguments.get("link_id"), "link_id")
        if not delete_task_link(db, link_id):
            raise JsonRpcError(-32602, f"Task link not found: {link_id}")
        db.commit()
        return tool_result({"deleted": True, "link_id": link_id}, f"Deleted Flow task link {link_id}.")

    if name == "flow_get_dependencies":
        require_tool_permission(actor, Permission.LINKS_READ)
        task_id = require_string(arguments.get("task_id"), "task_id")
        try:
            summary = get_dependency_summary(db, task_id)
        except ValueError as exc:
            raise JsonRpcError(-32602, str(exc)) from exc
        return tool_result(
            {"dependencies": summary.model_dump(mode="json")},
            f"Found dependencies for Flow task {task_id}.",
        )

    if name == "flow_list_task_links":
        require_tool_permission(actor, Permission.LINKS_READ)
        task_id = require_string(arguments.get("task_id"), "task_id")
        require_task(db, task_id)
        link_type = optional_string(arguments.get("link_type"))
        if link_type and link_type not in {"blocks", "depends_on", "related"}:
            raise JsonRpcError(-32602, "Invalid task link type.")
        links = [task_link_to_json(link) for link in list_task_links(db, task_id=task_id, link_type=link_type)]
        return tool_result(
            {"links": links, "count": len(links)},
            f"Found {len(links)} Flow task link{'s' if len(links) != 1 else ''}.",
        )

    if name == "flow_task_handoff":
        actor = require_tool_permission(actor, Permission.HANDOFF_CREATE)
        task_id = require_string(arguments.get("task_id"), "task_id")
        require_task(db, task_id)
        try:
            payload = HandoffRequest(
                summary=arguments.get("summary"),
                author=arguments.get("author") or actor.name,
                changed_files=arguments.get("changed_files", []),
                commands_run=arguments.get("commands_run", []),
                tests_run=arguments.get("tests_run", []),
                artifacts=arguments.get("artifacts", []),
                attempted_but_failed=arguments.get("attempted_but_failed", []),
                remaining_work=arguments.get("remaining_work", ""),
                outcome=arguments.get("outcome", "success"),
                next_recommended_agent=arguments.get("next_recommended_agent"),
                capabilities=arguments.get("capabilities", []),
            )
        except ValidationError as exc:
            raise JsonRpcError(-32602, "Invalid handoff payload.", exc.errors()) from exc
        handoff = create_task_handoff(
            db,
            task_id,
            payload.author or actor.name,
            payload.summary,
            payload.changed_files,
            payload.commands_run,
            payload.tests_run,
            payload.artifacts,
            payload.attempted_but_failed,
            payload.remaining_work,
            payload.outcome,
            payload.next_recommended_agent,
            payload.capabilities,
        )
        db.commit()
        data = handoff_to_json(handoff)
        return tool_result({"handoff": data}, f"Created Flow handoff {data['id']} for task {task_id}.")

    if name == "flow_get_task_handoffs":
        require_tool_permission(actor, Permission.HANDOFF_READ)
        task_id = require_string(arguments.get("task_id"), "task_id")
        require_task(db, task_id)
        handoffs = [handoff_to_json(handoff) for handoff in list_task_handoffs(db, task_id)]
        return tool_result(
            {"handoffs": handoffs, "count": len(handoffs)},
            f"Found {len(handoffs)} Flow handoff{'s' if len(handoffs) != 1 else ''}.",
        )

    if name == "flow_set_human_required":
        task_id = require_string(arguments.get("task_id"), "task_id")
        if not isinstance(arguments.get("human_required"), bool):
            raise JsonRpcError(-32602, "human_required must be a boolean.")
        task = require_task(db, task_id)
        changes: dict[str, Any] = {"human_required": arguments["human_required"]}
        if arguments["human_required"]:
            if "blocker_reason" in arguments:
                changes["blocker_reason"] = arguments["blocker_reason"]
            if "assignee_type" in arguments:
                changes["assignee_type"] = arguments["assignee_type"]
        else:
            changes["blocker_reason"] = ""
        try:
            payload = TaskUpdate(**changes)
        except ValidationError as exc:
            raise JsonRpcError(-32602, "Invalid task payload.", exc.errors()) from exc
        _authorize_task_update_mcp(actor, task, payload)
        try:
            task = _make_task_service(db).set_human_required(
                task_id,
                arguments["human_required"],
                actor,
                blocker_reason=arguments.get("blocker_reason"),
                assignee_type=arguments.get("assignee_type"),
            )
        except TaskNotFoundError as exc:
            raise JsonRpcError(-32602, exc.message) from exc
        except TaskConcurrentModificationError as exc:
            raise JsonRpcError(-32603, exc.message) from exc
        data = task_to_json(task)
        state = "set" if data["human_required"] else "cleared"
        return tool_result({"task": data}, f"Human-required state {state} for Flow task {data['id']}.")

    if name == "flow_board_summary":
        require_tool_permission(actor, Permission.TASKS_READ)
        project = optional_string(arguments.get("project"))
        tasks = [task_list_to_json(task) for task in list_tasks(db, project=project)]
        counts = {status: 0 for status in STATUSES}
        for task in tasks:
            counts[task["status"]] += 1
        human_required_tasks = [task for task in tasks if task["human_required"]]
        return tool_result(
            {"project": project, "counts_by_status": counts, "human_required_tasks": human_required_tasks},
            f"Summarized {len(tasks)} Flow task{'s' if len(tasks) != 1 else ''}.",
        )

    if name == "flow_list_ideas":
        require_tool_permission(actor, Permission.TASKS_READ)
        project = optional_string(arguments.get("project"))
        archived = optional_bool(arguments.get("archived"), default=False)
        ideas = [idea_to_json(idea, db) for idea in list_ideas(db, project=project, archived=archived)]
        return tool_result(
            {"ideas": ideas, "count": len(ideas)},
            f"Found {len(ideas)} Flow idea{'s' if len(ideas) != 1 else ''}.",
        )

    if name == "flow_create_idea":
        require_tool_permission(actor, Permission.TASKS_CREATE)
        try:
            payload = IdeaCreate(
                title=arguments.get("title"),
                description=arguments.get("description", ""),
                project=arguments.get("project", default_project()),
                author=arguments.get("author"),
            )
        except ValidationError as exc:
            raise JsonRpcError(-32602, "Invalid idea payload.", exc.errors()) from exc
        idea = create_idea(db, payload)
        db.commit()
        data = idea_to_json(idea, db)
        return tool_result({"idea": data}, f"Created Flow idea {data['id']}: {data['title']}")

    if name == "flow_update_idea":
        require_tool_permission(actor, Permission.TASKS_EDIT)
        idea_id = require_string(arguments.get("idea_id"), "idea_id")
        idea = require_idea(db, idea_id)
        try:
            payload = IdeaUpdate(**update_arguments(arguments, IDEA_UPDATE_FIELDS, "idea_id"))
        except ValidationError as exc:
            raise JsonRpcError(-32602, "Invalid idea payload.", exc.errors()) from exc
        idea = update_idea(db, idea, payload)
        db.commit()
        data = idea_to_json(idea, db)
        return tool_result({"idea": data}, f"Updated Flow idea {data['id']}: {data['title']}")

    if name == "flow_archive_idea":
        require_tool_permission(actor, Permission.TASKS_EDIT)
        idea_id = require_string(arguments.get("idea_id"), "idea_id")
        idea = archive_idea(db, require_idea(db, idea_id))
        db.commit()
        data = idea_to_json(idea, db)
        return tool_result({"idea": data}, f"Archived Flow idea {data['id']}: {data['title']}")

    if name == "flow_promote_idea":
        require_tool_permission(actor, Permission.TASKS_CREATE)
        idea_id = require_string(arguments.get("idea_id"), "idea_id")
        raw_tasks = arguments.get("tasks")
        if not isinstance(raw_tasks, list):
            raise JsonRpcError(-32602, "tasks must be a list.")
        try:
            specs = [PromoteTaskSpec(**item) for item in raw_tasks]
        except TypeError as exc:
            raise JsonRpcError(-32602, "Invalid promoted task payload.") from exc
        except ValidationError as exc:
            raise JsonRpcError(-32602, "Invalid promoted task payload.", exc.errors()) from exc
        _tasks, idea = promote_tasks(db, require_idea(db, idea_id), specs)
        db.commit()
        data = idea_to_json(idea, db)
        return tool_result(
            {"idea": data},
            f"Promoted Flow idea {data['id']} into {len(data['promoted_task_ids'])} linked task"
            f"{'s' if len(data['promoted_task_ids']) != 1 else ''}.",
        )

    if name == "flow_list_agents":
        require_tool_permission(actor, Permission.AGENT_READ)
        enabled_only = optional_bool(arguments.get("enabled_only"), default=False)
        agents = [agent_to_json(agent) for agent in list_agents(db, enabled_only=enabled_only)]
        return tool_result(
            {"agents": agents, "count": len(agents)},
            f"Found {len(agents)} Flow agent{'s' if len(agents) != 1 else ''}.",
        )

    if name == "flow_get_agent":
        require_tool_permission(actor, Permission.AGENT_READ)
        agent_id = require_string(arguments.get("agent_id"), "agent_id")
        agent = get_agent(db, agent_id)
        if agent is None:
            raise JsonRpcError(-32602, f"Agent not found: {agent_id}")
        data = agent_to_json(agent)
        return tool_result({"agent": data}, f"Found Flow agent {data['id']}: {data['name']}")

    if name == "flow_create_agent":
        require_tool_permission(actor, Permission.AGENT_MANAGE)
        try:
            payload = AgentCreate(
                name=arguments.get("name"),
                description=arguments.get("description", ""),
                enabled=arguments.get("enabled", True),
                agent_type=arguments.get("agent_type", "cli"),
                capabilities=arguments.get("capabilities", ""),
                command=arguments.get("command", ""),
                env_allowlist=arguments.get("env_allowlist", ""),
                working_directory=arguments.get("working_directory", ""),
                max_concurrency=arguments.get("max_concurrency", 1),
                heartbeat_timeout_seconds=arguments.get("heartbeat_timeout_seconds", 300),
                stale_claim_timeout_seconds=arguments.get("stale_claim_timeout_seconds", 600),
                dispatch_statuses=arguments.get("dispatch_statuses", "backlog,todo"),
            )
        except ValidationError as exc:
            raise JsonRpcError(-32602, "Invalid agent payload.", exc.errors()) from exc
        agent = create_agent(db, payload)
        db.commit()
        data = agent_to_json(agent)
        return tool_result({"agent": data}, f"Created Flow agent {data['id']}: {data['name']}")

    if name == "flow_update_agent":
        require_tool_permission(actor, Permission.AGENT_MANAGE)
        agent_id = require_string(arguments.get("agent_id"), "agent_id")
        agent = require_agent(db, agent_id)
        try:
            payload = AgentUpdate(**update_arguments(arguments, AGENT_UPDATE_FIELDS, "agent_id"))
        except ValidationError as exc:
            raise JsonRpcError(-32602, "Invalid agent payload.", exc.errors()) from exc
        agent = update_agent(db, agent, payload)
        db.commit()
        data = agent_to_json(agent)
        return tool_result({"agent": data}, f"Updated Flow agent {data['id']}: {data['name']}")

    if name == "flow_list_workspace_configs":
        require_tool_permission(actor, Permission.WORKSPACE_READ)
        enabled_only = optional_bool(arguments.get("enabled_only"), default=False)
        configs = [
            workspace_config_to_json(config)
            for config in list_workspace_configs(db, enabled_only=enabled_only)
        ]
        return tool_result(
            {"workspace_configs": configs, "count": len(configs)},
            f"Found {len(configs)} Flow workspace config{'s' if len(configs) != 1 else ''}.",
        )

    if name == "flow_create_webhook":
        require_tool_permission(actor, Permission.WEBHOOK_MANAGE)
        raw_events = arguments.get("events")
        if not isinstance(raw_events, list):
            raise JsonRpcError(-32602, "events must be a list.")
        if any(not isinstance(event, str) for event in raw_events):
            raise JsonRpcError(-32602, "events must contain only strings.")
        invalid = [event for event in raw_events if event not in WEBHOOK_EVENTS]
        if invalid:
            raise JsonRpcError(-32602, f"Invalid webhook event: {invalid[0]}")
        try:
            payload = WebhookConfigCreate(
                name=arguments.get("name"),
                url=arguments.get("url"),
                events=raw_events,
                project=arguments.get("project", "*"),
            )
        except ValidationError as exc:
            raise JsonRpcError(-32602, "Invalid webhook payload.", exc.errors()) from exc
        config, raw_secret = create_webhook_config(
            db,
            payload.name,
            payload.url,
            payload.events,
            payload.project,
            payload.max_retries,
            payload.retry_backoff_seconds,
        )
        db.commit()
        data = webhook_config_to_json(config)
        data["secret"] = raw_secret
        return tool_result({"webhook": data}, f"Created Flow webhook {data['id']}: {data['name']}")

    if name == "flow_list_webhooks":
        require_tool_permission(actor, Permission.WEBHOOK_READ)
        project = optional_string(arguments.get("project"))
        webhooks = [webhook_config_to_json(config) for config in list_webhook_configs(db, project=project)]
        return tool_result(
            {"webhooks": webhooks, "count": len(webhooks)},
            f"Found {len(webhooks)} Flow webhook{'s' if len(webhooks) != 1 else ''}.",
        )

    if name == "flow_get_webhook":
        require_tool_permission(actor, Permission.WEBHOOK_READ)
        webhook_id = require_string(arguments.get("webhook_id"), "webhook_id")
        config = require_webhook_config(db, webhook_id)
        data = webhook_config_to_json(config)
        return tool_result({"webhook": data}, f"Found Flow webhook {data['id']}: {data['name']}")

    if name == "flow_update_webhook":
        require_tool_permission(actor, Permission.WEBHOOK_MANAGE)
        webhook_id = require_string(arguments.get("webhook_id"), "webhook_id")
        config = require_webhook_config(db, webhook_id)
        updates = update_arguments(arguments, WEBHOOK_UPDATE_FIELDS, "webhook_id")
        if "active" in updates:
            if not isinstance(updates["active"], bool):
                raise JsonRpcError(-32602, "active must be a boolean.")
            updates["active"] = int(updates["active"])
        if "events" in updates:
            raw_events = updates["events"]
            if not isinstance(raw_events, list):
                raise JsonRpcError(-32602, "events must be a list.")
            if any(not isinstance(event, str) for event in raw_events):
                raise JsonRpcError(-32602, "events must contain only strings.")
            invalid = [event for event in raw_events if event not in WEBHOOK_EVENTS]
            if invalid:
                raise JsonRpcError(-32602, f"Invalid webhook event: {invalid[0]}")
        try:
            payload = WebhookConfigUpdate(**updates)
        except ValidationError as exc:
            raise JsonRpcError(-32602, "Invalid webhook payload.", exc.errors()) from exc
        update_data = {key: value for key, value in payload.model_dump(exclude_unset=True).items() if value is not None}
        config = update_webhook_config(db, config, update_data)
        db.commit()
        data = webhook_config_to_json(config)
        return tool_result({"webhook": data}, f"Updated Flow webhook {data['id']}: {data['name']}")

    if name == "flow_disable_webhook":
        require_tool_permission(actor, Permission.WEBHOOK_MANAGE)
        webhook_id = require_string(arguments.get("webhook_id"), "webhook_id")
        config = update_webhook_config(db, require_webhook_config(db, webhook_id), {"active": 0})
        db.commit()
        data = webhook_config_to_json(config)
        return tool_result({"webhook": data}, f"Disabled Flow webhook {data['id']}: {data['name']}")

    if name == "flow_delete_webhook":
        require_tool_permission(actor, Permission.WEBHOOK_MANAGE)
        webhook_id = require_string(arguments.get("webhook_id"), "webhook_id")
        config = require_webhook_config(db, webhook_id)
        delete_webhook_config(db, config)
        db.commit()
        return tool_result({"deleted": True, "webhook_id": webhook_id}, f"Deleted Flow webhook {webhook_id}.")

    if name == "flow_list_webhook_deliveries":
        require_tool_permission(actor, Permission.WEBHOOK_READ)
        webhook_id = require_string(arguments.get("webhook_id"), "webhook_id")
        require_webhook_config(db, webhook_id)
        limit = optional_int(arguments.get("limit"), "limit")
        deliveries = [webhook_delivery_to_json(delivery) for delivery in list_webhook_deliveries(db, webhook_id)]
        if limit is not None:
            deliveries = deliveries[:limit]
        return tool_result(
            {"deliveries": deliveries, "count": len(deliveries)},
            f"Found {len(deliveries)} Flow webhook deliver{'y' if len(deliveries) == 1 else 'ies'}.",
        )

    if name == "flow_get_webhook_delivery":
        require_tool_permission(actor, Permission.WEBHOOK_READ)
        delivery_id = require_string(arguments.get("delivery_id"), "delivery_id")
        delivery = get_webhook_delivery(db, delivery_id)
        if delivery is None:
            raise JsonRpcError(-32602, f"Webhook delivery not found: {delivery_id}")
        data = webhook_delivery_to_json(delivery)
        data["payload"] = delivery.payload
        return tool_result({"delivery": data}, f"Found Flow webhook delivery {data['id']}: {data['status']}")

    if name == "flow_get_workspace_config":
        require_tool_permission(actor, Permission.WORKSPACE_READ)
        config_id = require_string(arguments.get("config_id"), "config_id")
        config = require_workspace_config(db, config_id)
        data = workspace_config_to_json(config)
        return tool_result({"workspace_config": data}, f"Found Flow workspace config {data['id']}: {data['name']}")

    if name == "flow_create_workspace_config":
        require_tool_permission(actor, Permission.WORKSPACE_MANAGE)
        try:
            payload = WorkspaceConfigCreate(
                name=arguments.get("name"),
                strategy=arguments.get("strategy", "git_worktree"),
                base_branch=arguments.get("base_branch", "main"),
                branch_prefix=arguments.get("branch_prefix", "task/"),
                root_dir=arguments.get("root_dir", ""),
                scratch_root=arguments.get("scratch_root", "/tmp/flow-scratch"),
                description=arguments.get("description", ""),
                enabled=arguments.get("enabled", True),
            )
        except ValidationError as exc:
            raise JsonRpcError(-32602, "Invalid workspace config payload.", exc.errors()) from exc
        config = create_workspace_config(db, payload)
        db.commit()
        data = workspace_config_to_json(config)
        return tool_result({"workspace_config": data}, f"Created Flow workspace config {data['id']}: {data['name']}")

    if name == "flow_update_workspace_config":
        require_tool_permission(actor, Permission.WORKSPACE_MANAGE)
        config_id = require_string(arguments.get("config_id"), "config_id")
        config = require_workspace_config(db, config_id)
        try:
            payload = WorkspaceConfigUpdate(
                **update_arguments(arguments, WORKSPACE_CONFIG_UPDATE_FIELDS, "config_id")
            )
        except ValidationError as exc:
            raise JsonRpcError(-32602, "Invalid workspace config payload.", exc.errors()) from exc
        config = update_workspace_config(db, config, payload)
        db.commit()
        data = workspace_config_to_json(config)
        return tool_result({"workspace_config": data}, f"Updated Flow workspace config {data['id']}: {data['name']}")

    if name == "flow_provision_workspace":
        require_tool_permission(actor, Permission.WORKSPACE_MANAGE)
        config_id = require_string(arguments.get("config_id"), "config_id")
        task_id = require_string(arguments.get("task_id"), "task_id")
        config = require_workspace_config(db, config_id)
        result = provision_workspace(config, task_id, optional_string(arguments.get("repo_path")))
        return tool_result(
            {"workspace": result.__dict__},
            f"Provisioned Flow workspace {result.workspace_id} with strategy {result.strategy}.",
        )

    if name == "flow_cleanup_workspace":
        require_tool_permission(actor, Permission.WORKSPACE_MANAGE)
        workspace_id = require_string(arguments.get("workspace_id"), "workspace_id")
        strategy = require_string(arguments.get("strategy"), "strategy")
        path = require_string(arguments.get("path"), "path")
        cleaned = cleanup_workspace(workspace_id, strategy, path)
        return tool_result(
            {"workspace_id": workspace_id, "strategy": strategy, "path": path, "cleaned": cleaned},
            f"Cleaned Flow workspace {workspace_id}.",
        )

    if name == "flow_dispatch_agent":
        require_tool_permission(actor, Permission.DISPATCH)
        agent_id = require_string(arguments.get("agent_id"), "agent_id")
        task_id = require_string(arguments.get("task_id"), "task_id")
        try:
            run = dispatch_one(
                db,
                require_agent(db, agent_id),
                require_task(db, task_id),
                api_key=optional_string(arguments.get("api_key")) or "",
                base_url=optional_string(arguments.get("base_url")) or "",
            )
        except DispatchError as exc:
            raise JsonRpcError(-32603, str(exc)) from exc
        db.commit()
        data = agent_run_to_json(run)
        return tool_result({"run": data}, f"Dispatched Flow agent run {data['id']}.")

    if name == "flow_list_agent_runs":
        require_tool_permission(actor, Permission.TASKS_READ)
        runs = [
            agent_run_to_json(run)
            for run in list_agent_runs(
                db,
                agent_id=optional_string(arguments.get("agent_id")),
                task_id=optional_string(arguments.get("task_id")),
                status=optional_string(arguments.get("status")),
            )
        ]
        return tool_result(
            {"runs": runs, "count": len(runs)},
            f"Found {len(runs)} Flow agent run{'s' if len(runs) != 1 else ''}.",
        )

    if name == "flow_heartbeat":
        require_tool_permission(actor, Permission.DISPATCH)
        run_id = require_string(arguments.get("run_id"), "run_id")
        run = require_agent_run(db, run_id)
        run = heartbeat_run(db, run)
        db.commit()
        data = agent_run_to_json(run)
        return tool_result({"run": data}, f"Heartbeat recorded for Flow agent run {data['id']}.")

    if name == "flow_complete_run":
        require_tool_permission(actor, Permission.DISPATCH)
        run_id = require_string(arguments.get("run_id"), "run_id")
        exit_code = arguments.get("exit_code")
        if not isinstance(exit_code, int):
            raise JsonRpcError(-32602, "exit_code must be an integer.")
        run = complete_run(db, require_agent_run(db, run_id), exit_code)
        db.commit()
        data = agent_run_to_json(run)
        return tool_result({"run": data}, f"Completed Flow agent run {data['id']}.")

    if name == "flow_list_automation_rules":
        require_tool_permission(actor, Permission.RULES_READ)
        enabled_only = optional_bool(arguments.get("enabled_only"), default=False)
        rules = [automation_rule_to_json(rule) for rule in list_automation_rules(db, enabled_only=enabled_only)]
        return tool_result(
            {"rules": rules, "count": len(rules)},
            f"Found {len(rules)} Flow automation rule{'s' if len(rules) != 1 else ''}.",
        )

    if name == "flow_get_automation_rule":
        require_tool_permission(actor, Permission.RULES_READ)
        rule_id = require_string(arguments.get("rule_id"), "rule_id")
        rule = require_automation_rule(db, rule_id)
        data = automation_rule_to_json(rule)
        return tool_result({"rule": data}, f"Found Flow automation rule {data['id']}: {data['name']}")

    if name == "flow_create_automation_rule":
        require_tool_permission(actor, Permission.RULES_MANAGE)
        try:
            payload = AutomationRuleCreate(
                name=arguments.get("name"),
                description=arguments.get("description", ""),
                enabled=arguments.get("enabled", True),
                priority=arguments.get("priority", 50),
                trigger=arguments.get("trigger"),
                trigger_config=arguments.get("trigger_config", ""),
                conditions=arguments.get("conditions", "[]"),
                actions=arguments.get("actions", "[]"),
            )
        except ValidationError as exc:
            raise JsonRpcError(-32602, "Invalid automation rule payload.", exc.errors()) from exc
        rule = create_automation_rule(db, payload)
        db.commit()
        data = automation_rule_to_json(rule)
        return tool_result({"rule": data}, f"Created Flow automation rule {data['id']}: {data['name']}")

    if name == "flow_update_automation_rule":
        require_tool_permission(actor, Permission.RULES_MANAGE)
        rule_id = require_string(arguments.get("rule_id"), "rule_id")
        rule = require_automation_rule(db, rule_id)
        try:
            payload = AutomationRuleUpdate(**update_arguments(arguments, RULE_UPDATE_FIELDS, "rule_id"))
        except ValidationError as exc:
            raise JsonRpcError(-32602, "Invalid automation rule payload.", exc.errors()) from exc
        rule = update_automation_rule(db, rule, payload)
        db.commit()
        data = automation_rule_to_json(rule)
        return tool_result({"rule": data}, f"Updated Flow automation rule {data['id']}: {data['name']}")

    if name == "flow_evaluate_rules":
        require_tool_permission(actor, Permission.RULES_EVALUATE)
        data_arg = arguments.get("data")
        if data_arg is not None and not isinstance(data_arg, dict):
            raise JsonRpcError(-32602, "data must be an object.")
        try:
            event = AutomationEvent(
                trigger=arguments.get("trigger"),
                task_id=arguments.get("task_id"),
                project=arguments.get("project"),
                data=data_arg,
            )
        except ValidationError as exc:
            raise JsonRpcError(-32602, "Invalid automation event payload.", exc.errors()) from exc
        matches = evaluate_rules(db, event)
        db.commit()
        return tool_result(
            {"matches": matches, "count": len(matches)},
            f"Matched {len(matches)} Flow automation rule{'s' if len(matches) != 1 else ''}.",
        )

    raise JsonRpcError(-32601, f"Unknown Flow tool: {name}")


def require_tool_permission(actor: Actor | None, permission: Permission) -> Actor:
    if actor is None or not has_permission(actor, permission):
        raise JsonRpcError(-32603, f"Insufficient permission: {permission.value} is required.")
    return actor


def require_task(db: Session, task_id: str) -> Task:
    task = get_task(db, task_id)
    if task is None:
        raise JsonRpcError(-32602, f"Task not found: {task_id}")
    return task


def require_idea(db: Session, idea_id: str) -> Idea:
    idea = get_idea(db, idea_id)
    if idea is None:
        raise JsonRpcError(-32602, f"Idea not found: {idea_id}")
    return idea


def require_agent(db: Session, agent_id: str):
    agent = get_agent(db, agent_id)
    if agent is None:
        raise JsonRpcError(-32602, f"Agent not found: {agent_id}")
    return agent


def require_agent_run(db: Session, run_id: str):
    run = get_agent_run(db, run_id)
    if run is None:
        raise JsonRpcError(-32602, f"Agent run not found: {run_id}")
    return run


def require_automation_rule(db: Session, rule_id: str):
    rule = get_automation_rule(db, rule_id)
    if rule is None:
        raise JsonRpcError(-32602, f"Automation rule not found: {rule_id}")
    return rule


def require_workspace_config(db: Session, config_id: str):
    config = get_workspace_config(db, config_id)
    if config is None:
        raise JsonRpcError(-32602, f"Workspace config not found: {config_id}")
    return config


def require_webhook_config(db: Session, webhook_id: str):
    config = get_webhook_config(db, webhook_id)
    if config is None:
        raise JsonRpcError(-32602, f"Webhook not found: {webhook_id}")
    return config


def task_to_json(task: Task) -> dict[str, Any]:
    return serialize_task(task).model_dump(mode="json")


def task_list_to_json(task: Task) -> dict[str, Any]:
    return serialize_task_list(task).model_dump(mode="json")


def task_link_to_json(link) -> dict[str, Any]:
    return serialize_task_link(link).model_dump(mode="json")


def handoff_to_json(handoff) -> dict[str, Any]:
    return serialize_task_handoff(handoff).model_dump(mode="json")


def idea_to_json(idea: Idea, db: Session) -> dict[str, Any]:
    return serialize_idea(idea, db).model_dump(mode="json")


def agent_to_json(agent) -> dict[str, Any]:
    return serialize_agent(agent).model_dump(mode="json")


def agent_run_to_json(run) -> dict[str, Any]:
    return serialize_agent_run(run).model_dump(mode="json")


def automation_rule_to_json(rule) -> dict[str, Any]:
    return serialize_automation_rule(rule).model_dump(mode="json")


def workspace_config_to_json(config) -> dict[str, Any]:
    return serialize_workspace_config(config).model_dump(mode="json")


def webhook_config_to_json(config) -> dict[str, Any]:
    return serialize_webhook_config(config).model_dump(mode="json")


def webhook_delivery_to_json(delivery) -> dict[str, Any]:
    return serialize_webhook_delivery(delivery).model_dump(mode="json")


def update_arguments(arguments: dict[str, Any], allowed_fields: set[str], id_field: str) -> dict[str, Any]:
    return {field: value for field, value in arguments.items() if field != id_field and field in allowed_fields}


def require_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise JsonRpcError(-32602, f"{field} is required.")
    return value.strip()


def optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise JsonRpcError(-32602, "Optional string argument must be a string.")
    cleaned = value.strip()
    return cleaned or None


def optional_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise JsonRpcError(-32602, "Optional boolean argument must be a boolean.")
    return value


def optional_int(value: Any, field: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise JsonRpcError(-32602, f"{field} must be an integer.")
    if value < 1:
        raise JsonRpcError(-32602, f"{field} must be at least 1.")
    return value


def tool_result(structured_content: dict[str, Any], text: str) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": structured_content,
        "isError": False,
    }


def success_response(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def error_response(request_id: Any, exc: JsonRpcError) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": exc.code, "message": exc.message},
    }
    if exc.data is not None:
        payload["error"]["data"] = exc.data
    return payload


def exception_response(request_id: Any, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32603, "message": message},
    }
