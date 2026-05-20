from __future__ import annotations

import json
import os
import urllib.request
import urllib.parse
from typing import Any


def _get_base_url() -> str:
    return os.environ.get("FLOW_API_BASE_URL", "http://localhost:8100").rstrip("/")


def _request(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    query: dict[str, str] | None = None,
) -> Any:
    url = _get_base_url() + path
    if query:
        url += "?" + urllib.parse.urlencode(query)

    data = json.dumps(body).encode("utf-8") if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")

    api_key = os.environ.get("FLOW_API_KEY")
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")

    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def flow_list_projects() -> list[dict[str, Any]]:
    return _request("GET", "/api/projects")


def flow_get_project(slug: str) -> dict[str, Any]:
    return _request("GET", f"/api/projects/{slug}")


def flow_create_task(
    title: str,
    *,
    status: str = "backlog",
    priority: int = 50,
    project: str = "default",
    description: str = "",
    acceptance_criteria: str = "",
) -> dict[str, Any]:
    return _request(
        "POST",
        "/api/tasks",
        body={
            "title": title,
            "status": status,
            "priority": priority,
            "project": project,
            "description": description,
            "acceptance_criteria": acceptance_criteria,
        },
    )


def flow_import_markdown_preview(
    markdown: str,
    *,
    source_filename: str | None = None,
    default_project: str = "default",
    default_status: str = "backlog",
    default_priority: int = 50,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "markdown": markdown,
        "default_project": default_project,
        "default_status": default_status,
        "default_priority": default_priority,
    }
    if source_filename:
        body["source_filename"] = source_filename
    return _request("POST", "/api/import/markdown/preview", body=body)
