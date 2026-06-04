from __future__ import annotations

from fastapi.testclient import TestClient

from flow_app.config import reset_settings_cache
from flow_app.main import _render_llms_txt, create_app


def test_llms_txt_served_unauthenticated(no_auth_client):
    response = no_auth_client.get("/llms.txt")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text
    # Deployment-aware: the doc reflects the request's own base URL.
    assert "http://testserver/mcp" in body
    assert "http://testserver/api" in body
    assert "claude mcp add" in body


def test_render_llms_txt_contains_connect_flow():
    body = _render_llms_txt("http://x:8100")

    assert "claude mcp add --transport http flow http://x:8100/mcp" in body
    assert "http://x:8100/api/tasks/next" in body
    assert "github.com/elevenoutoften/flow" in body
    # No leftover template markers.
    assert "{BASE}" not in body
    assert "{DOCS}" not in body


def test_llms_txt_honors_public_url_override(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOW_PUBLIC_URL", "https://flow.example.com/")
    reset_settings_cache()
    app = create_app(f"sqlite:///{tmp_path / 'flow.sqlite'}")

    with TestClient(app) as client:
        body = client.get("/llms.txt").text

    assert "https://flow.example.com/mcp" in body
    # The trailing slash from the env value is stripped (no doubled slash).
    assert "https://flow.example.com//mcp" not in body
