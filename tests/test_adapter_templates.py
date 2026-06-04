from __future__ import annotations

from flow_app.adapter_templates import (
    AdapterTemplate,
    BUILTIN_TEMPLATES,
    get_template,
    validate_template,
)


def test_builtin_templates_exist():
    assert len(BUILTIN_TEMPLATES) >= 5
    names = [template.name for template in BUILTIN_TEMPLATES]
    assert "hermes-agent" in names
    assert "codex" in names
    assert "claude-code" in names
    assert "mcp-profile" in names
    assert "custom-script" in names
    assert "reviewer" in names


def test_get_template_found():
    template = get_template("hermes-agent")
    assert template is not None
    assert template.name == "hermes-agent"
    assert template.agent_type == "cli"


def test_get_template_not_found():
    assert get_template("nonexistent") is None


def test_adapter_template_schema():
    template = AdapterTemplate(
        name="test-agent",
        display_name="Test Agent",
        description="A test agent",
        command="test {workspace}",
    )
    assert template.name == "test-agent"
    assert template.command == "test {workspace}"
    assert template.capabilities == []
    assert template.dispatch_statuses == ["todo"]


def test_validate_template_clean():
    template = get_template("hermes-agent")
    assert template is not None
    warnings = validate_template(template)
    assert warnings == []


def test_validate_template_catches_secrets():
    template = AdapterTemplate(
        name="bad-agent",
        display_name="Bad",
        description="Has secrets",
        command="cli --api_key=sk-123 {workspace}",
    )
    warnings = validate_template(template)
    assert any("secret" in warning.lower() or "api_key" in warning.lower() for warning in warnings)


def test_validate_template_catches_shell_metacharacters():
    template = AdapterTemplate(
        name="shell-agent",
        display_name="Shell",
        description="Dangerous command",
        command="sh -c 'rm -rf /' {workspace}",
    )
    warnings = validate_template(template)
    assert any("metacharacter" in warning.lower() for warning in warnings)


def test_validate_template_catches_path_traversal():
    template = AdapterTemplate(
        name="path-agent",
        display_name="Path",
        description="Path traversal",
        command="agent {workspace}",
        command_allowlist=["../bin/agent"],
    )
    warnings = validate_template(template)
    assert any("path" in warning.lower() for warning in warnings)


def test_rest_list_templates(client):
    response = client.get("/api/adapter-templates")
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 5
    names = [template["name"] for template in data]
    assert "hermes-agent" in names


def test_rest_get_template(client):
    response = client.get("/api/adapter-templates/hermes-agent")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "hermes-agent"
    assert data["agent_type"] == "cli"
    assert isinstance(data["capabilities"], list)


def test_rest_get_template_not_found(client):
    response = client.get("/api/adapter-templates/nonexistent")
    assert response.status_code == 404


def test_rest_import_template(client):
    response = client.post("/api/adapter-templates/hermes-agent/import")
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "hermes-agent"
    assert data["agent_type"] == "cli"

    response2 = client.post("/api/adapter-templates/hermes-agent/import")
    assert response2.status_code == 409


def test_rest_import_template_with_overrides(client):
    response = client.post(
        "/api/adapter-templates/hermes-agent/import",
        json={"name": "my-hermes", "working_directory": "/tmp/workspace"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "my-hermes"
    assert data["working_directory"] == "/tmp/workspace"
