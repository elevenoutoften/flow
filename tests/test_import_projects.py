from __future__ import annotations

from flow_app.markdown_import import parse_markdown_tasks


def test_project_crud(client):
    response = client.post(
        "/api/projects",
        json={
            "slug": "flow",
            "name": "Flow",
            "repo_url": "https://github.com/elevenoutften/flow",
            "repo_path": "/var/lib/flow",
            "default_branch": "main",
        },
    )
    assert response.status_code == 201, response.text
    project = response.json()
    assert project["slug"] == "flow"
    assert project["name"] == "Flow"

    patched = client.patch(
        "/api/projects/flow",
        json={
            "description": "Operator UI work",
            "repo_url": "https://github.com/elevenoutften/flow/tree/main/site",
            "repo_path": "/var/lib/flow/site",
        },
    )
    assert patched.status_code == 200
    assert patched.json()["description"] == "Operator UI work"
    assert patched.json()["repo_url"] == "https://github.com/elevenoutften/flow/tree/main/site"
    assert patched.json()["repo_path"] == "/var/lib/flow/site"

    listed = client.get("/api/projects").json()
    assert "flow" in [item["slug"] for item in listed]


def test_markdown_parser_supports_headings_metadata_and_multiline_description():
    markdown = """# Sprint 1
Planning context for this batch.

- [ ] Add import wizard priority: 90 project: my-project assignee: codex
  Build a preview-first UI.
  acceptance: Preview shows duplicate warnings.

## Done things
- [x] Ship old task
  status: review
"""
    items = parse_markdown_tasks(
        markdown,
        source_filename="tasks.md",
        default_project="default-project",
        default_status="backlog",
        default_priority=50,
    )

    assert len(items) == 2
    assert items[0].title == "Add import wizard"
    assert items[0].priority == 90
    assert items[0].project == "my-project"
    assert items[0].assignee == "codex"
    assert "Planning context" in items[0].description
    assert "Build a preview-first UI." in items[0].description
    assert items[0].acceptance_criteria == "Preview shows duplicate warnings."
    assert items[0].source_filename == "tasks.md"
    assert items[0].source_title == "Sprint 1"
    assert items[1].status == "review"
    assert items[1].source_title == "Sprint 1 / Done things"


def test_markdown_import_preview_commit_and_duplicate_detection(client):
    markdown = """# Flow
- [ ] Import repo tasks priority: 80
  Parse checklist files.
- [x] Archive old list
"""
    preview = client.post(
        "/api/import/markdown/preview",
        json={
            "markdown": markdown,
            "source_filename": "tasks.md",
            "default_project": "default",
            "default_status": "backlog",
            "default_priority": 50,
        },
    )
    assert preview.status_code == 200, preview.text
    items = preview.json()["items"]
    assert len(items) == 2
    assert items[0]["duplicate"] is False

    committed = client.post("/api/import/markdown/commit", json={"items": items})
    assert committed.status_code == 200, committed.text
    payload = committed.json()
    assert payload["import_batch_id"].startswith("import_")
    assert len(payload["created"]) == 2
    assert payload["created"][0]["source_filename"] == "tasks.md"
    assert payload["created"][0]["source_line"] == 2
    assert payload["created"][0]["source_title"] == "Flow"

    duplicate_preview = client.post(
        "/api/import/markdown/preview",
        json={
            "markdown": markdown,
            "source_filename": "tasks.md",
            "default_project": "default",
        },
    )
    duplicate_items = duplicate_preview.json()["items"]
    assert duplicate_items[0]["duplicate"] is True
    assert duplicate_items[0]["duplicate_task_id"].startswith("flow_")
