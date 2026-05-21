from __future__ import annotations


def test_board_renders_columns_and_create_form(client):
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    for column in ["backlog", "todo", "doing", "review", "done"]:
        assert f'data-column="{column}"' in html
    assert 'id="create-form"' in html
    assert 'id="detail-drawer"' in html
    assert 'id="project-form"' in html
    assert 'id="project-form-title"' in html
    assert 'id="project-form-cancel"' in html
    assert 'data-edit-project' in html
    assert 'id="api-key-form"' in html
    assert 'id="import-form"' in html
    assert "Import Markdown" in html


def test_board_renders_handoff_section(client):
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    assert 'id="detail-handoff-section"' in html
    assert 'id="detail-handoff"' in html
    assert "Handoff" in html


def test_board_renders_task_card(client):
    client.post(
        "/api/tasks",
        json={"title": "Expose ComfyUI through server route", "status": "todo", "priority": 90, "project": "default"},
    )
    response = client.get("/")
    assert response.status_code == 200
    assert "Expose ComfyUI through server route" in response.text
    assert "P90" in response.text


def test_board_renders_dependency_summary_on_task_card(client):
    parent = client.post(
        "/api/tasks",
        json={"title": "Parent task", "status": "todo", "priority": 40, "project": "default"},
    ).json()
    child = client.post(
        "/api/tasks",
        json={"title": "Child task", "status": "backlog", "priority": 80, "project": "default"},
    ).json()
    link_response = client.post(
        f"/api/tasks/{parent['id']}/link",
        json={"parent_id": parent["id"], "child_id": child["id"], "link_type": "blocks"},
    )
    assert link_response.status_code == 201, link_response.text

    response = client.get("/")

    assert response.status_code == 200
    assert "dependency-summary" in response.text
    assert "Parent task" in response.text
    assert "Child task" in response.text


def test_api_key_form_has_role_selector(client):
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    assert 'name="role"' in html
    assert '<option value="read_only">' in html
    assert '<option value="implementer">' in html
    assert '<option value="reviewer">' in html
    assert '<option value="architect">' in html
    assert '<option value="admin">' in html


def test_api_key_card_shows_role_badge(client):
    client.post(
        "/api/api-keys",
        json={"name": "test-coder", "description": "Writes code", "role": "implementer"},
    )
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    assert "badge-role-implementer" in html
    assert ">implementer<" in html


def test_human_required_badge_on_task_card(client):
    client.post(
        "/api/tasks",
        json={"title": "Blocked task", "status": "doing", "priority": 50, "project": "default", "human_required": True, "blocker_reason": "Need approval"},
    )
    response = client.get("/")
    html = response.text
    assert "Human Required" in html
    assert "Need approval" in html
    assert "badge-human-required" in html


def test_board_has_human_required_filter(client):
    response = client.get("/")
    html = response.text
    assert "data-filter-human-required" in html
    assert "data-clear-human-filter" in html


def test_board_has_qualification_fields_in_create_form(client):
    response = client.get("/")
    html = response.text
    assert 'name="complexity"' in html
    assert 'name="impact"' in html
    assert 'name="effort"' in html
    assert 'name="risk"' in html


def test_board_has_ideas_button_and_workspace(client):
    response = client.get("/")
    html = response.text
    assert "data-open-ideas" in html
    assert 'id="ideas-workspace"' in html
    assert 'id="ideas-list"' in html


def test_board_includes_theme_selector(client):
    """Theme selector is present in the settings drawer."""
    html = client.get("/").text
    assert "data-theme-select" in html
    assert "available_themes" in html or "Neutral" in html
    assert "axis-love" in html


def test_board_includes_settings_drawer(client):
    """Settings drawer is present in the board."""
    html = client.get("/").text
    assert "settings-drawer" in html
    assert "data-open-settings" in html
    assert "Manage projects" in html
    assert "Manage API keys" in html


def test_ideas_quick_add_in_workspace(client):
    response = client.get("/")
    html = response.text
    assert 'id="ideas-quick-add-input"' in html
    assert 'id="ideas-quick-add-btn"' in html


def test_idea_edit_form_in_ideas_workspace(client):
    """Idea edit form is present in the ideas workspace."""
    html = client.get("/").text
    assert 'id="idea-edit-form"' in html
    assert "data-edit-idea" in html
    assert "data-cancel-edit-idea" in html


def test_ideas_workspace_project_scoping_markup(client):
    """Quick-add input and project select are present in the ideas workspace."""
    html = client.get("/").text
    assert 'id="ideas-workspace"' in html
    assert 'id="ideas-quick-add-input"' in html
    assert 'id="project-select"' in html
    assert 'id="idea-form"' not in html


def test_ideas_quick_add_uses_selected_project(client_with_admin):
    """Simulating the quick-add flow: POST /api/ideas with project from select."""
    client_with_admin.post("/api/ideas", json={"title": "Alpha idea", "project": "alpha"})
    client_with_admin.post("/api/ideas", json={"title": "Beta idea", "project": "beta"})

    response = client_with_admin.post("/api/ideas", json={"title": "Quick alpha", "project": "alpha"})
    assert response.status_code == 201
    idea = response.json()
    assert idea["project"] == "alpha"
    assert idea["author"] == "alice"

    filtered = client_with_admin.get("/api/ideas?project=alpha")
    assert filtered.status_code == 200
    alpha_ideas = [item for item in filtered.json() if item["project"] == "alpha"]
    assert len(alpha_ideas) == 2


def test_board_includes_data_theme(html):
    assert "data-theme=" in html
