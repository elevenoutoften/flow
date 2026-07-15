from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from flow_app.config import reset_settings_cache
from flow_app.main import create_app
from flow_app.metrics import metrics
from flow_app.ratelimit import auth_limiter, key_creation_limiter, mutation_limiter


ADMIN_HEADERS = {"X-Axis-Admin": "1", "X-Axis-User": "test-admin"}


@pytest.fixture(autouse=True)
def _reset_settings():
    # Isolate FLOW_* env vars so the user's environment doesn't leak into tests.
    # This fixes test_dogfood_e2e.py failures where FLOW_BASE_URL from the
    # user's shell causes the test to hit a real server instead of the test app.
    _saved_env: dict[str, str] = {}
    for key in list(os.environ):
        if key.startswith("FLOW_"):
            _saved_env[key] = os.environ.pop(key)
    reset_settings_cache()
    metrics.reset()
    auth_limiter.reset()
    key_creation_limiter.reset()
    mutation_limiter.reset()
    yield
    metrics.reset()
    auth_limiter.reset()
    key_creation_limiter.reset()
    mutation_limiter.reset()
    reset_settings_cache()
    # Restore env vars after tests
    os.environ.update(_saved_env)


@pytest.fixture()
def client(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'flow.sqlite'}"
    app = create_app(db_url, trusted_headers=True, session_secret="test-secret-for-testing")
    with TestClient(app) as test_client:
        test_client.headers.update(ADMIN_HEADERS)
        yield test_client


@pytest.fixture()
def client_with_admin(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'flow-admin.sqlite'}"
    app = create_app(db_url, trusted_headers=True, session_secret="test-secret-for-testing")
    with TestClient(app) as test_client:
        test_client.headers.update({"X-Axis-Admin": "1", "X-Axis-User": "alice"})
        test_client.get("/")
        yield test_client


@pytest.fixture()
def no_auth_client(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'flow.sqlite'}"
    app = create_app(db_url)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def shared_clients(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'flow.sqlite'}"
    app = create_app(db_url, trusted_headers=True, session_secret="test-secret-for-testing")
    auth = TestClient(app, raise_server_exceptions=False)
    auth.headers.update(ADMIN_HEADERS)
    unauth = TestClient(app, raise_server_exceptions=False)
    yield auth, unauth
    auth.close()
    unauth.close()


@pytest.fixture()
def html(client):
    response = client.get("/")
    return response.text
