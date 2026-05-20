from __future__ import annotations

from flow_app import agent_client


def test_agent_project_helpers_call_expected_endpoints(monkeypatch):
    calls = []

    def fake_request(method, path, body=None, *, query=None):
        calls.append((method, path, body, query))
        return [{"slug": "default"}] if path == "/api/projects" else {"ok": True}

    monkeypatch.setattr(agent_client, "_request", fake_request)

    assert agent_client.flow_list_projects() == [{"slug": "default"}]
    assert agent_client.flow_get_project("default") == {"ok": True}
    created = agent_client.flow_create_task("Task", project="default", priority=90)
    preview = agent_client.flow_import_markdown_preview("- [ ] Task", source_filename="tasks.md")

    assert created == {"ok": True}
    assert preview == {"ok": True}
    assert calls[0] == ("GET", "/api/projects", None, None)
    assert calls[1] == ("GET", "/api/projects/default", None, None)
    assert calls[2][0:2] == ("POST", "/api/tasks")
    assert calls[2][2]["title"] == "Task"
    assert calls[2][2]["priority"] == 90
    assert calls[3][0:2] == ("POST", "/api/import/markdown/preview")
    assert calls[3][2]["source_filename"] == "tasks.md"
