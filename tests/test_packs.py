"""Tests for flow_app.packs and flow_app.routes.packs — automation pack export/import."""

from __future__ import annotations

import json

from flow_app.packs import PACK_SCHEMA_VERSION, export_pack, import_pack, validate_pack


def test_export_redacts_secrets(client):
    # Create a webhook with a non-secret-reference secret value
    client.post("/api/webhook-configs", json={
        "name": "test-wh",
        "url": "https://example.com/hook",
        "events": ["task_created"],
        "project": "default",
    })
    # Export
    response = client.get("/api/packs/export")
    assert response.status_code == 200
    pack = response.json()
    for wh in pack.get("webhook_configs", []):
        if wh.get("name") == "test-wh":
            # Secret should be redacted to "***" since it's not an env/file reference
            assert wh["secret"] in ("***", "")


def test_export_download_header(client):
    response = client.get("/api/packs/export?download=1")
    assert response.status_code == 200
    assert "Content-Disposition" in response.headers
    assert "flow-pack-" in response.headers["Content-Disposition"]
    assert ".json" in response.headers["Content-Disposition"]


def test_import_dry_run(client):
    pack = {
        "schema_version": PACK_SCHEMA_VERSION,
        "name": "test-pack",
        "description": "Test",
        "exported_at": "2026-06-05T00:00:00Z",
        "rules": [
            {
                "name": "dry-run-rule",
                "trigger": "task_created",
                "trigger_config": "",
                "conditions": "[]",
                "actions": "[]",
            }
        ],
        "agents": [
            {
                "name": "dry-run-agent",
            }
        ],
        "webhook_configs": [],
        "notification_configs": [],
    }
    response = client.post("/api/packs/import?dry_run=1", json=pack)
    assert response.status_code == 200
    result = response.json()
    assert result["imported"]["rules"] >= 1
    assert result["imported"]["agents"] >= 1

    # Verify nothing was actually written
    rules_resp = client.get("/api/automation-rules")
    rule_names = [r["name"] for r in rules_resp.json().get("items", []) if r.get("name")]
    assert "dry-run-rule" not in rule_names


def test_import_applies_entities(client):
    pack = {
        "schema_version": PACK_SCHEMA_VERSION,
        "name": "test-pack",
        "description": "Test",
        "exported_at": "2026-06-05T00:00:00Z",
        "rules": [
            {
                "name": "import-test-rule",
                "trigger": "task_created",
                "trigger_config": "",
                "conditions": "[]",
                "actions": "[]",
            }
        ],
        "agents": [
            {
                "name": "import-test-agent",
            }
        ],
        "webhook_configs": [],
        "notification_configs": [],
    }
    response = client.post("/api/packs/import", json=pack)
    assert response.status_code == 200
    result = response.json()
    assert result["imported"]["rules"] >= 1
    assert result["imported"]["agents"] >= 1

    # Verify created
    rules_resp = client.get("/api/automation-rules")
    rule_names = [r["name"] for r in rules_resp.json().get("items", []) if r.get("name")]
    assert "import-test-rule" in rule_names

    agents_resp = client.get("/api/agents")
    agent_names = [a["name"] for a in agents_resp.json() if a.get("name")]
    assert "import-test-agent" in agent_names


def test_import_invalid_schema_version(client):
    pack = {
        "schema_version": 99,
        "name": "bad",
        "description": "",
        "exported_at": "",
        "rules": [],
        "agents": [],
        "webhook_configs": [],
        "notification_configs": [],
    }
    response = client.post("/api/packs/import", json=pack)
    assert response.status_code == 422
    assert "errors" in response.json().get("detail", {})


def test_import_upsert_existing(client):
    # Create a rule first
    pack_v1 = {
        "schema_version": PACK_SCHEMA_VERSION,
        "name": "upsert-pack",
        "description": "v1",
        "exported_at": "2026-06-05T00:00:00Z",
        "rules": [
            {
                "name": "upsert-rule",
                "trigger": "task_created",
                "description": "original",
                "conditions": "[]",
                "actions": "[]",
            }
        ],
        "agents": [],
        "webhook_configs": [],
        "notification_configs": [],
    }
    client.post("/api/packs/import", json=pack_v1)

    # Now upsert with updated description
    pack_v2 = {
        **pack_v1,
        "description": "v2",
        "rules": [
            {
                "name": "upsert-rule",
                "trigger": "task_created",
                "description": "updated",
                "conditions": "[]",
                "actions": "[]",
            }
        ],
    }
    response = client.post("/api/packs/import", json=pack_v2)
    assert response.status_code == 200

    # Verify the rule was updated
    rules_resp = client.get("/api/automation-rules")
    matching = [r for r in rules_resp.json().get("items", []) if r.get("name") == "upsert-rule"]
    assert len(matching) >= 1
    assert matching[0]["description"] == "updated"


def test_import_permission(client):
    # Create a non-admin (implementer) key
    key_resp = client.post("/api/api-keys", json={"name": "impl", "role": "implementer"})
    api_key = key_resp.json()["api_key"]

    pack = {
        "schema_version": PACK_SCHEMA_VERSION,
        "name": "perm-test",
        "description": "",
        "exported_at": "",
        "rules": [],
        "agents": [],
        "webhook_configs": [],
        "notification_configs": [],
    }
    response = client.post(
        "/api/packs/import",
        json=pack,
        headers={"Authorization": f"Bearer {api_key}"},
    )
    assert response.status_code == 403