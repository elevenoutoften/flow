from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from flow_app.config import reset_settings_cache
from flow_app.main import create_app


ADMIN_HEADERS = {"X-Axis-Admin": "1", "X-Axis-User": "test-admin"}


@pytest.fixture(autouse=True)
def _reset_settings():
    reset_settings_cache()
    yield
    reset_settings_cache()


@pytest.fixture()
def client(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'flow.sqlite'}"
    app = create_app(db_url, trusted_headers=True)
    with TestClient(app) as test_client:
        test_client.headers.update(ADMIN_HEADERS)
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
    app = create_app(db_url, trusted_headers=True)
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
