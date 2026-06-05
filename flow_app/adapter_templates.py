"""Declarative adapter templates for common Flow agent families."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


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


BUILTIN_TEMPLATES: dict[str, AdapterTemplate] = {
    "hermes": AdapterTemplate(
        name="hermes",
        family="hermes",
        description="Hermes Agent CLI - delegates to sub-agents via Flow board",
        command="python -m flow_app.hermes_wrapper",
        capabilities="hermes",
        dispatch_statuses="todo",
        max_concurrency=1,
        env_allowlist=(
            "FLOW_BASE_URL,FLOW_API_KEY,FLOW_TASK_ID,FLOW_RUN_ID,FLOW_PROJECT,HERMES_COMMAND,HERMES_TIMEOUT,"
            "HERMES_AGENT_NAME,HOME,PATH"
        ),
        notes="Set HERMES_COMMAND to the agent CLI (e.g. claude, codex). Requires Flow API key in environment.",
    ),
    "codex": AdapterTemplate(
        name="codex",
        family="codex",
        description="OpenAI Codex CLI - autonomous coding agent with sandbox",
        command="codex exec --dangerously-bypass-approvals-and-sandbox -m gpt-5.5",
        capabilities="codex",
        dispatch_statuses="todo",
        max_concurrency=1,
        notes="Requires OpenAI API key. Use --dangerously-bypass-approvals-and-sandbox for autonomous mode.",
    ),
    "claude-code": AdapterTemplate(
        name="claude-code",
        family="claude-code",
        description="Claude Code CLI - interactive coding assistant",
        command="claude --dangerously-skip-permissions",
        capabilities="claude-code",
        dispatch_statuses="todo",
        max_concurrency=1,
        notes="Requires Anthropic API key or Claude subscription. --dangerously-skip-permissions for autonomous mode.",
    ),
    "opencode": AdapterTemplate(
        name="opencode",
        family="opencode",
        description="OpenCode CLI - coding agent with model selection",
        command="opencode run --model opencode-go/qwen3.6-plus",
        capabilities="opencode",
        dispatch_statuses="todo",
        max_concurrency=1,
        notes="Requires corresponding model API key. Specify model via --model flag.",
    ),
    "opencrawl": AdapterTemplate(
        name="opencrawl",
        family="opencrawl",
        description="OpenClaw agent - web crawling and data extraction",
        command="opencrawl run",
        capabilities="opencrawl,crawl,extract",
        dispatch_statuses="",
        max_concurrency=2,
        notes="Requires OpenClaw installation. Dispatch only via explicit trigger.",
    ),
    "mcp": AdapterTemplate(
        name="mcp",
        family="mcp",
        description="MCP Profile Agent - connects via Model Context Protocol",
        command="",
        capabilities="mcp",
        dispatch_statuses="",
        agent_type="remote",
        notes=(
            "MCP agents connect via protocol, not CLI command. Set agent_type to 'remote'. Configure MCP server "
            "URL via env_allowlist."
        ),
    ),
    "custom-script": AdapterTemplate(
        name="custom-script",
        family="custom",
        description="Custom script agent - runs arbitrary shell commands",
        command="bash /path/to/script.sh",
        capabilities="custom",
        dispatch_statuses="todo",
        max_concurrency=1,
        notes=(
            "Replace /path/to/script.sh with your actual script. Ensure script is executable. Limit env_allowlist "
            "to required vars."
        ),
    ),
}
