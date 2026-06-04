from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .schemas import AgentCreate


class AdapterTemplate(BaseModel):
    """Declarative template for creating agent adapter entries.

    Templates describe agent families (Hermes, Codex, Claude Code, etc.)
    without containing secrets. They can be listed, previewed, and imported
    into Agent records via the API.
    """

    name: str = Field(..., description="Template identifier, e.g. 'hermes-agent'")
    display_name: str = Field(..., description="Human-readable name, e.g. 'Hermes Agent'")
    description: str = Field(..., description="What this adapter type does")
    agent_type: Literal["cli", "mcp", "api"] = Field(default="cli", description="Agent transport type")
    command: str = Field(..., description="Command template. Use {workspace} as placeholder for working directory.")
    command_allowlist: list[str] = Field(default_factory=list, description="Allowed sub-commands")
    env_allowlist: list[str] = Field(default_factory=list, description="Allowed environment variables")
    capabilities: list[str] = Field(default_factory=list, description="Capability tags this agent supports")
    dispatch_statuses: list[str] = Field(default_factory=lambda: ["todo"], description="Statuses this agent picks up")
    working_directory: str = Field(default="", description="Default working directory ({workspace} resolves at runtime)")
    max_concurrency: int = Field(default=1, ge=1, description="Max concurrent tasks")
    heartbeat_timeout_seconds: int = Field(default=300, ge=1)
    stale_claim_timeout_seconds: int = Field(default=600, ge=1)
    recommended_role: Literal["admin", "reviewer", "worker", "observer", "runner"] = Field(
        default="worker", description="Recommended API key role for this adapter"
    )
    setup_notes: str = Field(default="", description="Setup instructions and prerequisites")
    tags: list[str] = Field(default_factory=list, description="Search/filter tags")


BUILTIN_TEMPLATES: list[AdapterTemplate] = [
    AdapterTemplate(
        name="hermes-agent",
        display_name="Hermes Agent",
        description="Full-featured Hermes agent with CLI dispatch, web search, terminal, and delegation capabilities.",
        agent_type="cli",
        command="hermes run --workspace {workspace}",
        command_allowlist=["hermes"],
        env_allowlist=["HERMES_API_KEY", "HERMES_HOME", "PATH"],
        capabilities=["search", "terminal", "delegation", "code", "web"],
        dispatch_statuses=["todo", "doing"],
        working_directory="{workspace}",
        max_concurrency=2,
        recommended_role="worker",
        setup_notes="Requires Hermes CLI installed and authenticated. Set HERMES_API_KEY in environment.",
        tags=["hermes", "full-stack", "cli"],
    ),
    AdapterTemplate(
        name="codex",
        display_name="OpenAI Codex Agent",
        description="OpenAI Codex CLI agent for autonomous code generation and review tasks.",
        agent_type="cli",
        command="codex --workspace {workspace}",
        command_allowlist=["codex"],
        env_allowlist=["OPENAI_API_KEY", "PATH"],
        capabilities=["code", "review", "terminal"],
        dispatch_statuses=["todo"],
        working_directory="{workspace}",
        max_concurrency=1,
        recommended_role="worker",
        setup_notes="Requires OpenAI Codex CLI installed. Set OPENAI_API_KEY.",
        tags=["codex", "openai", "code"],
    ),
    AdapterTemplate(
        name="claude-code",
        display_name="Claude Code Agent",
        description="Anthropic Claude Code CLI for autonomous code tasks with agentic tool use.",
        agent_type="cli",
        command="claude --workspace {workspace}",
        command_allowlist=["claude"],
        env_allowlist=["ANTHROPIC_API_KEY", "PATH"],
        capabilities=["code", "review", "terminal", "web"],
        dispatch_statuses=["todo", "review"],
        working_directory="{workspace}",
        max_concurrency=1,
        recommended_role="worker",
        setup_notes="Requires Claude Code CLI installed and authenticated. Set ANTHROPIC_API_KEY.",
        tags=["claude", "anthropic", "code"],
    ),
    AdapterTemplate(
        name="mcp-profile",
        display_name="MCP Profile Agent",
        description="MCP (Model Context Protocol) profile for tool-calling agents connected via MCP server.",
        agent_type="mcp",
        command="",
        command_allowlist=[],
        env_allowlist=["MCP_SERVER_URL", "PATH"],
        capabilities=["mcp", "tools"],
        dispatch_statuses=["todo"],
        working_directory="",
        max_concurrency=3,
        recommended_role="worker",
        setup_notes="Connects to an MCP-compatible tool server. Set MCP_SERVER_URL to the server endpoint.",
        tags=["mcp", "tools", "profile"],
    ),
    AdapterTemplate(
        name="custom-script",
        display_name="Custom Script Agent",
        description="Generic custom script adapter for running arbitrary CLI tools as agents.",
        agent_type="cli",
        command="/usr/local/bin/my-agent {workspace}",
        command_allowlist=[],
        env_allowlist=[],
        capabilities=["custom"],
        dispatch_statuses=["todo"],
        working_directory="{workspace}",
        max_concurrency=1,
        recommended_role="worker",
        setup_notes="Replace command and allowlists with your custom agent's details. Ensure the script is executable.",
        tags=["custom", "script", "generic"],
    ),
    AdapterTemplate(
        name="reviewer",
        display_name="Reviewer Agent",
        description="Read-only reviewer that picks up tasks in review status. Least-privilege by default.",
        agent_type="cli",
        command="echo review-check --workspace {workspace}",
        command_allowlist=["echo"],
        env_allowlist=[],
        capabilities=["review"],
        dispatch_statuses=["review"],
        max_concurrency=2,
        recommended_role="reviewer",
        setup_notes="Replace the command with your review tool. Uses the 'reviewer' role for read-heavy access.",
        tags=["reviewer", "read-only", "least-privilege"],
    ),
]


class TemplateValidationError(Exception):
    """Raised when a template contains unsafe or invalid fields."""


def get_template(name: str) -> AdapterTemplate | None:
    """Find a built-in template by name."""
    return next((template for template in BUILTIN_TEMPLATES if template.name == name), None)


def validate_template(template: AdapterTemplate) -> list[str]:
    """Validate a template for safety. Returns a list of warnings."""
    warnings: list[str] = []
    secret_patterns = ["password", "secret", "token", "key=", "api_key", "apikey", "bearer"]

    for field_name in ("command", "working_directory"):
        value = getattr(template, field_name).lower()
        for pattern in secret_patterns:
            if pattern in value:
                warnings.append(f"{field_name} contains potential secret pattern: '{pattern}'")

    for env_var in template.env_allowlist:
        lower = env_var.lower()
        if "=" in env_var or any(pattern in lower for pattern in ("password", "secret", "token", "bearer")):
            warnings.append(f"env_allowlist contains suspicious variable: '{env_var}'")

    dangerous_chars = ["|", ";", "&", "$", "`", ">", "<", "!!", "\n", "'", '"']
    command = template.command.replace("{workspace}", "")
    for char in dangerous_chars:
        if char in command:
            warnings.append(f"command contains dangerous metacharacter: '{char}'")

    for entry in template.command_allowlist:
        if "/" in entry or ".." in entry:
            warnings.append(f"command_allowlist entry contains path: '{entry}'")

    if template.working_directory and template.working_directory != "{workspace}" and template.working_directory.startswith("/"):
        warnings.append("working_directory contains absolute path")

    return warnings


def agent_create_from_template(template: AdapterTemplate, overrides: AgentCreate | None = None) -> AgentCreate:
    """Build an AgentCreate payload from a template and optional overrides."""
    return AgentCreate(
        name=overrides.name if overrides and overrides.name else template.name,
        description=overrides.description if overrides and overrides.description else template.description,
        enabled=overrides.enabled if overrides else True,
        agent_type=overrides.agent_type if overrides and overrides.agent_type else template.agent_type,
        capabilities=overrides.capabilities if overrides and overrides.capabilities else ",".join(template.capabilities),
        command=overrides.command if overrides and overrides.command else template.command,
        command_allowlist=(
            overrides.command_allowlist if overrides and overrides.command_allowlist else ",".join(template.command_allowlist)
        ),
        env_allowlist=overrides.env_allowlist if overrides and overrides.env_allowlist else ",".join(template.env_allowlist),
        working_directory=(
            overrides.working_directory if overrides and overrides.working_directory else template.working_directory
        ),
        max_concurrency=(
            overrides.max_concurrency if overrides and overrides.max_concurrency else template.max_concurrency
        ),
        heartbeat_timeout_seconds=(
            overrides.heartbeat_timeout_seconds
            if overrides and overrides.heartbeat_timeout_seconds
            else template.heartbeat_timeout_seconds
        ),
        stale_claim_timeout_seconds=(
            overrides.stale_claim_timeout_seconds
            if overrides and overrides.stale_claim_timeout_seconds
            else template.stale_claim_timeout_seconds
        ),
        dispatch_statuses=(
            overrides.dispatch_statuses if overrides and overrides.dispatch_statuses else ",".join(template.dispatch_statuses)
        ),
    )
