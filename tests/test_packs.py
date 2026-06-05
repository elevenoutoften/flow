"""Tests for flow_app.packs and flow_app.routes.packs — automation pack export/import."""

from __future__ import annotations

import json
import os

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


def test_pack_export_redacts_runner_api_key_ref(client):
    client.post("/api/runners", json={
        "name": "test-runner-pack",
        "runner_type": "poll",
        "api_key_ref": "plaintext-key-should-be-redacted",
    })

    response = client.get("/api/packs/export")

    assert response.status_code == 200
    pack = response.json()
    for runner in pack.get("runners", []):
        if runner["name"] == "test-runner-pack":
            assert runner["api_key_ref"] == "***"
            break
    else:
        raise AssertionError("test-runner-pack not found in pack export")


def test_export_redacts_rule_action_secrets(client):
    actions = [
        {
            "type": "webhook",
            "api_key": "plain-api-key",
            "secret": "plain-secret",
            "headers": {"token": "plain-token"},
        }
    ]
    response = client.post("/api/automation-rules", json={
        "name": "secret-action-rule",
        "trigger": "task_created",
        "conditions": "[]",
        "actions": json.dumps(actions),
    })
    assert response.status_code == 201, response.text

    response = client.get("/api/packs/export")
    assert response.status_code == 200
    pack = response.json()
    exported = next(rule for rule in pack["rules"] if rule["name"] == "secret-action-rule")
    redacted_actions = json.loads(exported["actions"])

    assert redacted_actions[0]["api_key"] == "***"
    assert redacted_actions[0]["secret"] == "***"
    assert redacted_actions[0]["headers"]["token"] == "***"


def test_export_redacts_rule_action_secrets_case_insensitive(client):
    actions = [
        {
            "type": "webhook",
            "Authorization": "Bearer plain-token",
            "X-Api-Key": "plain-api-key",
            "headers": {
                "authorization": "plain-header-auth",
                "X-Auth-Token": "plain-header-token",
            },
            "body": {
                "access_token": "plain-access-token",
                "auth_token": "plain-auth-token",
                "webhookSecret": "plain-webhook-secret",
                "private_key": "plain-private-key",
                "password": "plain-password",
                "env_secret": "env:PACK_SECRET",
                "file_secret": "file:/tmp/secret.txt",
            },
        }
    ]
    response = client.post("/api/automation-rules", json={
        "name": "case-insensitive-secret-action-rule",
        "trigger": "task_created",
        "conditions": "[]",
        "actions": json.dumps(actions),
    })
    assert response.status_code == 201, response.text

    response = client.get("/api/packs/export")
    assert response.status_code == 200
    pack = response.json()
    exported = next(rule for rule in pack["rules"] if rule["name"] == "case-insensitive-secret-action-rule")
    redacted_actions = json.loads(exported["actions"])
    action = redacted_actions[0]

    assert action["Authorization"] == "***"
    assert action["X-Api-Key"] == "***"
    assert action["headers"]["authorization"] == "***"
    assert action["headers"]["X-Auth-Token"] == "***"
    assert action["body"]["access_token"] == "***"
    assert action["body"]["auth_token"] == "***"
    assert action["body"]["webhookSecret"] == "***"
    assert action["body"]["private_key"] == "***"
    assert action["body"]["password"] == "***"
    assert action["body"]["env_secret"] == "env:PACK_SECRET"
    assert action["body"]["file_secret"] == "file:/tmp/secret.txt"


def test_export_preserves_env_and_file_references(client):
    actions = [
        {
            "type": "webhook",
            "api_key": "env:MY_API_KEY",
            "headers": {"Authorization": "file:/run/secrets/auth.txt"},
        }
    ]
    response = client.post("/api/automation-rules", json={
        "name": "env-file-secret-reference-rule",
        "trigger": "task_created",
        "conditions": "[]",
        "actions": json.dumps(actions),
    })
    assert response.status_code == 201, response.text

    response = client.get("/api/packs/export")
    assert response.status_code == 200
    pack = response.json()
    exported = next(rule for rule in pack["rules"] if rule["name"] == "env-file-secret-reference-rule")
    redacted_actions = json.loads(exported["actions"])

    assert redacted_actions[0]["api_key"] == "env:MY_API_KEY"
    assert redacted_actions[0]["headers"]["Authorization"] == "file:/run/secrets/auth.txt"


def test_export_does_not_redact_normal_keys(client):
    actions = [
        {
            "type": "webhook",
            "name": "notify-service",
            "url": "https://example.com/hook",
            "method": "POST",
            "description": "Send a task event",
            "id": "action-1",
            "api_key": "plain-api-key",
        }
    ]
    response = client.post("/api/automation-rules", json={
        "name": "normal-keys-rule",
        "trigger": "task_created",
        "conditions": "[]",
        "actions": json.dumps(actions),
    })
    assert response.status_code == 201, response.text

    response = client.get("/api/packs/export")
    assert response.status_code == 200
    pack = response.json()
    exported = next(rule for rule in pack["rules"] if rule["name"] == "normal-keys-rule")
    redacted_actions = json.loads(exported["actions"])
    action = redacted_actions[0]

    assert action["name"] == "notify-service"
    assert action["url"] == "https://example.com/hook"
    assert action["method"] == "POST"
    assert action["description"] == "Send a task event"
    assert action["id"] == "action-1"
    assert action["api_key"] == "***"


def test_flow_serve_creates_lock_file(tmp_path, monkeypatch):
    from flow_app.config import reset_settings_cache
    from flow_app.serve import _ensure_lock_file

    monkeypatch.setenv("FLOW_DATA_DIR", str(tmp_path))
    reset_settings_cache()
    lock_path = _ensure_lock_file()
    try:
        assert lock_path.exists()
        assert lock_path.read_text(encoding="utf-8") == str(os.getpid())
    finally:
        lock_path.unlink(missing_ok=True)
        reset_settings_cache()


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
    response = client.post("/api/packs/import?conflict_policy=update", json=pack_v2)
    assert response.status_code == 200

    # Verify the rule was updated
    rules_resp = client.get("/api/automation-rules")
    matching = [r for r in rules_resp.json().get("items", []) if r.get("name") == "upsert-rule"]
    assert len(matching) >= 1
    assert matching[0]["description"] == "updated"


def test_import_default_conflict_policy_is_skip(client):
    pack_v1 = {
        "schema_version": PACK_SCHEMA_VERSION,
        "name": "default-skip-pack",
        "description": "v1",
        "exported_at": "2026-06-05T00:00:00Z",
        "rules": [
            {
                "name": "default-skip-rule",
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
    response = client.post("/api/packs/import", json=pack_v1)
    assert response.status_code == 200

    pack_v2 = {
        **pack_v1,
        "description": "v2",
        "rules": [
            {
                "name": "default-skip-rule",
                "trigger": "task_created",
                "description": "updated",
                "conditions": "[]",
                "actions": "[]",
            }
        ],
    }
    response = client.post("/api/packs/import", json=pack_v2)
    assert response.status_code == 200
    result = response.json()
    assert result["imported"]["rules"] == 0
    assert "rule 'default-skip-rule': skipped existing entity" in result["skipped"]

    rules_resp = client.get("/api/automation-rules")
    matching = [r for r in rules_resp.json().get("items", []) if r.get("name") == "default-skip-rule"]
    assert len(matching) == 1
    assert matching[0]["description"] == "original"


def test_import_conflict_policy_skip(client):
    pack_v1 = {
        "schema_version": PACK_SCHEMA_VERSION,
        "name": "skip-pack",
        "description": "v1",
        "exported_at": "2026-06-05T00:00:00Z",
        "rules": [
            {
                "name": "skip-rule",
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
    assert client.post("/api/packs/import", json=pack_v1).status_code == 200

    pack_v2 = {
        **pack_v1,
        "description": "v2",
        "rules": [
            {
                "name": "skip-rule",
                "trigger": "task_created",
                "description": "updated",
                "conditions": "[]",
                "actions": "[]",
            }
        ],
    }
    response = client.post("/api/packs/import?conflict_policy=skip", json=pack_v2)
    assert response.status_code == 200
    result = response.json()
    assert result["imported"]["rules"] == 0
    assert "rule 'skip-rule': skipped existing entity" in result["skipped"]

    rules_resp = client.get("/api/automation-rules")
    matching = [r for r in rules_resp.json().get("items", []) if r.get("name") == "skip-rule"]
    assert len(matching) == 1
    assert matching[0]["description"] == "original"


def test_import_conflict_policy_error(client):
    pack = {
        "schema_version": PACK_SCHEMA_VERSION,
        "name": "error-pack",
        "description": "",
        "exported_at": "2026-06-05T00:00:00Z",
        "rules": [
            {
                "name": "error-rule",
                "trigger": "task_created",
                "conditions": "[]",
                "actions": "[]",
            }
        ],
        "agents": [],
        "webhook_configs": [],
        "notification_configs": [],
    }
    assert client.post("/api/packs/import", json=pack).status_code == 200

    response = client.post("/api/packs/import?conflict_policy=error", json=pack)
    assert response.status_code == 409
    assert response.json()["detail"]["section"] == "rules"
    assert response.json()["detail"]["name"] == "error-rule"


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
