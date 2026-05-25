from __future__ import annotations

from unittest.mock import patch
import hashlib
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import event

from flow_app.config import FLOW_VERSION
from flow_app.main import create_app
from flow_app.models import Task
from flow_app.schemas import ApiKeyRole
from flow_app.security import SESSION_COOKIE_NAME, Actor, Permission, ROLE_PERMISSIONS, sign_session


def create_task(client, **overrides):
    payload = {
        "title": "Expose ComfyUI through server route",
        "status": "todo",
        "priority": 50,
        "project": "default",
        "description": "Wire route.",
        "acceptance_criteria": "Route responds.",
    }
    payload.update(overrides)
    response = client.post("/api/tasks", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_flow_version_is_static_content_hash():
    static_dir = Path(__file__).resolve().parent.parent / "flow_app" / "static"
    digest = hashlib.sha256()
    for path in sorted(static_dir.rglob("*")):
        if path.is_file():
            digest.update(path.read_bytes())

    assert FLOW_VERSION == digest.hexdigest()[:8]


def test_create_list_and_patch_task(client):
    task = create_task(client, priority=90)
    assert task["id"] == "flow_000001"
    assert task["notes"] == []
    assert task["source_filename"] is None

    listed = client.get("/api/tasks").json()["items"]
    assert [item["id"] for item in listed] == [task["id"]]

    response = client.patch(
        f"/api/tasks/{task['id']}",
        json={"title": "Expose Flow", "priority": 95, "assignee": "codex"},
    )
    assert response.status_code == 200
    patched = response.json()
    assert patched["title"] == "Expose Flow"
    assert patched["priority"] == 95
    assert patched["assignee"] == "codex"


def test_list_tasks_uses_lightweight_response_without_detail_fields(client):
    task = create_task(client)
    note = client.post(f"/api/tasks/{task['id']}/note", json={"note": "Implementation note."})
    assert note.status_code == 200, note.text
    handoff = client.post(
        f"/api/tasks/{task['id']}/handoff",
        json={"summary": "Ready for review.", "tests_run": ["python -m pytest tests/ -v"]},
    )
    assert handoff.status_code == 201, handoff.text

    listed = client.get("/api/tasks")
    assert listed.status_code == 200
    list_item = listed.json()["items"][0]
    assert "notes" not in list_item
    assert "latest_handoff" not in list_item

    detail = client.get(f"/api/tasks/{task['id']}")
    assert detail.status_code == 200
    detail_item = detail.json()
    assert detail_item["notes"][0]["body"] == "Implementation note."
    assert detail_item["latest_handoff"]["summary"] == "Ready for review."


def test_list_tasks_query_count_does_not_scale_with_notes_or_handoffs(client):
    for index in range(5):
        task = create_task(client, title=f"List query task {index}")
        note = client.post(f"/api/tasks/{task['id']}/note", json={"note": f"Note {index}."})
        assert note.status_code == 200, note.text
        handoff = client.post(f"/api/tasks/{task['id']}/handoff", json={"summary": f"Handoff {index}."})
        assert handoff.status_code == 201, handoff.text

    statements = []

    def count_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(client.app.state.engine, "before_cursor_execute", count_statement)
    try:
        response = client.get("/api/tasks")
    finally:
        event.remove(client.app.state.engine, "before_cursor_execute", count_statement)

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 5
    assert body["total"] == 5
    assert body["limit"] == 100
    assert body["offset"] == 0
    assert len(statements) <= 3


def test_list_tasks_pagination_metadata_and_bounds(client):
    created = [create_task(client, title=f"Page task {index:03d}", priority=index) for index in range(105)]

    default_page = client.get("/api/tasks")
    assert default_page.status_code == 200
    default_body = default_page.json()
    assert len(default_body["items"]) == 100
    assert default_body["total"] == 105
    assert default_body["limit"] == 100
    assert default_body["offset"] == 0

    page = client.get("/api/tasks", params={"limit": 3, "offset": 2})
    assert page.status_code == 200
    body = page.json()
    assert body["total"] == 105
    assert body["limit"] == 3
    assert body["offset"] == 2
    assert [item["id"] for item in body["items"]] == [created[102]["id"], created[101]["id"], created[100]["id"]]

    beyond = client.get("/api/tasks", params={"limit": 10, "offset": 999})
    assert beyond.status_code == 200
    assert beyond.json()["items"] == []

    assert client.get("/api/tasks", params={"limit": 501}).status_code == 422


def test_api_key_create_list_and_revoke(client):
    created = client.post(
        "/api/api-keys",
        json={"name": "codex-worker-1", "description": "Worker pool"},
    )
    assert created.status_code == 201, created.text
    payload = created.json()
    assert payload["id"] == "key_000001"
    assert payload["api_key"].startswith("flow_")
    assert payload["key_prefix"] == payload["api_key"][:16]
    assert payload["role"] == "read_only"
    assert "key_hash" not in payload

    listed = client.get("/api/api-keys")
    assert listed.status_code == 200
    items = listed.json()
    assert len(items) == 1
    assert items[0]["name"] == "codex-worker-1"
    assert items[0]["role"] == "read_only"
    assert "api_key" not in items[0]
    assert "key_hash" not in items[0]
    assert items[0]["revoked_at"] is None

    admin_created = client.post(
        "/api/api-keys",
        json={"name": "codex-admin", "description": "Admin key", "role": "admin"},
    )
    assert admin_created.status_code == 201, admin_created.text
    assert admin_created.json()["role"] == "admin"

    items = client.get("/api/api-keys").json()
    assert {item["name"]: item["role"] for item in items} == {
        "codex-admin": "admin",
        "codex-worker-1": "read_only",
    }

    revoked = client.post(f"/api/api-keys/{payload['id']}/revoke", json={})
    assert revoked.status_code == 200
    revoked_payload = revoked.json()
    assert revoked_payload["role"] == "read_only"
    assert revoked_payload["revoked_at"] is not None


def test_api_key_role_validation(client):
    valid = client.post(
        "/api/api-keys",
        json={"name": "admin-key", "role": "admin"},
    )
    assert valid.status_code == 201, valid.text
    assert valid.json()["role"] == "admin"

    invalid = client.post(
        "/api/api-keys",
        json={"name": "superuser-key", "role": "superuser"},
    )
    assert invalid.status_code == 422

    wrong_case = client.post(
        "/api/api-keys",
        json={"name": "case-key", "role": "Admin"},
    )
    assert wrong_case.status_code == 422


def test_api_key_role_default_is_read_only(client):
    created = client.post(
        "/api/api-keys",
        json={"name": "readonly-key"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["role"] == "read_only"


def bearer_headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


def create_role_headers(client, role: str, name: str | None = None) -> dict[str, str]:
    created_key = client.post(
        "/api/api-keys",
        json={"name": name or f"{role}-key", "role": role},
    )
    assert created_key.status_code == 201, created_key.text
    return bearer_headers(created_key.json()["api_key"])


def test_api_key_management_requires_human_admin_when_trusted_auth_headers_exist(tmp_path):
    app = create_app(f"sqlite:///{tmp_path / 'trusted.sqlite'}", trusted_headers=True)
    with TestClient(app) as trusted_client:
        agent_response = trusted_client.get("/api/api-keys", headers={"X-Axis-Agent": "codex"})
        assert agent_response.status_code == 403

        user_response = trusted_client.get("/api/api-keys", headers={"X-Axis-User": "viewer"})
        assert user_response.status_code == 403

        admin_response = trusted_client.get(
            "/api/api-keys",
            headers={"X-Axis-User": "admin_user", "X-Axis-Admin": "1"},
        )
        assert admin_response.status_code == 200


class TestTrustedHeaderGating:
    def test_trusted_headers_disabled_by_default(self, tmp_path, monkeypatch):
        monkeypatch.delenv("FLOW_TRUSTED_HEADERS", raising=False)
        app = create_app(f"sqlite:///{tmp_path / 'default.sqlite'}")
        with TestClient(app) as test_client:
            response = test_client.get("/api/api-keys", headers={"X-Axis-Admin": "1", "X-Axis-User": "alice"})

        assert response.status_code == 401

    def test_trusted_headers_admin_rejected_when_disabled(self, tmp_path):
        app = create_app(f"sqlite:///{tmp_path / 'disabled-admin.sqlite'}", trusted_headers=False)
        with TestClient(app) as test_client:
            response = test_client.get("/api/api-keys", headers={"X-Axis-Admin": "1", "X-Axis-User": "alice"})

        assert response.status_code == 401

    def test_trusted_headers_user_rejected_when_disabled(self, tmp_path):
        app = create_app(f"sqlite:///{tmp_path / 'disabled-user.sqlite'}", trusted_headers=False)
        with TestClient(app) as test_client:
            response = test_client.get("/api/tasks", headers={"X-Axis-User": "alice"})

        assert response.status_code == 401

    def test_trusted_headers_admin_accepted_when_enabled(self, tmp_path):
        app = create_app(f"sqlite:///{tmp_path / 'enabled-admin.sqlite'}", trusted_headers=True)
        with TestClient(app) as test_client:
            response = test_client.get("/api/api-keys", headers={"X-Axis-Admin": "1", "X-Axis-User": "alice"})

        assert response.status_code == 200

    def test_trusted_headers_user_accepted_when_enabled(self, tmp_path):
        app = create_app(f"sqlite:///{tmp_path / 'enabled-user.sqlite'}", trusted_headers=True)
        with TestClient(app) as test_client:
            response = test_client.get("/api/tasks", headers={"X-Axis-User": "alice"})

        assert response.status_code == 200

    def test_bearer_auth_works_regardless_of_trusted_headers(self, tmp_path):
        db_url = f"sqlite:///{tmp_path / 'bearer.sqlite'}"
        admin_app = create_app(db_url, trusted_headers=True)
        with TestClient(admin_app) as admin_client:
            created_key = admin_client.post(
                "/api/api-keys",
                json={"name": "reader", "role": "read_only"},
                headers={"X-Axis-Admin": "1"},
            )
            assert created_key.status_code == 201, created_key.text
            headers = bearer_headers(created_key.json()["api_key"])

        disabled_app = create_app(db_url, trusted_headers=False)
        with TestClient(disabled_app) as disabled_client:
            assert disabled_client.get("/api/tasks", headers=headers).status_code == 200

        enabled_app = create_app(db_url, trusted_headers=True)
        with TestClient(enabled_app) as enabled_client:
            assert enabled_client.get("/api/tasks", headers=headers).status_code == 200

    def test_board_page_loads_without_trusted_headers(self, tmp_path):
        app = create_app(f"sqlite:///{tmp_path / 'board.sqlite'}", trusted_headers=False)
        with TestClient(app) as test_client:
            response = test_client.get("/")

        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_session_cookie_not_issued_without_explicit_secret(self, tmp_path):
        db_url = f"sqlite:///{tmp_path / 'no-secret.sqlite'}"
        app = create_app(db_url, trusted_headers=True)
        with TestClient(app) as test_client:
            response = test_client.get("/", headers={"X-Axis-Admin": "1", "X-Axis-User": "alice"})

        assert response.status_code == 200
        assert "set-cookie" not in response.headers

        secret_app = create_app(
            f"sqlite:///{tmp_path / 'explicit-secret.sqlite'}",
            trusted_headers=True,
            session_secret="test-secret-for-testing",
        )
        with TestClient(secret_app) as secret_client:
            cookie_response = secret_client.get("/", headers={"X-Axis-Admin": "1", "X-Axis-User": "alice"})
            valid_cookie = cookie_response.cookies[SESSION_COOKIE_NAME]

        disabled_session_app = create_app(db_url, trusted_headers=False)
        with TestClient(disabled_session_app) as disabled_session_client:
            forged_response = disabled_session_client.get(
                "/api/tasks",
                headers={"Cookie": f"{SESSION_COOKIE_NAME}={valid_cookie}"},
            )

        assert forged_response.status_code == 401

    def test_session_cookie_with_wrong_secret_rejected(self, tmp_path):
        app = create_app(
            f"sqlite:///{tmp_path / 'wrong-secret.sqlite'}",
            trusted_headers=False,
            session_secret="correct-secret",
        )
        forged_cookie = sign_session(Actor(name="alice", role=ApiKeyRole.admin, source="admin_header"), "wrong-secret")
        with TestClient(app) as test_client:
            response = test_client.get("/api/tasks", headers={"Cookie": f"{SESSION_COOKIE_NAME}={forged_cookie}"})

        assert response.status_code == 401

    def test_default_sqlite_url_secret_rejected(self, tmp_path):
        db_url = f"sqlite:///{tmp_path / 'default-url.sqlite'}"
        app = create_app(db_url, trusted_headers=False)
        forged_cookie = sign_session(Actor(name="alice", role=ApiKeyRole.admin, source="admin_header"), db_url)
        with TestClient(app) as test_client:
            response = test_client.get("/api/tasks", headers={"Cookie": f"{SESSION_COOKIE_NAME}={forged_cookie}"})

        assert response.status_code == 401

    def test_session_cookie_works_with_explicit_secret(self, tmp_path):
        app = create_app(
            f"sqlite:///{tmp_path / 'valid-session.sqlite'}",
            trusted_headers=True,
            session_secret="test-secret-for-testing",
        )
        with TestClient(app) as test_client:
            board_response = test_client.get("/", headers={"X-Axis-Admin": "1", "X-Axis-User": "alice"})
            api_response = test_client.get("/api/tasks")

        assert board_response.status_code == 200
        assert SESSION_COOKIE_NAME in board_response.cookies
        assert api_response.status_code == 200

    def test_board_reissues_cookie_for_session_cookie_actor(self, tmp_path):
        app = create_app(
            f"sqlite:///{tmp_path / 'refresh-session.sqlite'}",
            trusted_headers=True,
            session_secret="test-secret-for-testing",
        )
        with TestClient(app) as test_client:
            initial_response = test_client.get("/", headers={"X-Axis-Admin": "1", "X-Axis-User": "alice"})
            refreshed_response = test_client.get("/")

        assert initial_response.status_code == 200
        assert refreshed_response.status_code == 200
        assert SESSION_COOKIE_NAME in test_client.cookies
        assert test_client.cookies[SESSION_COOKIE_NAME] != ""
        assert SESSION_COOKIE_NAME in refreshed_response.cookies
        assert refreshed_response.cookies[SESSION_COOKIE_NAME] != ""
        assert "set-cookie" in refreshed_response.headers

    def test_healthz_config_reports_auth_settings(self, tmp_path):
        app = create_app(
            f"sqlite:///{tmp_path / 'healthz-config.sqlite'}",
            trusted_headers=True,
            session_secret="test-secret-for-testing",
            session_cookie_secure=True,
        )
        with TestClient(app) as test_client:
            response = test_client.get("/healthz/config")

        assert response.status_code == 200
        assert response.json() == {
            "trusted_headers": True,
            "session_auth_enabled": True,
            "session_cookie_secure": True,
        }

    def test_session_cookie_not_issued_when_trusted_headers_disabled(self, tmp_path):
        app = create_app(
            f"sqlite:///{tmp_path / 'trusted-disabled-session.sqlite'}",
            trusted_headers=False,
            session_secret="test-secret-for-testing",
        )
        with TestClient(app) as test_client:
            response = test_client.get("/", headers={"X-Axis-Admin": "1", "X-Axis-User": "alice"})

        assert response.status_code == 200
        assert "set-cookie" not in response.headers

    def test_session_cookie_secure_flag_is_configurable(self, tmp_path):
        app = create_app(
            f"sqlite:///{tmp_path / 'secure-session.sqlite'}",
            trusted_headers=True,
            session_secret="test-secret-for-testing",
            session_cookie_secure=True,
        )
        with TestClient(app) as test_client:
            response = test_client.get("/", headers={"X-Axis-Admin": "1", "X-Axis-User": "alice"})

        assert response.status_code == 200
        assert "secure" in response.headers["set-cookie"].lower()

    def test_valid_session_cookie_before_expiry(self, tmp_path):
        """A freshly signed session cookie should authenticate successfully."""
        app = create_app(
            f"sqlite:///{tmp_path / 'valid-expiry.sqlite'}",
            trusted_headers=False,
            session_secret="test-secret-for-testing",
        )
        actor = Actor(name="alice", role=ApiKeyRole.admin, source="admin_header")
        cookie = sign_session(actor, "test-secret-for-testing")
        with TestClient(app) as test_client:
            response = test_client.get("/api/tasks", headers={"Cookie": f"{SESSION_COOKIE_NAME}={cookie}"})

        assert response.status_code == 200

    def test_expired_session_cookie_rejected(self, tmp_path):
        """An expired session cookie must be rejected server-side."""
        app = create_app(
            f"sqlite:///{tmp_path / 'expired.sqlite'}",
            trusted_headers=False,
            session_secret="test-secret-for-testing",
        )
        actor = Actor(name="alice", role=ApiKeyRole.admin, source="admin_header")
        # Sign with max_age=0 so exp == iat (already expired)
        expired_cookie = sign_session(actor, "test-secret-for-testing", max_age=0)
        with TestClient(app) as test_client:
            response = test_client.get("/api/tasks", headers={"Cookie": f"{SESSION_COOKIE_NAME}={expired_cookie}"})

        assert response.status_code == 401

    def test_future_iat_session_cookie_rejected(self, tmp_path):
        """A cookie with iat in the future must be rejected."""
        import json

        from flow_app.security import _b64decode, _b64encode, _session_signature

        app = create_app(
            f"sqlite:///{tmp_path / 'future-iat.sqlite'}",
            trusted_headers=False,
            session_secret="test-secret-for-testing",
        )
        future_now = int(__import__("time").time()) + 3600
        payload = {
            "name": "alice",
            "role": "admin",
            "iat": future_now,
            "exp": future_now + 43200,
        }
        encoded = _b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
        signature = _session_signature(encoded, "test-secret-for-testing")
        future_cookie = f"{encoded}.{signature}"
        with TestClient(app) as test_client:
            response = test_client.get("/api/tasks", headers={"Cookie": f"{SESSION_COOKIE_NAME}={future_cookie}"})

        assert response.status_code == 401

    def test_legacy_session_cookie_without_exp_rejected(self, tmp_path):
        """A legacy cookie missing iat/exp fields must be rejected."""
        import json

        from flow_app.security import _b64encode, _session_signature

        app = create_app(
            f"sqlite:///{tmp_path / 'legacy-no-exp.sqlite'}",
            trusted_headers=False,
            session_secret="test-secret-for-testing",
        )
        # Manually craft a cookie without iat/exp (legacy shape)
        payload = {"name": "alice", "role": "admin"}
        encoded = _b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
        signature = _session_signature(encoded, "test-secret-for-testing")
        legacy_cookie = f"{encoded}.{signature}"
        with TestClient(app) as test_client:
            response = test_client.get("/api/tasks", headers={"Cookie": f"{SESSION_COOKIE_NAME}={legacy_cookie}"})

        assert response.status_code == 401

    def test_tampered_exp_in_session_cookie_rejected(self, tmp_path):
        """A cookie with a tampered exp value must be rejected (signature mismatch)."""
        import json

        from flow_app.security import _b64decode, _b64encode

        app = create_app(
            f"sqlite:///{tmp_path / 'tampered-exp.sqlite'}",
            trusted_headers=False,
            session_secret="test-secret-for-testing",
        )
        actor = Actor(name="alice", role=ApiKeyRole.admin, source="admin_header")
        valid_cookie = sign_session(actor, "test-secret-for-testing")
        # Decode, tamper exp, re-encode (signature won't match)
        encoded_payload = valid_cookie.partition(".")[0]
        payload = json.loads(_b64decode(encoded_payload).decode("utf-8"))
        payload["exp"] = payload["exp"] + 999999
        tampered_encoded = _b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
        tampered_cookie = f"{tampered_encoded}.{valid_cookie.partition('.')[2]}"
        with TestClient(app) as test_client:
            response = test_client.get("/api/tasks", headers={"Cookie": f"{SESSION_COOKIE_NAME}={tampered_cookie}"})

        assert response.status_code == 401


def test_missing_auth_rejected_for_api_endpoints(no_auth_client):
    assert no_auth_client.get("/").status_code == 200
    assert no_auth_client.get("/healthz").status_code == 200
    assert no_auth_client.get("/api/tasks").status_code == 401
    assert no_auth_client.post("/api/tasks", json={"title": "No auth"}).status_code == 401
    assert no_auth_client.get("/api/api-keys").status_code == 401


def test_bearer_read_only_can_read_but_not_write_or_claim(client, no_auth_client):
    created_key = client.post("/api/api-keys", json={"name": "reader", "role": "read_only"}).json()
    task = create_task(client, status="todo")
    headers = bearer_headers(created_key["api_key"])

    assert no_auth_client.get("/api/tasks", headers=headers).status_code == 200
    assert no_auth_client.post(f"/api/tasks/{task['id']}/claim", json={}, headers=headers).status_code == 403
    assert no_auth_client.post("/api/tasks", json={"title": "Denied"}, headers=headers).status_code == 403
    denied_patch = no_auth_client.patch(f"/api/tasks/{task['id']}", json={"title": "Denied"}, headers=headers)
    assert denied_patch.status_code == 403
    assert no_auth_client.get("/api/api-keys", headers=headers).status_code == 403


def test_bearer_admin_can_manage_keys_and_write_tasks(client, no_auth_client):
    created_key = client.post("/api/api-keys", json={"name": "admin-bearer", "role": "admin"}).json()
    headers = bearer_headers(created_key["api_key"])

    created_task = no_auth_client.post("/api/tasks", json={"title": "Bearer admin task"}, headers=headers)
    assert created_task.status_code == 201, created_task.text
    assert no_auth_client.get("/api/api-keys", headers=headers).status_code == 200

    revoked = no_auth_client.post(f"/api/api-keys/{created_key['id']}/revoke", json={}, headers=headers)
    assert revoked.status_code == 200
    assert no_auth_client.get("/api/tasks", headers=headers).status_code == 401


def test_invalid_and_revoked_bearer_tokens_receive_401(client, no_auth_client):
    created_key = client.post("/api/api-keys", json={"name": "temporary", "role": "admin"}).json()

    invalid = no_auth_client.get("/api/tasks", headers=bearer_headers("flow_invalid"))
    assert invalid.status_code == 401

    revoked = client.post(f"/api/api-keys/{created_key['id']}/revoke", json={})
    assert revoked.status_code == 200
    response = no_auth_client.get("/api/tasks", headers=bearer_headers(created_key["api_key"]))
    assert response.status_code == 401


def test_implementer_role_can_read_and_move_but_not_create_or_done(client, no_auth_client):
    created_key = client.post("/api/api-keys", json={"name": "implementer", "role": "implementer"}).json()
    task = create_task(client, status="todo")
    headers = bearer_headers(created_key["api_key"])

    assert no_auth_client.get("/api/tasks", headers=headers).status_code == 200
    direct_review = no_auth_client.post(f"/api/tasks/{task['id']}/move", json={"status": "review"}, headers=headers)
    assert direct_review.status_code == 403

    moved = no_auth_client.post(f"/api/tasks/{task['id']}/move", json={"status": "doing"}, headers=headers)
    assert moved.status_code == 200
    assert moved.json()["status"] == "doing"

    moved = no_auth_client.post(f"/api/tasks/{task['id']}/move", json={"status": "review"}, headers=headers)
    assert moved.status_code == 200
    assert moved.json()["status"] == "review"

    assert no_auth_client.post("/api/tasks", json={"title": "Denied"}, headers=headers).status_code == 403
    done = no_auth_client.post(f"/api/tasks/{task['id']}/done", json={"summary": "Denied"}, headers=headers)
    assert done.status_code == 403


def test_implementer_can_move_todo_to_doing_but_not_to_done(client, no_auth_client):
    headers = create_role_headers(client, "implementer", "implementer-forward")
    task = create_task(client, status="todo")

    moved = no_auth_client.post(f"/api/tasks/{task['id']}/move", json={"status": "doing"}, headers=headers)
    assert moved.status_code == 200
    assert moved.json()["status"] == "doing"

    denied_done = no_auth_client.post(f"/api/tasks/{task['id']}/move", json={"status": "done"}, headers=headers)
    assert denied_done.status_code == 403

    moved = no_auth_client.post(f"/api/tasks/{task['id']}/move", json={"status": "review"}, headers=headers)
    assert moved.status_code == 200
    assert moved.json()["status"] == "review"

    denied_done = no_auth_client.post(f"/api/tasks/{task['id']}/move", json={"status": "done"}, headers=headers)
    assert denied_done.status_code == 403


def test_reviewer_cannot_move_todo_to_done(client, no_auth_client):
    headers = create_role_headers(client, "reviewer", "reviewer-todo-done")
    task = create_task(client, status="todo")

    response = no_auth_client.post(f"/api/tasks/{task['id']}/move", json={"status": "done"}, headers=headers)
    assert response.status_code == 403


def test_reviewer_can_approve_and_send_back(client, no_auth_client):
    headers = create_role_headers(client, "reviewer")
    done_task = create_task(client, status="review")
    todo_task = create_task(client, status="review")
    doing_task = create_task(client, status="review")

    approved = no_auth_client.post(f"/api/tasks/{done_task['id']}/move", json={"status": "done"}, headers=headers)
    assert approved.status_code == 200
    assert approved.json()["status"] == "done"

    note = no_auth_client.post(
        f"/api/tasks/{todo_task['id']}/note",
        json={"note": "Needs follow-up before approval."},
        headers=headers,
    )
    assert note.status_code == 200

    sent_to_todo = no_auth_client.post(f"/api/tasks/{todo_task['id']}/move", json={"status": "todo"}, headers=headers)
    assert sent_to_todo.status_code == 200
    assert sent_to_todo.json()["status"] == "todo"

    sent_to_doing = no_auth_client.post(f"/api/tasks/{doing_task['id']}/move", json={"status": "doing"}, headers=headers)
    assert sent_to_doing.status_code == 200
    assert sent_to_doing.json()["status"] == "doing"


def test_reviewer_sendback_requires_note_or_handoff(client, no_auth_client):
    reviewer_headers = create_role_headers(client, "reviewer", "reviewer-sendback")
    implementer_headers = create_role_headers(client, "implementer", "implementer-sendback")
    admin_headers = create_role_headers(client, "admin", "admin-sendback")
    architect_headers = create_role_headers(client, "architect", "architect-sendback")

    no_context_task = create_task(client, status="review")
    denied = no_auth_client.post(
        f"/api/tasks/{no_context_task['id']}/move",
        json={"status": "todo"},
        headers=reviewer_headers,
    )
    assert denied.status_code == 409
    assert denied.json()["detail"] == "Send-back requires a reviewer-authored note or handoff."

    note = no_auth_client.post(
        f"/api/tasks/{no_context_task['id']}/note",
        json={"note": "Please address the failing edge case."},
        headers=reviewer_headers,
    )
    assert note.status_code == 200

    allowed_with_note = no_auth_client.post(
        f"/api/tasks/{no_context_task['id']}/move",
        json={"status": "todo"},
        headers=reviewer_headers,
    )
    assert allowed_with_note.status_code == 200
    assert allowed_with_note.json()["status"] == "todo"

    handoff_task = create_task(client, status="review")
    handoff = no_auth_client.post(
        f"/api/tasks/{handoff_task['id']}/handoffs",
        json={
            "summary": "Reviewer handoff with required changes.",
            "changed_files": ["flow_app/main.py"],
            "tests_run": ["tests/test_api.py"],
            "outcome": "partial",
        },
        headers=reviewer_headers,
    )
    assert handoff.status_code == 201, handoff.text

    allowed_with_handoff = no_auth_client.post(
        f"/api/tasks/{handoff_task['id']}/move",
        json={"status": "todo"},
        headers=reviewer_headers,
    )
    assert allowed_with_handoff.status_code == 200
    assert allowed_with_handoff.json()["status"] == "todo"

    implementer_note_task = create_task(client, status="doing", assignee="implementer-sendback")
    implementer_note = no_auth_client.post(
        f"/api/tasks/{implementer_note_task['id']}/note",
        json={"note": "Implementation context from before review."},
        headers=implementer_headers,
    )
    assert implementer_note.status_code == 200, implementer_note.text

    moved_to_review = no_auth_client.post(
        f"/api/tasks/{implementer_note_task['id']}/move",
        json={"status": "review"},
        headers=implementer_headers,
    )
    assert moved_to_review.status_code == 200
    assert moved_to_review.json()["status"] == "review"

    denied_with_implementer_note = no_auth_client.post(
        f"/api/tasks/{implementer_note_task['id']}/move",
        json={"status": "todo"},
        headers=reviewer_headers,
    )
    assert denied_with_implementer_note.status_code == 409
    assert denied_with_implementer_note.json()["detail"] == "Send-back requires a reviewer-authored note or handoff."

    reviewer_note = no_auth_client.post(
        f"/api/tasks/{implementer_note_task['id']}/note",
        json={"note": "Reviewer requested changes."},
        headers=reviewer_headers,
    )
    assert reviewer_note.status_code == 200, reviewer_note.text

    allowed_after_reviewer_note = no_auth_client.post(
        f"/api/tasks/{implementer_note_task['id']}/move",
        json={"status": "todo"},
        headers=reviewer_headers,
    )
    assert allowed_after_reviewer_note.status_code == 200
    assert allowed_after_reviewer_note.json()["status"] == "todo"

    admin_task = create_task(client, status="review")
    admin_sendback = no_auth_client.post(
        f"/api/tasks/{admin_task['id']}/move",
        json={"status": "todo"},
        headers=admin_headers,
    )
    assert admin_sendback.status_code == 200
    assert admin_sendback.json()["status"] == "todo"

    architect_task = create_task(client, status="review")
    architect_sendback = no_auth_client.post(
        f"/api/tasks/{architect_task['id']}/move",
        json={"status": "todo"},
        headers=architect_headers,
    )
    assert architect_sendback.status_code == 200
    assert architect_sendback.json()["status"] == "todo"


def test_admin_can_do_any_transition(client, no_auth_client):
    headers = create_role_headers(client, "admin")
    transitions = [
        ("backlog", "todo"),
        ("todo", "doing"),
        ("doing", "review"),
        ("review", "done"),
        ("done", "doing"),
        ("doing", "backlog"),
    ]

    for index, (current, target) in enumerate(transitions):
        task = create_task(client, title=f"Admin transition {index}", status=current)
        moved = no_auth_client.post(f"/api/tasks/{task['id']}/move", json={"status": target}, headers=headers)
        assert moved.status_code == 200, moved.text
        assert moved.json()["status"] == target


def test_architect_can_do_any_transition(client, no_auth_client):
    headers = create_role_headers(client, "architect")
    transitions = [
        ("backlog", "todo"),
        ("todo", "doing"),
        ("doing", "review"),
        ("review", "done"),
        ("done", "review"),
        ("review", "backlog"),
    ]

    for index, (current, target) in enumerate(transitions):
        task = create_task(client, title=f"Architect transition {index}", status=current)
        moved = no_auth_client.post(f"/api/tasks/{task['id']}/move", json={"status": target}, headers=headers)
        assert moved.status_code == 200, moved.text
        assert moved.json()["status"] == target


def test_read_only_cannot_move_tasks(client, no_auth_client):
    headers = create_role_headers(client, "read_only")
    task = create_task(client, status="todo")

    moved = no_auth_client.post(f"/api/tasks/{task['id']}/move", json={"status": "doing"}, headers=headers)
    assert moved.status_code == 403


def test_read_only_cannot_claim_release_done_or_note_tasks(client, no_auth_client):
    headers = create_role_headers(client, "read_only", "strict-reader")
    task = create_task(client, status="todo", assignee="admin")

    assert no_auth_client.post(f"/api/tasks/{task['id']}/claim", json={"agent_name": "strict-reader"}, headers=headers).status_code == 403
    assert no_auth_client.post(f"/api/tasks/{task['id']}/release", json={}, headers=headers).status_code == 403
    assert no_auth_client.post(f"/api/tasks/{task['id']}/note", json={"note": "Denied."}, headers=headers).status_code == 403
    assert no_auth_client.patch(f"/api/tasks/{task['id']}", json={"title": "Forbidden"}, headers=headers).status_code == 403


class TestScopedNotePermissions:
    def test_admin_can_note_any_task(self, client, no_auth_client):
        headers = create_role_headers(client, "admin", "note-admin")

        for index, task in enumerate(
            [
                create_task(client, title="Admin note todo", status="todo"),
                create_task(client, title="Admin note review", status="review", assignee="other-agent"),
            ],
        ):
            response = no_auth_client.post(
                f"/api/tasks/{task['id']}/note",
                json={"note": f"Admin note {index}."},
                headers=headers,
            )
            assert response.status_code == 200, response.text
            assert response.json()["notes"][-1]["body"] == f"Admin note {index}."

    def test_architect_can_note_any_task(self, client, no_auth_client):
        headers = create_role_headers(client, "architect", "note-architect")

        for index, task in enumerate(
            [
                create_task(client, title="Architect note doing", status="doing"),
                create_task(client, title="Architect note backlog", status="backlog", assignee="other-agent"),
            ],
        ):
            response = no_auth_client.post(
                f"/api/tasks/{task['id']}/note",
                json={"note": f"Architect note {index}."},
                headers=headers,
            )
            assert response.status_code == 200, response.text
            assert response.json()["notes"][-1]["body"] == f"Architect note {index}."

    def test_implementer_can_note_their_own_assigned_task(self, client, no_auth_client):
        headers = create_role_headers(client, "implementer", "note-implementer")
        task = create_task(client, status="doing", assignee="note-implementer")

        response = no_auth_client.post(
            f"/api/tasks/{task['id']}/note",
            json={"note": "Implementation note."},
            headers=headers,
        )

        assert response.status_code == 200, response.text
        assert response.json()["notes"][-1]["body"] == "Implementation note."

    def test_implementer_cannot_note_unassigned_task(self, client, no_auth_client):
        headers = create_role_headers(client, "implementer", "note-implementer")
        task = create_task(client, status="todo")

        response = no_auth_client.post(
            f"/api/tasks/{task['id']}/note",
            json={"note": "Denied."},
            headers=headers,
        )

        assert response.status_code == 403
        assert client.get(f"/api/tasks/{task['id']}").json()["notes"] == []

    def test_implementer_cannot_note_task_assigned_to_someone_else(self, client, no_auth_client):
        headers = create_role_headers(client, "implementer", "note-implementer")
        task = create_task(client, status="doing", assignee="other-implementer")

        response = no_auth_client.post(
            f"/api/tasks/{task['id']}/note",
            json={"note": "Denied."},
            headers=headers,
        )

        assert response.status_code == 403
        assert client.get(f"/api/tasks/{task['id']}").json()["notes"] == []

    def test_scoped_note_permission_with_key_claim(self, client, no_auth_client):
        implementer_headers = create_role_headers(client, "implementer", "claim-key-implementer")
        task = create_task(client, status="todo")

        claimed = no_auth_client.post(
            f"/api/tasks/{task['id']}/claim",
            json={"agent_name": "different-agent"},
            headers=implementer_headers,
        )
        assert claimed.status_code == 200, claimed.text
        assert claimed.json()["assignee"] == "different-agent"

        note = no_auth_client.post(
            f"/api/tasks/{task['id']}/note",
            json={"note": "Key-owned claim note."},
            headers=implementer_headers,
        )
        assert note.status_code == 200, note.text
        assert note.json()["notes"][-1]["body"] == "Key-owned claim note."

        other_implementer_headers = create_role_headers(client, "implementer", "other-claim-key-implementer")
        other_note = no_auth_client.post(
            f"/api/tasks/{task['id']}/note",
            json={"note": "Denied."},
            headers=other_implementer_headers,
        )
        assert other_note.status_code == 403

        read_only_headers = create_role_headers(client, "read_only", "claim-note-reader")
        read_only_note = no_auth_client.post(
            f"/api/tasks/{task['id']}/note",
            json={"note": "Denied."},
            headers=read_only_headers,
        )
        assert read_only_note.status_code == 403

        reviewer_headers = create_role_headers(client, "reviewer", "claim-note-reviewer")
        review_task = create_task(client, status="review")
        reviewer_note = no_auth_client.post(
            f"/api/tasks/{review_task['id']}/note",
            json={"note": "Review note."},
            headers=reviewer_headers,
        )
        assert reviewer_note.status_code == 200, reviewer_note.text
        assert reviewer_note.json()["notes"][-1]["body"] == "Review note."

    def test_claim_key_takeover_prevented(self, client, no_auth_client):
        key_a_headers = create_role_headers(client, "implementer", "claim-key-a")
        key_b_headers = create_role_headers(client, "implementer", "claim-key-b")
        task = create_task(client, status="todo")

        first_claim = no_auth_client.post(
            f"/api/tasks/{task['id']}/claim",
            json={"agent_name": "test-impl"},
            headers=key_a_headers,
        )
        assert first_claim.status_code == 200, first_claim.text
        assert first_claim.json()["assignee"] == "test-impl"

        takeover_attempt = no_auth_client.post(
            f"/api/tasks/{task['id']}/claim",
            json={"agent_name": "test-impl"},
            headers=key_b_headers,
        )
        assert takeover_attempt.status_code == 409
        assert takeover_attempt.json()["detail"] == "Task is already claimed by a different key."

        repeat_claim = no_auth_client.post(
            f"/api/tasks/{task['id']}/claim",
            json={"agent_name": "test-impl"},
            headers=key_a_headers,
        )
        assert repeat_claim.status_code == 200, repeat_claim.text

        key_a_note = no_auth_client.post(
            f"/api/tasks/{task['id']}/note",
            json={"note": "Original claimer note."},
            headers=key_a_headers,
        )
        assert key_a_note.status_code == 200, key_a_note.text

        key_b_note = no_auth_client.post(
            f"/api/tasks/{task['id']}/note",
            json={"note": "Denied."},
            headers=key_b_headers,
        )
        assert key_b_note.status_code == 403

        released = no_auth_client.post(f"/api/tasks/{task['id']}/release", json={}, headers=key_a_headers)
        assert released.status_code == 200, released.text
        assert released.json()["assignee"] is None

        key_b_claim_after_release = no_auth_client.post(
            f"/api/tasks/{task['id']}/claim",
            json={"agent_name": "test-impl"},
            headers=key_b_headers,
        )
        assert key_b_claim_after_release.status_code == 200, key_b_claim_after_release.text
        assert key_b_claim_after_release.json()["assignee"] == "test-impl"

    def test_legacy_claim_without_claimer_key_id_uses_assignee_fallback(self, client, no_auth_client):
        key_a_headers = create_role_headers(client, "implementer", "legacy-impl")
        key_b_headers = create_role_headers(client, "implementer", "legacy-impl")
        task = create_task(client, status="doing", assignee="legacy-impl")

        key_a_note = no_auth_client.post(
            f"/api/tasks/{task['id']}/note",
            json={"note": "Legacy fallback note A."},
            headers=key_a_headers,
        )
        assert key_a_note.status_code == 200, key_a_note.text

        with client.app.state.SessionLocal() as db:
            db_task = db.get(Task, task["id"])
            assert db_task is not None
            db_task.assignee = "legacy-impl"
            db_task.status = "doing"
            db_task.claimer_key_id = None
            db.commit()

        key_b_reclaim = no_auth_client.post(
            f"/api/tasks/{task['id']}/claim",
            json={"agent_name": "legacy-impl"},
            headers=key_b_headers,
        )
        assert key_b_reclaim.status_code == 200, key_b_reclaim.text

        released = no_auth_client.post(f"/api/tasks/{task['id']}/release", json={}, headers=key_b_headers)
        assert released.status_code == 200, released.text

        with client.app.state.SessionLocal() as db:
            db_task = db.get(Task, task["id"])
            assert db_task is not None
            db_task.assignee = "legacy-impl"
            db_task.status = "doing"
            db_task.claimer_key_id = None
            db.commit()

        key_b_note = no_auth_client.post(
            f"/api/tasks/{task['id']}/note",
            json={"note": "Legacy fallback note B."},
            headers=key_b_headers,
        )
        assert key_b_note.status_code == 200, key_b_note.text

    def test_reviewer_can_note_task_in_review_status(self, client, no_auth_client):
        headers = create_role_headers(client, "reviewer", "note-reviewer")
        task = create_task(client, status="review")

        response = no_auth_client.post(
            f"/api/tasks/{task['id']}/note",
            json={"note": "Review note."},
            headers=headers,
        )

        assert response.status_code == 200, response.text
        assert response.json()["notes"][-1]["body"] == "Review note."

    def test_reviewer_cannot_note_task_in_todo_status(self, client, no_auth_client):
        headers = create_role_headers(client, "reviewer", "note-reviewer")
        task = create_task(client, status="todo")

        response = no_auth_client.post(
            f"/api/tasks/{task['id']}/note",
            json={"note": "Denied."},
            headers=headers,
        )

        assert response.status_code == 403
        assert client.get(f"/api/tasks/{task['id']}").json()["notes"] == []

    def test_reviewer_cannot_note_task_in_doing_status(self, client, no_auth_client):
        headers = create_role_headers(client, "reviewer", "note-reviewer")
        task = create_task(client, status="doing")

        response = no_auth_client.post(
            f"/api/tasks/{task['id']}/note",
            json={"note": "Denied."},
            headers=headers,
        )

        assert response.status_code == 403
        assert client.get(f"/api/tasks/{task['id']}").json()["notes"] == []

    def test_read_only_cannot_note_any_task(self, client, no_auth_client):
        headers = create_role_headers(client, "read_only", "note-reader")
        task = create_task(client, status="review")

        response = no_auth_client.post(
            f"/api/tasks/{task['id']}/note",
            json={"note": "Denied."},
            headers=headers,
        )

        assert response.status_code == 403
        assert client.get(f"/api/tasks/{task['id']}").json()["notes"] == []


def test_done_cannot_be_reopened_by_implementer_or_reviewer(client, no_auth_client):
    implementer_headers = create_role_headers(client, "implementer", "done-implementer")
    reviewer_headers = create_role_headers(client, "reviewer", "done-reviewer")
    implementer_task = create_task(client, status="done")
    reviewer_task = create_task(client, status="done")

    implementer_reopen = no_auth_client.post(
        f"/api/tasks/{implementer_task['id']}/move",
        json={"status": "doing"},
        headers=implementer_headers,
    )
    assert implementer_reopen.status_code == 403

    reviewer_reopen = no_auth_client.post(
        f"/api/tasks/{reviewer_task['id']}/move",
        json={"status": "doing"},
        headers=reviewer_headers,
    )
    assert reviewer_reopen.status_code == 403


def test_transition_validation_on_claim(client, no_auth_client):
    headers = create_role_headers(client, "implementer")
    backlog_task = create_task(client, status="backlog")
    todo_task = create_task(client, status="todo")

    backlog_claim = no_auth_client.post(f"/api/tasks/{backlog_task['id']}/claim", json={}, headers=headers)
    assert backlog_claim.status_code == 403

    todo_claim = no_auth_client.post(f"/api/tasks/{todo_task['id']}/claim", json={}, headers=headers)
    assert todo_claim.status_code == 200
    assert todo_claim.json()["status"] == "doing"

    reviewer_headers = create_role_headers(client, "reviewer")
    reviewer_todo = create_task(client, status="todo")
    reviewer_claim = no_auth_client.post(f"/api/tasks/{reviewer_todo['id']}/claim", json={}, headers=reviewer_headers)
    assert reviewer_claim.status_code == 200
    assert reviewer_claim.json()["status"] == "doing"

    architect_headers = create_role_headers(client, "architect")
    architect_backlog = create_task(client, status="backlog")
    architect_claim = no_auth_client.post(
        f"/api/tasks/{architect_backlog['id']}/claim",
        json={},
        headers=architect_headers,
    )
    assert architect_claim.status_code == 200
    assert architect_claim.json()["status"] == "doing"


def test_role_permission_mapping():
    assert Permission.KEY_MANAGE in ROLE_PERMISSIONS[ApiKeyRole.admin]
    assert Permission.KEY_MANAGE not in ROLE_PERMISSIONS[ApiKeyRole.architect]
    assert Permission.TASKS_CREATE in ROLE_PERMISSIONS[ApiKeyRole.architect]
    assert Permission.TASKS_CREATE not in ROLE_PERMISSIONS[ApiKeyRole.implementer]
    assert Permission.TASKS_SET_HUMAN_REQUIRED in ROLE_PERMISSIONS[ApiKeyRole.admin]
    assert Permission.TASKS_SET_HUMAN_REQUIRED in ROLE_PERMISSIONS[ApiKeyRole.architect]
    assert Permission.TASKS_SET_HUMAN_REQUIRED not in ROLE_PERMISSIONS[ApiKeyRole.implementer]
    assert Permission.TASKS_SET_HUMAN_REQUIRED not in ROLE_PERMISSIONS[ApiKeyRole.reviewer]
    assert Permission.TASKS_SET_HUMAN_REQUIRED not in ROLE_PERMISSIONS[ApiKeyRole.read_only]
    assert Permission.TASKS_CLAIM not in ROLE_PERMISSIONS[ApiKeyRole.read_only]
    assert Permission.HANDOFF_READ in ROLE_PERMISSIONS[ApiKeyRole.read_only]
    assert Permission.HANDOFF_CREATE in ROLE_PERMISSIONS[ApiKeyRole.admin]
    assert Permission.HANDOFF_CREATE in ROLE_PERMISSIONS[ApiKeyRole.architect]
    assert Permission.HANDOFF_CREATE in ROLE_PERMISSIONS[ApiKeyRole.implementer]
    assert Permission.HANDOFF_CREATE in ROLE_PERMISSIONS[ApiKeyRole.reviewer]
    assert Permission.HANDOFF_CREATE not in ROLE_PERMISSIONS[ApiKeyRole.read_only]
    assert Permission.HANDOFF_MANAGE in ROLE_PERMISSIONS[ApiKeyRole.admin]
    assert Permission.HANDOFF_MANAGE in ROLE_PERMISSIONS[ApiKeyRole.architect]
    assert Permission.HANDOFF_MANAGE not in ROLE_PERMISSIONS[ApiKeyRole.implementer]


def test_next_prefers_todo_before_backlog_then_priority(client):
    backlog = create_task(client, title="Backlog high", status="backlog", priority=100)
    todo_high = create_task(client, title="Todo high", status="todo", priority=80)
    todo_low = create_task(client, title="Todo low", status="todo", priority=60)

    response = client.get("/api/tasks/next?project=default")
    assert response.status_code == 200
    assert response.json()["id"] == todo_high["id"]

    client.post(f"/api/tasks/{todo_high['id']}/claim", json={"agent_name": "codex"})
    response = client.get("/api/tasks/next?project=default")
    assert response.json()["id"] == todo_low["id"]

    client.post(f"/api/tasks/{todo_low['id']}/claim", json={"agent_name": "codex"})
    response = client.get("/api/tasks/next?project=default")
    assert response.json()["id"] == backlog["id"]


def test_next_filters_project_and_excludes_done(client):
    create_task(client, title="Other project", project="elsewhere", priority=100)
    done = create_task(client, title="Done task", status="done", priority=200)

    response = client.get("/api/tasks/next?project=default")
    assert response.status_code == 404

    response = client.get("/api/tasks/next?project=elsewhere")
    assert response.status_code == 200
    assert response.json()["title"] == "Other project"
    assert done["status"] == "done"


def test_claim_conflict_and_release_behavior(client):
    task = create_task(client, status="todo")

    claimed = client.post(f"/api/tasks/{task['id']}/claim", json={"agent_name": "codex"})
    assert claimed.status_code == 200
    assert claimed.json()["assignee"] == "codex"
    assert claimed.json()["status"] == "doing"

    conflict = client.post(f"/api/tasks/{task['id']}/claim", json={"agent_name": "other"})
    assert conflict.status_code == 409

    released = client.post(f"/api/tasks/{task['id']}/release", json={})
    assert released.status_code == 200
    assert released.json()["assignee"] is None
    assert released.json()["status"] == "todo"


def test_move_note_and_done(client):
    task = create_task(client, status="todo")

    moved = client.post(f"/api/tasks/{task['id']}/move", json={"status": "review"})
    assert moved.status_code == 200
    assert moved.json()["status"] == "review"

    noted = client.post(f"/api/tasks/{task['id']}/note", json={"note": "Ready for QA.", "author": "codex"})
    assert noted.status_code == 200
    assert noted.json()["notes"][0]["body"] == "Ready for QA."

    done = client.post(f"/api/tasks/{task['id']}/done", json={"summary": "Verified route.", "author": "codex"})
    assert done.status_code == 200
    body = done.json()
    assert body["status"] == "done"
    assert [note["body"] for note in body["notes"]] == ["Ready for QA.", "Verified route."]


def test_task_handoff_create_list_get_and_done_payload(client):
    task = create_task(client, status="review")

    created = client.post(
        f"/api/tasks/{task['id']}/handoff",
        json={
            "summary": "Review found one follow-up.",
            "author": "reviewer",
            "changed_files": ["flow_app/main.py"],
            "commands_run": ["pytest tests/test_api.py"],
            "tests_run": ["tests/test_api.py"],
            "attempted_but_failed": ["full suite not run"],
            "remaining_work": "Run full suite.",
            "outcome": "partial",
            "next_recommended_agent": "implementer",
            "capabilities": ["api"],
        },
    )
    assert created.status_code == 201, created.text
    handoff = created.json()
    assert handoff["id"] == "handoff_000001"
    assert handoff["task_id"] == task["id"]
    assert handoff["changed_files"] == ["flow_app/main.py"]
    assert handoff["outcome"] == "partial"

    listed = client.get(f"/api/tasks/{task['id']}/handoffs")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [handoff["id"]]

    fetched = client.get(f"/api/tasks/{task['id']}/handoffs/{handoff['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["remaining_work"] == "Run full suite."

    done = client.post(
        f"/api/tasks/{task['id']}/done",
        json={
            "summary": "Finished follow-up.",
            "handoff": {
                "summary": "Follow-up implemented.",
                "changed_files": ["flow_app/repository.py"],
                "tests_run": ["uv run --extra test python -m pytest tests/ -v"],
                "outcome": "success",
            },
        },
    )
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["status"] == "done"
    assert body["notes"][-1]["body"] == "Finished follow-up."
    assert body["latest_handoff"]["id"] == "handoff_000002"
    assert body["latest_handoff"]["summary"] == "Follow-up implemented."


def test_implementer_and_reviewer_can_create_handoffs(client, no_auth_client):
    task = create_task(client, status="review")

    for role in ("implementer", "reviewer"):
        headers = create_role_headers(client, role, f"{role}-handoff")
        created = no_auth_client.post(
            f"/api/tasks/{task['id']}/handoffs",
            json={
                "summary": f"{role} handoff.",
                "changed_files": ["flow_app/security.py"],
                "tests_run": ["tests/test_api.py"],
                "outcome": "success",
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        assert created.json()["author"] == f"{role}-handoff"


def test_read_only_cannot_create_handoffs(client, no_auth_client):
    headers = create_role_headers(client, "read_only", "handoff-reader")
    task = create_task(client, status="review")

    denied = no_auth_client.post(
        f"/api/tasks/{task['id']}/handoffs",
        json={"summary": "Should be denied."},
        headers=headers,
    )

    assert denied.status_code == 403


def test_board_renders_drag_and_drop_hooks(client):
    task = create_task(client, status="todo", priority=80)

    response = client.get("/")
    assert response.status_code == 200

    html = response.text
    assert 'data-drop-zone' in html
    assert 'data-column-count' in html
    assert f'data-task-id="{task["id"]}"' in html
    assert 'data-task-priority="80"' in html


def test_validation_errors(client):
    assert client.post("/api/tasks", json={"title": "", "status": "todo"}).status_code == 422
    task = create_task(client)
    assert client.post(f"/api/tasks/{task['id']}/move", json={"status": "later"}).status_code == 422
    claimed = client.post(f"/api/tasks/{task['id']}/claim", json={})
    assert claimed.status_code == 200
    assert claimed.json()["assignee"] == "test-admin"
    assert client.get("/api/tasks?status=later").status_code == 422


def test_human_required_fields_default_to_agent_values(client):
    """Existing tasks and new tasks default to human_required=false, assignee_type=agent."""
    task = create_task(client)
    assert task["human_required"] is False
    assert task["assignee_type"] == "agent"
    assert task["blocker_reason"] == ""


def test_create_task_with_human_required_fields(client):
    """Create a task with explicit human_required, assignee_type, blocker_reason."""
    payload = {
        "title": "Deploy GPU agent",
        "status": "doing",
        "priority": 95,
        "project": "default",
        "human_required": True,
        "assignee_type": "human",
        "blocker_reason": "Needs Windows hardware access",
    }
    response = client.post("/api/tasks", json=payload)
    assert response.status_code == 201
    task = response.json()
    assert task["human_required"] is True
    assert task["assignee_type"] == "human"
    assert task["blocker_reason"] == "Needs Windows hardware access"


def test_patch_task_human_required_fields(client):
    """Patch human_required, assignee_type, and blocker_reason."""
    task = create_task(client)
    assert task["human_required"] is False
    assert task["assignee_type"] == "agent"

    patched = client.patch(
        f"/api/tasks/{task['id']}",
        json={"human_required": True, "assignee_type": "mixed", "blocker_reason": "Waiting on ops"},
    ).json()
    assert patched["human_required"] is True
    assert patched["assignee_type"] == "mixed"
    assert patched["blocker_reason"] == "Waiting on ops"

    # Clear human_required and blocker_reason
    cleared = client.patch(
        f"/api/tasks/{task['id']}",
        json={"human_required": False, "blocker_reason": ""},
    ).json()
    assert cleared["human_required"] is False
    assert cleared["blocker_reason"] == ""


def test_admin_can_set_and_clear_human_required_on_any_task(client, no_auth_client):
    headers = create_role_headers(client, "admin", "human-admin")
    task = create_task(client, assignee="other")

    marked = no_auth_client.patch(
        f"/api/tasks/{task['id']}",
        json={"human_required": True, "assignee_type": "human", "blocker_reason": "Need admin decision"},
        headers=headers,
    )
    assert marked.status_code == 200, marked.text
    assert marked.json()["human_required"] is True
    assert marked.json()["assignee_type"] == "human"

    cleared = no_auth_client.patch(
        f"/api/tasks/{task['id']}",
        json={"human_required": False},
        headers=headers,
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["human_required"] is False
    assert cleared.json()["blocker_reason"] == ""


def test_architect_can_set_and_clear_human_required_on_any_task(client, no_auth_client):
    headers = create_role_headers(client, "architect", "human-architect")
    task = create_task(client, assignee="other")

    marked = no_auth_client.patch(
        f"/api/tasks/{task['id']}",
        json={"human_required": True, "assignee_type": "mixed", "blocker_reason": "Needs product call"},
        headers=headers,
    )
    assert marked.status_code == 200, marked.text
    assert marked.json()["human_required"] is True
    assert marked.json()["assignee_type"] == "mixed"

    cleared = no_auth_client.patch(
        f"/api/tasks/{task['id']}",
        json={"human_required": False},
        headers=headers,
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["human_required"] is False
    assert cleared.json()["blocker_reason"] == ""


def test_implementer_can_mark_own_claimed_task_human_required(client, no_auth_client):
    headers = create_role_headers(client, "implementer", "human-implementer")
    task = create_task(client, assignee="human-implementer", status="doing")

    response = no_auth_client.patch(
        f"/api/tasks/{task['id']}",
        json={"human_required": True, "blocker_reason": "Need credentials"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["human_required"] is True
    assert response.json()["blocker_reason"] == "Need credentials"


def test_implementer_cannot_mark_another_implementers_task_human_required(client, no_auth_client):
    headers = create_role_headers(client, "implementer", "human-implementer")
    task = create_task(client, assignee="other-implementer", status="doing")

    response = no_auth_client.patch(
        f"/api/tasks/{task['id']}",
        json={"human_required": True, "blocker_reason": "Need credentials"},
        headers=headers,
    )
    assert response.status_code == 403
    assert client.get(f"/api/tasks/{task['id']}").json()["human_required"] is False


def test_implementer_cannot_clear_human_required_on_own_task(client, no_auth_client):
    headers = create_role_headers(client, "implementer", "human-implementer")
    task = create_task(client, assignee="human-implementer", status="doing", human_required=True, blocker_reason="blocked")

    response = no_auth_client.patch(
        f"/api/tasks/{task['id']}",
        json={"human_required": False},
        headers=headers,
    )
    assert response.status_code == 403
    unchanged = client.get(f"/api/tasks/{task['id']}").json()
    assert unchanged["human_required"] is True
    assert unchanged["blocker_reason"] == "blocked"


def test_implementer_can_mark_own_claimed_task_human_required_without_assignee_type(client, no_auth_client):
    headers = create_role_headers(client, "implementer", "human-implementer")
    task = create_task(client, assignee="human-implementer", status="doing")

    response = no_auth_client.patch(
        f"/api/tasks/{task['id']}",
        json={"human_required": True, "blocker_reason": "Need credentials"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    marked = response.json()
    assert marked["human_required"] is True
    assert marked["assignee_type"] == "agent"
    assert marked["blocker_reason"] == "Need credentials"


def test_reviewer_can_mark_review_task_human_required(client, no_auth_client):
    headers = create_role_headers(client, "reviewer", "human-reviewer")
    review_task = create_task(client, status="review")
    todo_task = create_task(client, status="todo")

    marked = no_auth_client.patch(
        f"/api/tasks/{review_task['id']}",
        json={"human_required": True, "blocker_reason": "Needs acceptance clarification"},
        headers=headers,
    )
    assert marked.status_code == 200, marked.text
    assert marked.json()["human_required"] is True

    denied = no_auth_client.patch(
        f"/api/tasks/{todo_task['id']}",
        json={"human_required": True, "blocker_reason": "Needs acceptance clarification"},
        headers=headers,
    )
    assert denied.status_code == 403
    assert client.get(f"/api/tasks/{todo_task['id']}").json()["human_required"] is False


def test_reviewer_cannot_clear_human_required(client, no_auth_client):
    headers = create_role_headers(client, "reviewer", "human-reviewer")
    task = create_task(client, status="review", human_required=True, blocker_reason="blocked")

    response = no_auth_client.patch(
        f"/api/tasks/{task['id']}",
        json={"human_required": False},
        headers=headers,
    )
    assert response.status_code == 403
    unchanged = client.get(f"/api/tasks/{task['id']}").json()
    assert unchanged["human_required"] is True
    assert unchanged["blocker_reason"] == "blocked"


def test_reviewer_can_mark_review_task_human_required_without_assignee_type(client, no_auth_client):
    headers = create_role_headers(client, "reviewer", "human-reviewer")
    task = create_task(client, status="review")

    response = no_auth_client.patch(
        f"/api/tasks/{task['id']}",
        json={"human_required": True, "blocker_reason": "Need sign-off"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    marked = response.json()
    assert marked["human_required"] is True
    assert marked["assignee_type"] == "agent"
    assert marked["blocker_reason"] == "Need sign-off"


def test_read_only_cannot_change_human_required_fields(client, no_auth_client):
    headers = create_role_headers(client, "read_only", "human-reader")
    task = create_task(client)

    response = no_auth_client.patch(
        f"/api/tasks/{task['id']}",
        json={"human_required": True, "blocker_reason": "Need access"},
        headers=headers,
    )
    assert response.status_code == 403
    assert client.get(f"/api/tasks/{task['id']}").json()["human_required"] is False


def test_clearing_human_required_also_clears_blocker_reason(client):
    task = create_task(client, human_required=True, blocker_reason="blocked")

    response = client.patch(
        f"/api/tasks/{task['id']}",
        json={"human_required": False},
    )
    assert response.status_code == 200
    assert response.json()["human_required"] is False
    assert response.json()["blocker_reason"] == ""


def test_forbidden_human_required_attempt_does_not_partially_update_task(client, no_auth_client):
    headers = create_role_headers(client, "implementer", "human-implementer")
    task = create_task(client, assignee="other-implementer", status="doing")

    response = no_auth_client.patch(
        f"/api/tasks/{task['id']}",
        json={"human_required": True, "blocker_reason": "Need credentials"},
        headers=headers,
    )
    assert response.status_code == 403
    unchanged = client.get(f"/api/tasks/{task['id']}").json()
    assert unchanged["human_required"] is False
    assert unchanged["assignee_type"] == "agent"
    assert unchanged["blocker_reason"] == ""


def test_invalid_assignee_type_rejected(client):
    """Invalid assignee_type values return 422 validation errors."""
    response = client.post(
        "/api/tasks",
        json={"title": "Bad type", "status": "todo", "assignee_type": "robot"},
    )
    assert response.status_code == 422

    task = create_task(client)
    patch_response = client.patch(
        f"/api/tasks/{task['id']}",
        json={"assignee_type": "invalid"},
    )
    assert patch_response.status_code == 422


def test_human_required_in_list_and_get_responses(client):
    """human_required, assignee_type, blocker_reason appear in list and get endpoints."""
    task = create_task(client, human_required=True, assignee_type="mixed", blocker_reason="blocked")
    task_id = task["id"]

    # GET /api/tasks/{id}
    fetched = client.get(f"/api/tasks/{task_id}").json()
    assert fetched["human_required"] is True
    assert fetched["assignee_type"] == "mixed"
    assert fetched["blocker_reason"] == "blocked"

    # GET /api/tasks
    listed = client.get("/api/tasks").json()["items"]
    found = [t for t in listed if t["id"] == task_id][0]
    assert found["human_required"] is True
    assert found["assignee_type"] == "mixed"
    assert found["blocker_reason"] == "blocked"


def test_qualification_fields_default_values(client):
    task = create_task(client)
    assert task["complexity"] == "small"
    assert task["impact"] == "medium"
    assert task["effort"] == "medium"
    assert task["risk"] == "low"


def test_create_task_with_qualification_fields(client):
    task = create_task(
        client,
        complexity="large",
        impact="critical",
        effort="high",
        risk="high",
    )
    assert task["complexity"] == "large"
    assert task["impact"] == "critical"
    assert task["effort"] == "high"
    assert task["risk"] == "high"


def test_patch_qualification_fields(client):
    task = create_task(client)
    patched = client.patch(
        f"/api/tasks/{task['id']}",
        json={"complexity": "epic", "impact": "low", "effort": "low", "risk": "medium"},
    ).json()
    assert patched["complexity"] == "epic"
    assert patched["impact"] == "low"
    assert patched["effort"] == "low"
    assert patched["risk"] == "medium"


def test_invalid_qualification_enum_rejected(client):
    assert client.post("/api/tasks", json={"title": "Bad", "complexity": "huge"}).status_code == 422
    assert client.post("/api/tasks", json={"title": "Bad", "impact": "extreme"}).status_code == 422
    assert client.post("/api/tasks", json={"title": "Bad", "effort": "massive"}).status_code == 422
    assert client.post("/api/tasks", json={"title": "Bad", "risk": "extreme"}).status_code == 422


def test_healthz_returns_ok(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["database"] is True


def test_healthz_returns_503_when_db_down(no_auth_client):
    from flow_app.main import create_app
    from sqlalchemy import text

    # Create an app with a broken database URL
    app = create_app(database_url="sqlite:///nonexistent/path/that/does/not/exist/flow.sqlite")
    from fastapi.testclient import TestClient
    broken_client = TestClient(app)
    response = broken_client.get("/healthz")
    assert response.status_code == 503
    assert response.json()["ok"] is False
