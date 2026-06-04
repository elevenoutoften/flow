from __future__ import annotations

import os

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from flow_app.config import reset_settings_cache
from flow_app.database import build_engine, build_session_factory
from flow_app.main import create_app
from flow_app.models import AgentApiKey, ApiKeyRole
from flow_app.repository import create_agent_api_key
from flow_app.schemas import AgentApiKeyCreate
from flow_app.serve import ensure_session_secret, main as serve_main


def _make_app(tmp_path, *, session_secret="login-test-secret"):
    db_url = f"sqlite:///{tmp_path / 'flow.sqlite'}"
    # trusted_headers=False so the login flow is the only way to authenticate the browser.
    return create_app(db_url, trusted_headers=False, session_secret=session_secret)


def _create_admin_key(app) -> str:
    with app.state.SessionLocal() as db:
        _, raw_key = create_agent_api_key(db, AgentApiKeyCreate(name="login-key", role=ApiKeyRole.admin))
        db.commit()
    return raw_key


def test_login_page_renders(tmp_path):
    app = _make_app(tmp_path)
    with TestClient(app) as client:
        response = client.get("/login")

    assert response.status_code == 200
    assert "Sign in" in response.text


def test_login_with_valid_key_sets_session_cookie(tmp_path):
    app = _make_app(tmp_path)
    with TestClient(app) as client:
        key = _create_admin_key(app)
        response = client.post("/login", data={"api_key": key}, follow_redirects=False)

        assert response.status_code == 303
        assert response.headers["location"] == "/"
        assert "flow_session=" in response.headers.get("set-cookie", "")

        # The cookie authenticates subsequent requests: the board shows the signed-in actor.
        board = client.get("/")
        assert "Sign out" in board.text


def test_login_with_bad_key_redirects_with_error(tmp_path):
    app = _make_app(tmp_path)
    with TestClient(app) as client:
        response = client.post("/login", data={"api_key": "flow_does_not_exist"}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login?error=invalid"


def test_login_disabled_without_session_secret(tmp_path):
    app = _make_app(tmp_path, session_secret="")
    with TestClient(app) as client:
        response = client.post("/login", data={"api_key": "anything"}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login?error=disabled"


def test_logout_redirects_and_clears_cookie(tmp_path):
    app = _make_app(tmp_path)
    with TestClient(app) as client:
        response = client.get("/logout", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_ensure_session_secret_creates_and_reuses(tmp_path, monkeypatch):
    monkeypatch.delenv("FLOW_SESSION_SECRET", raising=False)
    data_dir = tmp_path / "data"

    first = ensure_session_secret(data_dir)
    assert first
    assert (data_dir / "session_secret").read_text(encoding="utf-8").strip() == first
    # Stable across calls (sessions survive restarts).
    assert ensure_session_secret(data_dir) == first


def test_ensure_session_secret_respects_env(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOW_SESSION_SECRET", "explicit-secret")
    data_dir = tmp_path / "data"

    assert ensure_session_secret(data_dir) == "explicit-secret"
    assert not (data_dir / "session_secret").exists()  # explicit env wins; no file written


def test_serve_bootstrap_seeds_before_running(tmp_path, monkeypatch):
    import uvicorn

    db_url = f"sqlite:///{tmp_path / 'flow.sqlite'}"
    data_dir = tmp_path / "data"
    run_calls = []

    monkeypatch.setenv("FLOW_DATABASE_URL", db_url)
    monkeypatch.setenv("FLOW_DATA_DIR", str(data_dir))
    monkeypatch.delenv("FLOW_SESSION_SECRET", raising=False)
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: run_calls.append((args, kwargs)))

    try:
        assert serve_main(["--bootstrap", "--host", "127.0.0.1", "--port", "8123"]) == 0
    finally:
        os.environ.pop("FLOW_SESSION_SECRET", None)
        reset_settings_cache()

    assert run_calls == [(("flow_app.main:app",), {"host": "127.0.0.1", "port": 8123, "reload": False})]
    engine = build_engine(db_url)
    session_factory = build_session_factory(engine)
    with session_factory() as db:
        key_count = db.scalar(select(func.count()).select_from(AgentApiKey))
    assert key_count == 3
