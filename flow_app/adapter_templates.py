"""Declarative adapter templates for common Flow agent families."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .schemas import AgentCreate


class AdapterTemplate(BaseModel):
    """Declarative adapter template for a specific agent family."""

    name: str = Field(
        ...,
        min_length=1,
        max_length=120,
        description="Unique template name, e.g. 'hermes', 'codex'",
    )
    family: Literal["hermes", "codex", "claude-code", "opencode", "opencrawl", "mcp", "custom"] = Field(
        ..., description="Adapter family this template belongs to"
    )
    description: str = Field("", max_length=500)
    agent_type: Literal["cli", "remote"] = "cli"
    command: str = Field("", description="Command template with {task_id}, {run_id}, {agent_id} placeholders")
    command_allowlist: str = ""
    env_allowlist: str = ""
    working_directory: str = ""
    capabilities: str = Field("", description="Comma-separated capability tags")
    dispatch_statuses: str = Field("todo", description="Comma-separated dispatch statuses")
    max_concurrency: int = Field(1, ge=1)
    heartbeat_timeout_seconds: int = Field(300, ge=1)
    stale_claim_timeout_seconds: int = Field(600, ge=1)
    notes: str = Field("", description="Setup notes and caveats for this template")

    @field_validator("command")
    @classmethod
    def reject_dangerous_commands(cls, value: str) -> str:
        """Reject command templates that include obvious shell-injection or destructive patterns."""
        blocked = {"rm -rf", "sudo", "> /dev/", "| bash", "$((", "`"}
        lower = value.lower()
        for pattern in blocked:
            if pattern in lower:
                raise ValueError(f"Command contains blocked pattern: {pattern}")
        return value

    @field_validator("working_directory")
    @classmethod
    def reject_path_traversal(cls, value: str) -> str:
        """Reject working directories with path traversal."""
        if ".." in value:
            raise ValueError("Working directory must not contain '..'")
        return value


class AdapterTemplateInstantiate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=120)
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
    on_collision: Literal["error", "skip", "update"] = "error"


class AdapterTemplatePreview(BaseModel):
    """Preview of what instantiate would create, without persisting."""

    name: str
    agent_type: str
    command: str
    capabilities: str
    dispatch_statuses: str
    env_allowlist: str
    working_directory: str
    max_concurrency: int
    heartbeat_timeout_seconds: int
    stale_claim_timeout_seconds: int
    description: str
    notes: str
    source_template: str
    overrides_applied: list[str]
    would_create: bool = True
    conflict_with: str | None = None


TEMPLATE_OVERRIDE_FIELDS = {
    "name",
    "description",
    "enabled",
    "agent_type",
    "capabilities",
    "command",
    "command_allowlist",
    "env_allowlist",
    "working_directory",
    "max_concurrency",
    "heartbeat_timeout_seconds",
    "stale_claim_timeout_seconds",
    "dispatch_statuses",
}


def adapter_template_overrides(payload: AdapterTemplateInstantiate) -> dict[str, object]:
    return {
        field: value
        for field, value in payload.model_dump(exclude_unset=True).items()
        if field in TEMPLATE_OVERRIDE_FIELDS and value is not None
    }


def agent_payload_from_template(template: AdapterTemplate, payload: AdapterTemplateInstantiate) -> AgentCreate:
    data = template.model_dump(exclude={"family", "notes"})
    data["name"] = payload.name
    data["description"] = template.description
    data.update(adapter_template_overrides(payload))
    return AgentCreate(**data)


def preview_from_template(
    template: AdapterTemplate,
    payload: AdapterTemplateInstantiate,
    *,
    conflict_with: str | None = None,
) -> AdapterTemplatePreview:
    agent_payload = agent_payload_from_template(template, payload)
    data = agent_payload.model_dump()
    data.update(
        {
            "notes": template.notes,
            "source_template": template.name,
            "overrides_applied": sorted(adapter_template_overrides(payload)),
            "would_create": conflict_with is None,
            "conflict_with": conflict_with,
        }
    )
    return AdapterTemplatePreview(**data)


BUILTIN_TEMPLATES: dict[str, AdapterTemplate] = {
    "hermes": AdapterTemplate(
        name="hermes",
        family="hermes",
        description="Hermes Agent CLI - delegates to sub-agents via Flow board",
        command="python -m flow_app.hermes_wrapper",
        command_allowlist="python,hermes",
        capabilities="hermes",
        dispatch_statuses="todo",
        max_concurrency=1,
        env_allowlist=(
            "FLOW_BASE_URL,FLOW_API_KEY,FLOW_TASK_ID,FLOW_RUN_ID,FLOW_PROJECT,HERMES_COMMAND,HERMES_TIMEOUT,"
            "HERMES_AGENT_NAME,HOME,PATH"
        ),
        notes="Set HERMES_COMMAND to the agent CLI (e.g. claude, codex). Requires Flow API key in environment. command_allowlist restricts to python/hermes prefixes.",
    ),
    "codex": AdapterTemplate(
        name="codex",
        family="codex",
        description="OpenAI Codex CLI - autonomous coding agent with sandbox",
        command="codex exec --dangerously-bypass-approvals-and-sandbox -m gpt-5.5",
        command_allowlist="codex",
        capabilities="codex",
        dispatch_statuses="todo",
        max_concurrency=1,
        notes="Requires OpenAI API key. command_allowlist locks execution to codex prefix. Override --model via env or allowlist.",
    ),
    "claude-code": AdapterTemplate(
        name="claude-code",
        family="claude-code",
        description="Claude Code CLI - interactive coding assistant",
        command="claude --dangerously-skip-permissions",
        command_allowlist="claude",
        capabilities="claude-code",
        dispatch_statuses="todo",
        max_concurrency=1,
        notes="Requires Anthropic API key or Claude subscription. command_allowlist locks execution to claude prefix.",
    ),
    "opencode": AdapterTemplate(
        name="opencode",
        family="opencode",
        description="OpenCode CLI - coding agent with model selection",
        command="opencode run --model opencode-go/qwen3.6-plus",
        command_allowlist="opencode",
        capabilities="opencode",
        dispatch_statuses="todo",
        max_concurrency=1,
        notes="Requires corresponding model API key. command_allowlist locks execution to opencode prefix.",
    ),
    "opencrawl": AdapterTemplate(
        name="opencrawl",
        family="opencrawl",
        description="Opencrawl agent - web crawling and data extraction",
        command="opencrawl run",
        command_allowlist="opencrawl",
        capabilities="opencrawl,crawl,extract",
        dispatch_statuses="",
        max_concurrency=2,
        notes="Requires opencrawl installation. Dispatch only via explicit trigger. command_allowlist locks to opencrawl prefix.",
    ),
    "mcp": AdapterTemplate(
        name="mcp",
        family="mcp",
        description="MCP Profile Agent - connects via Model Context Protocol",
        command="",
        command_allowlist="",
        capabilities="mcp",
        dispatch_statuses="",
        agent_type="remote",
        notes=(
            "MCP agents connect via protocol, not CLI command. Set agent_type to 'remote'. Configure MCP server "
            "URL via env_allowlist. command_allowlist is intentionally empty because remote agents do not execute CLI commands."
        ),
    ),
    "custom-script": AdapterTemplate(
        name="custom-script",
        family="custom",
        description="Custom script agent - runs arbitrary shell commands",
        command="bash /path/to/script.sh",
        command_allowlist="",
        capabilities="custom",
        dispatch_statuses="todo",
        max_concurrency=1,
        notes=(
            "Replace /path/to/script.sh with your actual script. Ensure script is executable. Limit env_allowlist "
            "to required vars. command_allowlist is intentionally empty because custom scripts vary; "
            "operators should set a restrictive allowlist matching their script prefix when configuring."
        ),
    ),
}
