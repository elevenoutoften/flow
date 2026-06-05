from __future__ import annotations

import pytest

from flow_app import adapter_import
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


def test_preview_template(client):
    response = client.post("/api/adapter-templates/codex/preview", json={"name": "codex-preview"})

    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["name"] == "codex-preview"
    assert preview["command"] == BUILTIN_TEMPLATES["codex"].command
    assert preview["source_template"] == "codex"
    assert preview["overrides_applied"] == ["name"]
    assert preview["would_create"] is True
    assert preview["conflict_with"] is None
    assert client.get("/api/agents").json() == []


def test_preview_template_with_overrides(client):
    response = client.post(
        "/api/adapter-templates/codex/preview",
        json={"name": "codex-preview", "command": "codex exec --model gpt-5.5", "dispatch_statuses": "backlog,todo"},
    )

    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["command"] == "codex exec --model gpt-5.5"
    assert preview["dispatch_statuses"] == "backlog,todo"
    assert preview["overrides_applied"] == ["command", "dispatch_statuses", "name"]


def test_preview_template_conflict(client):
    created = client.post("/api/adapter-templates/codex/instantiate", json={"name": "taken-agent"})

    response = client.post("/api/adapter-templates/codex/preview", json={"name": "taken-agent"})

    assert created.status_code == 201, created.text
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["would_create"] is False
    assert preview["conflict_with"] == created.json()["id"]


def test_instantiate_template_conflict(client):
    payload = {"name": "duplicate-codex"}
    first = client.post("/api/adapter-templates/codex/instantiate", json=payload)
    second = client.post("/api/adapter-templates/codex/instantiate", json=payload)

    assert first.status_code == 201, first.text
    assert second.status_code == 409


def test_instantiate_collision_error(client):
    payload = {"name": "duplicate-codex", "on_collision": "error"}
    first = client.post("/api/adapter-templates/codex/instantiate", json=payload)
    second = client.post("/api/adapter-templates/codex/instantiate", json=payload)

    assert first.status_code == 201, first.text
    assert second.status_code == 409


def test_instantiate_collision_skip(client):
    first = client.post("/api/adapter-templates/codex/instantiate", json={"name": "stable-agent"})
    second = client.post(
        "/api/adapter-templates/codex/instantiate",
        json={"name": "stable-agent", "command": "codex exec --model gpt-5.5", "on_collision": "skip"},
    )

    assert first.status_code == 201, first.text
    assert second.status_code == 200, second.text
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["command"] == first.json()["command"]


def test_instantiate_collision_update(client):
    first = client.post("/api/adapter-templates/codex/instantiate", json={"name": "updatable-agent"})
    second = client.post(
        "/api/adapter-templates/hermes/instantiate",
        json={
            "name": "updatable-agent",
            "command": "python -m flow_app.hermes_wrapper --custom-flag",
            "dispatch_statuses": "backlog,todo",
            "on_collision": "update",
        },
    )

    assert first.status_code == 201, first.text
    assert second.status_code == 200, second.text
    agent = second.json()
    assert agent["id"] == first.json()["id"]
    assert agent["capabilities"] == "hermes"
    assert agent["command"] == "python -m flow_app.hermes_wrapper --custom-flag"
    assert agent["dispatch_statuses"] == "backlog,todo"


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


def test_mcp_preview_template(client):
    response = rpc(
        client,
        "tools/call",
        {"name": "flow_preview_adapter_template", "arguments": {"template": "codex", "name": "mcp-codex"}},
    )

    assert response.status_code == 200
    preview = response.json()["result"]["structuredContent"]["preview"]
    assert preview["name"] == "mcp-codex"
    assert preview["source_template"] == "codex"
    assert preview["would_create"] is True


def test_mcp_instantiate_template_skip(client):
    first = rpc(
        client,
        "tools/call",
        {"name": "flow_instantiate_adapter_template", "arguments": {"template": "codex", "name": "mcp-codex"}},
    )
    second = rpc(
        client,
        "tools/call",
        {
            "name": "flow_instantiate_adapter_template",
            "arguments": {"template": "codex", "name": "mcp-codex", "on_collision": "skip"},
        },
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["result"]["structuredContent"]["collision"] == "skip"


def test_adapter_import_cli_list(monkeypatch, capsys):
    class DummyResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return [{"name": "codex", "family": "codex", "description": "OpenAI Codex CLI"}]

    class DummyClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return None

        def get(self, path):
            assert path == "/api/adapter-templates"
            return DummyResponse()

    monkeypatch.setattr(adapter_import.httpx, "Client", DummyClient)

    assert adapter_import.main(["--list"]) == 0
    assert "codex\tcodex\tOpenAI Codex CLI" in capsys.readouterr().out


def test_adapter_import_cli_preview(monkeypatch, capsys):
    class DummyResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"name": "test-agent", "source_template": "codex", "would_create": True}

    class DummyClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return None

        def post(self, path, json):
            assert path == "/api/adapter-templates/codex/preview"
            assert json == {"name": "test-agent"}
            return DummyResponse()

    monkeypatch.setattr(adapter_import.httpx, "Client", DummyClient)

    assert adapter_import.main(["--preview", "codex", "--name", "test-agent"]) == 0
    out = capsys.readouterr().out
    assert '"name": "test-agent"' in out
    assert '"would_create": true' in out


def test_builtin_templates_have_command_allowlist():
    """Built-in templates must have appropriate command_allowlist values."""
    from flow_app.adapter_templates import BUILTIN_TEMPLATES

    # CLI-based templates must have non-empty command_allowlist
    cli_templates = {"hermes", "codex", "claude-code", "opencode", "opencrawl"}
    for name in cli_templates:
        template = BUILTIN_TEMPLATES[name]
        assert template.command_allowlist, f"{name} template must have a non-empty command_allowlist"

    # Remote-protocol and custom templates intentionally have empty allowlists
    assert BUILTIN_TEMPLATES["mcp"].command_allowlist == ""
    assert BUILTIN_TEMPLATES["custom-script"].command_allowlist == ""


def test_instantiate_preserves_command_allowlist(client):
    """Instantiating a template preserves its command_allowlist."""
    response = client.post("/api/adapter-templates/codex/instantiate", json={"name": "allowlist-test-agent"})

    assert response.status_code == 201, response.text
    agent = response.json()
    assert agent["command_allowlist"] == "codex"


def test_instantiate_hermes_command_allowlist(client):
    """Hermes template preserves multi-prefix command_allowlist."""
    response = client.post("/api/adapter-templates/hermes/instantiate", json={"name": "hermes-allowlist-agent"})

    assert response.status_code == 201, response.text
    agent = response.json()
    assert "python" in agent["command_allowlist"]
    assert "hermes" in agent["command_allowlist"]
