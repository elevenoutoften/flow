#!/usr/bin/env python3
"""Hermes wrapper for Flow agent dispatch.

Environment variables:
  FLOW_TASK_ID   - Task ID to work on (required)
  FLOW_PROJECT   - Project slug (required)
  FLOW_BASE_URL  - Flow API base URL, e.g. http://127.0.0.1:8100 (required)
  FLOW_API_KEY   - Flow API key for authentication (required)
  FLOW_RUN_ID    - Agent run ID for heartbeat/completion (required)
  HERMES_COMMAND - Hermes CLI command template (default: "hermes run")
  HERMES_TIMEOUT - Timeout in seconds for Hermes invocation (default: 600)
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import shlex
import socket
import subprocess
import sys
from typing import Any

from . import agent_client


REQUIRED_ENV_VARS = ("FLOW_TASK_ID", "FLOW_PROJECT", "FLOW_BASE_URL", "FLOW_API_KEY", "FLOW_RUN_ID")
DEFAULT_HERMES_COMMAND = "hermes run"
DEFAULT_HERMES_TIMEOUT = 600
MAX_NOTE_CHARS = 12000
MAX_CONTEXT_CHARS = 4000
MAX_DEPENDENCY_ITEMS = 10
MAX_HANDOFF_ITEMS = 5
MAX_HANDOFF_FIELD_CHARS = 500


@dataclass(frozen=True)
class WrapperConfig:
    task_id: str
    project: str
    base_url: str
    api_key: str
    run_id: str
    agent_name: str
    hermes_command: str
    hermes_timeout: int


class ConfigError(ValueError):
    """Raised when the wrapper environment is invalid."""


def load_config(environ: dict[str, str] | None = None) -> WrapperConfig:
    env = environ if environ is not None else os.environ
    missing = [name for name in REQUIRED_ENV_VARS if not env.get(name)]
    if missing:
        raise ConfigError(f"Missing required environment variable(s): {', '.join(missing)}")

    timeout_value = env.get("HERMES_TIMEOUT", str(DEFAULT_HERMES_TIMEOUT))
    try:
        hermes_timeout = int(timeout_value)
    except ValueError as exc:
        raise ConfigError("HERMES_TIMEOUT must be an integer.") from exc
    if hermes_timeout <= 0:
        raise ConfigError("HERMES_TIMEOUT must be greater than zero.")

    return WrapperConfig(
        task_id=env["FLOW_TASK_ID"],
        project=env["FLOW_PROJECT"],
        base_url=env["FLOW_BASE_URL"].rstrip("/"),
        api_key=env["FLOW_API_KEY"],
        run_id=env["FLOW_RUN_ID"],
        agent_name=env.get("HERMES_AGENT_NAME", "hermes").strip() or "hermes",
        hermes_command=env.get("HERMES_COMMAND", DEFAULT_HERMES_COMMAND).strip() or DEFAULT_HERMES_COMMAND,
        hermes_timeout=hermes_timeout,
    )


def _configure_agent_client(config: WrapperConfig) -> None:
    # This wrapper runs as a child of the Flow dispatcher on the same host. Use an
    # internal FLOW_BASE_URL such as http://127.0.0.1:8100; the public example.com
    # URL is Cloudflare-protected and may reject urllib clients.
    os.environ["FLOW_API_BASE_URL"] = config.base_url
    os.environ["FLOW_API_KEY"] = config.api_key


def flow_request(method: str, path: str, body: dict[str, Any] | None = None, *, query: dict[str, str] | None = None) -> Any:
    return agent_client._request(method, path, body=body, query=query)


def assignee_name(agent_name: str) -> str:
    suffix = os.environ.get("USER") or socket.gethostname()
    return f"{agent_name}-{suffix}" if suffix else agent_name


def get_task(task_id: str) -> dict[str, Any]:
    return flow_request("GET", f"/api/tasks/{task_id}")


def get_dependency_summary(task_id: str) -> dict[str, Any] | None:
    """Fetch dependency summary via the REST API. Returns None on failure."""
    try:
        return flow_request("GET", f"/api/tasks/{task_id}/dependencies")
    except Exception:
        return None


def get_task_handoffs(task_id: str) -> list[dict[str, Any]] | None:
    """Fetch handoff list via the REST API. Returns None on failure."""
    try:
        return flow_request("GET", f"/api/tasks/{task_id}/handoffs")
    except Exception:
        return None


def _truncate_context(value: str, limit: int = MAX_HANDOFF_FIELD_CHARS) -> str:
    """Truncate a single text field value with a visible marker."""
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "…[truncated]"


def _format_dependency_block(dep: dict[str, Any] | None) -> str:
    """Build a dependency context block for the dispatched agent prompt."""
    if dep is None:
        return ""
    lines: list[str] = []
    blocked_by = dep.get("blocked_by_tasks") or []
    blocking = dep.get("blocking_tasks") or []

    if blocked_by:
        lines.append("Blocked by:")
        for task in blocked_by[:MAX_DEPENDENCY_ITEMS]:
            lines.append(
                f"  - {task.get('id', '?')} ({task.get('title', '?')}) — status: {task.get('status', '?')}"
            )
        if len(blocked_by) > MAX_DEPENDENCY_ITEMS:
            lines.append(f"  … and {len(blocked_by) - MAX_DEPENDENCY_ITEMS} more blocked-by tasks [truncated]")

    if blocking:
        lines.append("Blocking:")
        for task in blocking[:MAX_DEPENDENCY_ITEMS]:
            lines.append(
                f"  - {task.get('id', '?')} ({task.get('title', '?')}) — status: {task.get('status', '?')}"
            )
        if len(blocking) > MAX_DEPENDENCY_ITEMS:
            lines.append(f"  … and {len(blocking) - MAX_DEPENDENCY_ITEMS} more blocking tasks [truncated]")

    return "\n".join(lines)


def _format_handoff_block(handoffs: list[dict[str, Any]] | None) -> str:
    """Build the latest handoff context block for the dispatched agent prompt."""
    if not handoffs:
        return ""
    lines: list[str] = []
    latest = handoffs[0]  # newest first from API

    lines.append(f"Latest handoff by {latest.get('author', '?')}:")
    lines.append(f"  Outcome: {latest.get('outcome', '?')}")
    summary = latest.get("summary", "")
    if summary:
        lines.append(f"  Summary: {_truncate_context(summary)}")

    remaining = latest.get("remaining_work", "")
    if remaining:
        lines.append(f"  Remaining work: {_truncate_context(remaining)}")

    attempted = latest.get("attempted_but_failed") or []
    if attempted:
        lines.append(f"  Attempted but failed: {_truncate_context(', '.join(attempted))}")

    tests_run = latest.get("tests_run") or []
    if tests_run:
        lines.append(f"  Tests run: {', '.join(tests_run[:MAX_HANDOFF_ITEMS])}")
        if len(tests_run) > MAX_HANDOFF_ITEMS:
            lines.append(f"    … and {len(tests_run) - MAX_HANDOFF_ITEMS} more [truncated]")

    next_agent = latest.get("next_recommended_agent")
    if next_agent:
        lines.append(f"  Next recommended agent: {next_agent}")

    changed_files = latest.get("changed_files") or []
    if changed_files:
        lines.append(f"  Changed files: {', '.join(changed_files[:MAX_HANDOFF_ITEMS])}")
        if len(changed_files) > MAX_HANDOFF_ITEMS:
            lines.append(f"    … and {len(changed_files) - MAX_HANDOFF_ITEMS} more [truncated]")

    # Show previous handoffs count if there are more
    if len(handoffs) > 1:
        lines.append(f"  ({len(handoffs) - 1} earlier handoff(s) not shown)")

    result = "\n".join(lines)
    return _truncate_to(result, MAX_CONTEXT_CHARS)


def _truncate_to(value: str, limit: int) -> str:
    """Bound the entire block to a max character count with a visible marker."""
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "\n…[context truncated]"


def build_task_context_bundle(task: dict[str, Any]) -> str:
    """Build a transport-agnostic context bundle with dependencies and handoff info.

    This helper is designed to be reusable by any wrapper/agent client, not just
    the Hermes wrapper. It fetches dependency summaries and handoff records via
    the Flow REST API and returns a formatted string ready to be injected into a
    dispatched agent prompt.

    Gracefully falls back when dependencies or handoffs are absent or unavailable.
    """
    task_id = task.get("id", "?")
    dep = get_dependency_summary(task_id)
    handoffs = get_task_handoffs(task_id)

    parts: list[str] = []
    dep_block = _format_dependency_block(dep)
    if dep_block:
        parts.extend(["## Dependency Context", dep_block, ""])

    handoff_block = _format_handoff_block(handoffs)
    if handoff_block:
        parts.extend(["## Handoff Context", handoff_block, ""])

    return "\n".join(parts).strip()


def claim_task(task_id: str, assignee: str) -> dict[str, Any]:
    return flow_request("POST", f"/api/tasks/{task_id}/claim", {"agent_name": assignee})


def post_note(task_id: str, note: str, author: str) -> dict[str, Any]:
    return flow_request("POST", f"/api/tasks/{task_id}/note", {"note": _truncate(note), "author": author})


def heartbeat(run_id: str) -> dict[str, Any]:
    return flow_request("POST", f"/api/agent-runs/{run_id}/heartbeat")


def complete_run(run_id: str, exit_code: int) -> dict[str, Any]:
    return flow_request("POST", f"/api/agent-runs/{run_id}/complete", query={"exit_code": str(exit_code)})


def move_to_review(task_id: str) -> dict[str, Any]:
    return flow_request("POST", f"/api/tasks/{task_id}/move", {"status": "review"})


def create_handoff(
    task_id: str,
    *,
    author: str,
    summary: str,
    changed_files: list[str],
    outcome: str,
    command: str,
    remaining_work: str = "",
    attempted_but_failed: list[str] | None = None,
) -> dict[str, Any]:
    return flow_request(
        "POST",
        f"/api/tasks/{task_id}/handoff",
        {
            "author": author,
            "summary": summary,
            "changed_files": changed_files,
            "commands_run": [command],
            "tests_run": [],
            "artifacts": [],
            "attempted_but_failed": attempted_but_failed or [],
            "remaining_work": remaining_work,
            "outcome": outcome,
            "next_recommended_agent": "reviewer" if outcome == "success" else None,
            "capabilities": ["hermes", "dogfood"],
        },
    )


def build_prompt(task: dict[str, Any], config: WrapperConfig) -> str:
    title = task.get("title") or config.task_id
    description = task.get("description") or "(none)"
    acceptance = task.get("acceptance_criteria") or "(none)"
    parts = [
        "You are Hermes running inside Flow's dogfood dispatcher.",
        f"Task ID: {config.task_id}",
        f"Project: {config.project}",
        f"Title: {title}",
        "",
        "Description:",
        description,
        "",
        "Acceptance Criteria:",
        acceptance,
        "",
    ]
    ctx = build_task_context_bundle(task)
    if ctx:
        parts.append(ctx)
        parts.append("")
    parts.append(
        "Implement the task in the current workspace. When finished, leave a concise summary of the work performed."
    )
    return "\n".join(parts)


def run_hermes(command: str, prompt: str, timeout: int) -> subprocess.CompletedProcess[str]:
    args = shlex.split(command) + [prompt]
    if not args:
        raise ConfigError("HERMES_COMMAND must not be empty.")
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def git_changed_files() -> list[str]:
    try:
        result = subprocess.run(["git", "diff", "--name-only", "HEAD"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def summarize_output(result: subprocess.CompletedProcess[str]) -> str:
    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    parts = [f"Hermes exited with code {result.returncode}."]
    if stdout:
        parts.extend(["", "stdout:", stdout])
    if stderr:
        parts.extend(["", "stderr:", stderr])
    return _truncate("\n".join(parts))


def _truncate(value: str, limit: int = MAX_NOTE_CHARS) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 32].rstrip() + "\n...[truncated by Flow wrapper]"


def execute(config: WrapperConfig) -> int:
    _configure_agent_client(config)
    author = config.agent_name
    assignee = assignee_name(config.agent_name)
    exit_code = 1

    try:
        claim_task(config.task_id, assignee)
        post_note(config.task_id, f"Starting Hermes work on {config.task_id}.", author)
        heartbeat(config.run_id)

        task = get_task(config.task_id)
        prompt = build_prompt(task, config)
        try:
            result = run_hermes(config.hermes_command, prompt, config.hermes_timeout)
        except subprocess.TimeoutExpired:
            exit_code = 124
            timeout_note = f"Hermes timed out after {config.hermes_timeout} seconds."
            heartbeat(config.run_id)
            post_note(config.task_id, timeout_note, author)
            create_handoff(
                config.task_id,
                author=author,
                summary=timeout_note,
                changed_files=git_changed_files(),
                outcome="failed",
                command=config.hermes_command,
                remaining_work="Hermes timed out; task remains in doing for follow-up.",
                attempted_but_failed=[config.hermes_command],
            )
            return exit_code
        exit_code = result.returncode
        summary = summarize_output(result)
        changed_files = git_changed_files()

        heartbeat(config.run_id)
        if exit_code == 0:
            post_note(config.task_id, summary, author)
            create_handoff(
                config.task_id,
                author=author,
                summary=summary,
                changed_files=changed_files,
                outcome="success",
                command=config.hermes_command,
            )
            move_to_review(config.task_id)
        else:
            post_note(config.task_id, f"Hermes failed.\n\n{summary}", author)
            create_handoff(
                config.task_id,
                author=author,
                summary=summary,
                changed_files=changed_files,
                outcome="failed",
                command=config.hermes_command,
                remaining_work="Hermes exited non-zero; task remains in doing for follow-up.",
                attempted_but_failed=[config.hermes_command],
            )
    finally:
        try:
            heartbeat(config.run_id)
        finally:
            complete_run(config.run_id, exit_code)

    return exit_code


def main() -> int:
    try:
        config = load_config()
        return execute(config)
    except ConfigError as exc:
        print(f"hermes_wrapper error: {exc}", file=sys.stderr)
        return 2
    except subprocess.TimeoutExpired as exc:
        print(f"hermes_wrapper error: Hermes timed out after {exc.timeout} seconds.", file=sys.stderr)
        return 124
    except Exception as exc:
        print(f"hermes_wrapper error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
