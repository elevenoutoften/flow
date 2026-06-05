from __future__ import annotations

import pytest

from flow_app.adapter_templates import AdapterTemplate, BUILTIN_TEMPLATES


def rpc(client, method, params=None, request_id=1):
    return client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}},
    )


def test_list_templates(client):
    response = client.get("/api/adapter-templates")

    assert response.status_code == 200
    templates = response.json()
    assert len(templates) == 7
    assert {template["name"] for template in templates} == set(BUILTIN_TEMPLATES)
    assert {"name": "hermes", "family": "hermes", "description": BUILTIN_TEMPLATES["hermes"].description} in templates


def test_get_template(client):
    response = client.get("/api/adapter-templates/hermes")

    assert response.status_code == 200
    template = response.json()
    assert template["name"] == "hermes"
    assert template["family"] == "hermes"
    assert template["command"] == "python -m flow_app.hermes_wrapper"
    assert "FLOW_API_KEY" in template["env_allowlist"]
    assert "Requires Flow API key" in template["notes"]


def test_get_template_not_found(client):
    response = client.get("/api/adapter-templates/nonexistent")

    assert response.status_code == 404


def test_instantiate_template(client):
    response = client.post("/api/adapter-templates/codex/instantiate", json={"name": "codex-worker"})

    assert response.status_code == 201, response.text
    agent = response.json()
    assert agent["name"] == "codex-worker"
    assert agent["agent_type"] == "cli"
    assert agent["command"] == BUILTIN_TEMPLATES["codex"].command
    assert agent["capabilities"] == "codex"
    assert agent["dispatch_statuses"] == "todo"


def test_instantiate_template_with_overrides(client):
    response = client.post(
        "/api/adapter-templates/hermes/instantiate",
        json={
            "name": "my-hermes-agent",
            "command": "python -m flow_app.hermes_wrapper --custom-flag",
            "dispatch_statuses": "backlog,todo",
            "env_allowlist": "FLOW_BASE_URL,FLOW_API_KEY,MY_EXTRA_VAR",
        },
    )

    assert response.status_code == 201, response.text
    agent = response.json()
    assert agent["name"] == "my-hermes-agent"
    assert agent["command"] == "python -m flow_app.hermes_wrapper --custom-flag"
    assert agent["dispatch_statuses"] == "backlog,todo"
    assert agent["env_allowlist"] == "FLOW_BASE_URL,FLOW_API_KEY,MY_EXTRA_VAR"


def test_instantiate_template_conflict(client):
    payload = {"name": "duplicate-codex"}
    first = client.post("/api/adapter-templates/codex/instantiate", json=payload)
    second = client.post("/api/adapter-templates/codex/instantiate", json=payload)

    assert first.status_code == 201, first.text
    assert second.status_code == 409


def test_template_rejects_dangerous_command():
    with pytest.raises(ValueError, match="rm -rf"):
        AdapterTemplate(name="bad", family="custom", command="bash -lc 'rm -rf /tmp/example'")


def test_template_rejects_path_traversal():
    with pytest.raises(ValueError, match="Working directory"):
        AdapterTemplate(name="bad", family="custom", working_directory="../etc")


def test_mcp_list_templates(client):
    response = rpc(client, "tools/call", {"name": "flow_list_adapter_templates", "arguments": {}})

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["structuredContent"]["count"] == 7
    names = {template["name"] for template in result["structuredContent"]["templates"]}
    assert names == set(BUILTIN_TEMPLATES)
    assert "Found 7 Flow adapter templates" in result["content"][0]["text"]


def test_mcp_get_template(client):
    response = rpc(client, "tools/call", {"name": "flow_get_adapter_template", "arguments": {"name": "hermes"}})

    assert response.status_code == 200
    template = response.json()["result"]["structuredContent"]["template"]
    assert template["name"] == "hermes"
    assert template["command"] == "python -m flow_app.hermes_wrapper"
