from __future__ import annotations

import re
from pathlib import Path


def test_board_renders_columns_and_create_form(client):
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    for column in ["backlog", "todo", "doing", "review", "done"]:
        assert f'data-column="{column}"' in html
    assert 'id="create-form"' in html
    assert 'id="detail-drawer"' in html
    assert 'id="ideasOverlay"' in html
    assert 'id="settingsOverlay"' in html
    assert "data-open-ideas" in html
    assert "data-open-settings" in html


def test_settings_surface_renders_live_management_forms(client):
    response = client.get("/settings.html")
    assert response.status_code == 200
    html = response.text
    assert 'id="project-form"' in html
    assert 'id="project-form-title"' in html
    assert 'id="project-form-cancel"' in html
    assert "data-edit-project" in html
    assert 'id="api-key-form"' in html
    assert 'id="import-form"' in html
    assert "Import Markdown" in html


def test_settings_surface_wires_api_ready_segments(client):
    response = client.get("/settings.html")
    assert response.status_code == 200
    html = response.text
    for section, form_id, table_id in (
        ("agents", "agent-form", "agent-list"),
        ("workspaces", "workspace-form", "workspace-list"),
        ("agent-runs", "run-test", "agent-run-list"),
        ("automation-rules", "rule-form", "automation-rule-list"),
        ("webhooks", "webhook-form", "webhook-list"),
    ):
        assert f'id="{section}" data-settings-live="{section}"' in html
        assert f'id="{form_id}"' in html
        assert f'id="{table_id}"' in html

    assert "Backend API available. Service logic is in place; UI wiring pending." not in html
    assert "Workers &amp; adapters" in html
    assert "Run isolation" in html
    assert "Dispatch history" in html
    assert "Rules engine" in html
    assert "HTTP integrations" in html


def test_settings_live_segments_call_real_apis_and_preserve_rule_json():
    script = Path("flow_app/static/flow-settings.js").read_text(encoding="utf-8")
    for endpoint in (
        '"/api/agents"',
        '"/api/workspace-configs"',
        '"/api/agent-runs',
        '"/api/automation-rules',
        '"/api/webhooks"',
    ):
        assert endpoint in script
    assert "bindLiveSettingsControls()" in script
    assert "loadLiveSettingsData()" in script
    assert "keepSidebarLinkInView" in script
    assert 'hidden.id = select.id' in script
    save_body = script.split("async function saveAutomationRule", 1)[1].split("function openRuleTest", 1)[0]
    assert "buildRuleJsonFromControls();" not in save_body
    assert "normalizeJsonText" in save_body


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
    assert f'["{parent["id"]}","{child["id"]}"]' in response.text.replace(" ", "")
    assert "Parent task" in response.text
    assert "Child task" in response.text


def test_api_key_form_has_role_selector(client):
    response = client.get("/settings.html")
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
    response = client.get("/settings.html")
    assert response.status_code == 200
    html = response.text
    assert "api-key-role implementer" in html
    assert ">implementer<" in html


def test_human_required_badge_on_task_card(client):
    client.post(
        "/api/tasks",
        json={
            "title": "Blocked task",
            "status": "doing",
            "priority": 50,
            "project": "default",
            "human_required": True,
            "blocker_reason": "Need approval",
        },
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


def test_board_has_ideas_button_and_surface(client):
    response = client.get("/")
    html = response.text
    assert "data-open-ideas" in html
    assert 'id="ideasOverlay"' in html
    assert 'id="ideasFrame"' in html

    ideas = client.get("/ideas.html")
    assert ideas.status_code == 200
    assert 'id="ideaWall"' in ideas.text


def test_settings_surface_includes_theme_selector(client):
    html = client.get("/settings.html").text
    assert 'name="theme"' in html
    # flow2 ships Axis Love (default), Axis Teal, Axis Leaf; Neutral is the grey extra, last.
    assert "Axis Love" in html
    assert "Axis Teal" in html
    assert "Axis Leaf" in html
    assert "Neutral" in html


def test_board_includes_settings_surface_entrypoint(client):
    html = client.get("/").text
    assert 'id="settingsOverlay"' in html
    assert 'id="settingsFrame"' in html
    assert "data-open-settings" in html

    settings = client.get("/settings.html").text
    assert "Projects" in settings
    assert "API Keys" in settings


def test_ideas_quick_add_card_in_surface(client):
    response = client.get("/ideas.html")
    html = response.text
    assert 'class="idea-card is-template"' in html
    assert "Add new idea" in html
    assert "window.FlowIdeas" in html


def test_idea_editor_is_generated_by_ideas_surface_script(client):
    html = client.get("/ideas.html").text
    assert "/static/flow-ideas.js" in html
    script = Path("flow_app/static/flow-ideas.js").read_text(encoding="utf-8")
    assert "openIdeaEditor" in script
    assert 'data-editor-action="save"' in script


def test_board_dropdown_keyboard_and_drag_autoscroll_are_wired():
    script = Path("flow_app/static/flow.js").read_text(encoding="utf-8")
    assert 'trigger.addEventListener("keydown"' in script
    assert 'event.key === "ArrowDown"' in script
    assert 'event.key === "Enter" || event.key === " "' in script
    assert "function autoScrollDuringDrag" in script
    assert "boardArea.scrollLeft" in script
    assert "list.scrollTop" in script


def test_ideas_author_tooltips_and_dropdown_keyboard_are_wired():
    script = Path("flow_app/static/flow-ideas.js").read_text(encoding="utf-8")
    assert 'hoverTip.id = "hover-tip"' in script
    assert "bindTooltips(scope)" in script
    assert 'element.addEventListener("mouseenter", showTip)' in script
    assert 'trigger.addEventListener("keydown"' in script
    assert 'event.key === "ArrowDown"' in script
    assert 'event.key === "Enter" || event.key === " "' in script


def test_ideas_surface_project_scoping_markup(client):
    html = client.get("/ideas.html?project=alpha").text
    assert 'data-project="alpha"' in html
    assert 'selectedProject: "alpha"' in html
    assert 'id="idea-form"' not in html


def test_ideas_quick_add_uses_selected_project(client_with_admin):
    client_with_admin.post("/api/ideas", json={"title": "Alpha idea", "project": "alpha"})
    client_with_admin.post("/api/ideas", json={"title": "Beta idea", "project": "beta"})

    response = client_with_admin.post("/api/ideas", json={"title": "Quick alpha", "project": "alpha"})
    assert response.status_code == 201
    idea = response.json()
    assert idea["project"] == "alpha"
    assert idea["author"] == "alice"

    filtered = client_with_admin.get("/api/ideas?project=alpha")
    assert filtered.status_code == 200
    alpha_ideas = [item for item in filtered.json()["items"] if item["project"] == "alpha"]
    assert len(alpha_ideas) == 2


def test_board_includes_data_theme(html):
    assert "data-theme=" in html


def test_board_defers_overlay_mutations_instead_of_reloading():
    """A settings/ideas mutation must not reload the board while the overlay is
    open — that would destroy in-flight content such as a one-time API key.
    The host marks the board dirty and reloads only once the overlay closes."""
    script = Path("flow_app/static/flow.js").read_text(encoding="utf-8")
    assert "state.boardDirty = true" in script
    assert "function flushBoardDirty" in script
    assert '"flow:ideas-mutated"' in script and '"flow:settings-mutated"' in script
    # The previous (buggy) behavior reloaded the parent inline on every mutation.
    assert 'mutated") window.location.reload' not in script


def test_theme_switch_is_wired_end_to_end():
    """The appearance control must actually apply + persist a theme and sync it
    to the host board, and the board must define the non-default accent."""
    board_js = Path("flow_app/static/flow.js").read_text(encoding="utf-8")
    settings_js = Path("flow_app/static/flow-settings.js").read_text(encoding="utf-8")
    board_css = Path("flow_app/static/flow.css").read_text(encoding="utf-8")
    assert 'type: "flow:theme"' in settings_js
    assert 'localStorage.setItem("flow.theme"' in settings_js
    assert '"flow:theme"' in board_js and "flow.theme" in board_js
    # The board must define every non-default accent so theme switching is visible there.
    for token in ('[data-theme="teal"]', '[data-theme="leaf"]', '[data-theme="neutral"]'):
        assert token in board_css


def test_settings_surface_uses_flow2_theme_tokens(client):
    """Theme option values use the flow2 tokens; Axis Love is the default."""
    html = client.get("/settings.html").text
    for value in ('value="love"', 'value="teal"', 'value="leaf"', 'value="neutral"'):
        assert value in html
    # Default theme renders on <body> and the love radio is preselected.
    assert 'data-theme="love"' in html
    assert 'value="love" checked' in html


def test_settings_live_numeric_fields_use_token_steppers(client):
    html = client.get("/settings.html").text
    for name in (
        "max_concurrency",
        "heartbeat_timeout_seconds",
        "stale_claim_timeout_seconds",
        "priority",
        "max_retries",
        "retry_backoff_seconds",
    ):
        assert re.search(r'<div class="number-stepper"[^>]*>.*?<input class="form-input" name="' + name + r'" type="number"', html, re.S)

    script = Path("flow_app/static/flow-settings.js").read_text(encoding="utf-8")
    assert "initNumberSteppers()" in script
    assert '".number-stepper, .priority-counter"' in script


def test_settings_check_controls_use_flow2_token_styles():
    css = Path("flow_app/static/flow-settings.css").read_text(encoding="utf-8")
    checks_block = css.split("/* checks & radios */", 1)[1].split("/*", 1)[0]
    assert "appearance:none" in checks_block
    assert ".check:has(input:checked)" in checks_block
    assert "accent-color" not in checks_block
    assert ".toggle input:focus-visible+.toggle-track" in css


def test_settings_markup_uses_css_classes_for_local_spacing(client):
    html = client.get("/settings.html").text
    assert "style=" not in html
    assert "form-hint-spaced" in html
    assert "system-table is-flush" in html


def test_dependency_overlay_uses_flow2_edge_renderer():
    script = Path("flow_app/static/flow.js").read_text(encoding="utf-8")
    assert "updateDepLinesUI" in script
    assert "flow-dep-lines" in script
    assert "clipPathUnits" in script
    assert "dep-path" in script


def test_dependency_hover_lines_allow_offscreen_connected_cards():
    script = Path("flow_app/static/flow.js").read_text(encoding="utf-8")
    highlight_body = script.split("function highlightCardDeps", 1)[1].split("function clearHighlight", 1)[0]
    assert "depMakePath" in highlight_body
    assert "depPointInsideClip" not in highlight_body


def test_wrapped_flow_select_menus_do_not_capture_closed_clicks():
    for path in (
        "flow_app/static/flow.css",
        "flow_app/static/flow-ideas.css",
        "flow_app/static/flow-settings.css",
    ):
        css = Path(path).read_text(encoding="utf-8")
        assert ".flow-scroll-wrapper-flow-select-menu>.flow-select-menu" in css
        assert ".flow-select.is-open>.flow-scroll-wrapper-flow-select-menu>.flow-select-menu" in css
        assert "flow-scroll-wrapper-flow-select-menu>.flow-select-menu" in css and "pointer-events:none" in css


def test_settings_selects_are_upgraded_to_flow2_dropdowns():
    script = Path("flow_app/static/flow-settings.js").read_text(encoding="utf-8")
    assert "enhanceSelects(document)" in script
    assert "select.form-input" in script
    assert "flow-select-trigger" in script
