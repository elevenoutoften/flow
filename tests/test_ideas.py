from __future__ import annotations


def create_idea(client, **overrides):
    payload = {
        "title": "New dark-mode UI",
        "description": "Add a dark theme toggle.",
        "project": "default",
        "author": "codex",
    }
    payload.update(overrides)
    response = client.post("/api/ideas", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_create_list_and_get_idea(client):
    idea = create_idea(client)
    assert idea["id"] == "idea_000001"
    assert idea["title"] == "New dark-mode UI"
    assert idea["description"] == "Add a dark theme toggle."
    assert idea["project"] == "default"
    assert idea["author"] == "codex"
    assert idea["archived_at"] is None

    listed = client.get("/api/ideas").json()
    assert [item["id"] for item in listed] == [idea["id"]]

    fetched = client.get(f"/api/ideas/{idea['id']}").json()
    assert fetched["id"] == idea["id"]
    assert fetched["title"] == idea["title"]


def test_update_idea(client):
    idea = create_idea(client)
    response = client.patch(
        f"/api/ideas/{idea['id']}",
        json={"title": "Updated title", "description": "Updated desc", "author": "nyx"},
    )
    assert response.status_code == 200
    patched = response.json()
    assert patched["title"] == "Updated title"
    assert patched["description"] == "Updated desc"
    assert patched["author"] == "nyx"
    assert patched["project"] == "default"


def test_archive_and_unarchive_idea(client):
    idea = create_idea(client)
    assert idea["archived_at"] is None

    archived = client.post(f"/api/ideas/{idea['id']}/archive", json={})
    assert archived.status_code == 200
    body = archived.json()
    assert body["archived_at"] is not None
    assert body["id"] == idea["id"]

    listed = client.get("/api/ideas").json()
    assert idea["id"] not in [item["id"] for item in listed]

    archived_listed = client.get("/api/ideas?archived=true").json()
    assert idea["id"] in [item["id"] for item in archived_listed]

    unarchived = client.post(f"/api/ideas/{idea['id']}/unarchive", json={})
    assert unarchived.status_code == 200
    assert unarchived.json()["archived_at"] is None

    listed = client.get("/api/ideas").json()
    assert idea["id"] in [item["id"] for item in listed]


def test_list_ideas_filters_project(client):
    create_idea(client, project="default", title="Idea A")
    create_idea(client, project="other-project", title="Idea B")

    all_ideas = client.get("/api/ideas").json()
    assert len(all_ideas) == 2

    filtered = client.get("/api/ideas?project=other-project").json()
    assert len(filtered) == 1
    assert filtered[0]["title"] == "Idea B"


def test_idea_not_found(client):
    assert client.get("/api/ideas/idea_999999").status_code == 404
    assert client.patch("/api/ideas/idea_999999", json={"title": "x"}).status_code == 404
    assert client.post("/api/ideas/idea_999999/archive", json={}).status_code == 404
    assert client.post("/api/ideas/idea_999999/unarchive", json={}).status_code == 404


def test_validation_errors(client):
    assert client.post("/api/ideas", json={"title": ""}).status_code == 422
    assert client.post("/api/ideas", json={"title": "ok", "project": ""}).status_code == 422

    idea = create_idea(client)
    assert client.patch(f"/api/ideas/{idea['id']}", json={"title": ""}).status_code == 422
    assert client.patch(f"/api/ideas/{idea['id']}", json={"project": ""}).status_code == 422


def test_idea_requires_auth(no_auth_client):
    assert no_auth_client.get("/api/ideas").status_code == 401
    assert no_auth_client.post("/api/ideas", json={"title": "x"}).status_code == 401
    assert no_auth_client.get("/api/ideas/idea_000001").status_code == 401
    assert no_auth_client.patch("/api/ideas/idea_000001", json={"title": "x"}).status_code == 401
    assert no_auth_client.post("/api/ideas/idea_000001/archive", json={}).status_code == 401


def test_read_only_can_read_ideas(client, no_auth_client):
    created_key = client.post("/api/api-keys", json={"name": "reader", "role": "read_only"}).json()
    headers = {"Authorization": f"Bearer {created_key['api_key']}"}
    idea = create_idea(client)

    assert no_auth_client.get("/api/ideas", headers=headers).status_code == 200
    assert no_auth_client.get(f"/api/ideas/{idea['id']}", headers=headers).status_code == 200
    assert no_auth_client.post("/api/ideas", json={"title": "x"}, headers=headers).status_code == 403
    assert no_auth_client.patch(f"/api/ideas/{idea['id']}", json={"title": "x"}, headers=headers).status_code == 403
    assert no_auth_client.post(f"/api/ideas/{idea['id']}/archive", json={}, headers=headers).status_code == 403


def test_ensure_project_created_for_idea(client):
    response = client.post("/api/ideas", json={"title": "Auto project", "project": "new-project"})
    assert response.status_code == 201
    assert response.json()["project"] == "new-project"

    projects = client.get("/api/projects").json()
    assert "new-project" in [p["slug"] for p in projects]


def test_promote_idea_creates_linked_tasks(client):
    idea = create_idea(client)
    assert idea["promoted_task_ids"] == []

    promote_payload = [
        {"title": "Design dark mode toggle", "status": "todo", "priority": 80},
        {"title": "Implement dark mode CSS", "status": "backlog", "priority": 60},
    ]
    response = client.post(f"/api/ideas/{idea['id']}/promote", json=promote_payload)
    assert response.status_code == 200
    body = response.json()
    assert len(body["promoted_task_ids"]) == 2
    assert body["archived_at"] is not None

    # Verify the tasks exist and link back
    for tid in body["promoted_task_ids"]:
        task = client.get(f"/api/tasks/{tid}").json()
        assert task["import_batch_id"] == idea["id"]
        assert task["project"] == idea["project"]


def test_promote_idea_not_found(client):
    response = client.post("/api/ideas/idea_999999/promote", json=[{"title": "x"}])
    assert response.status_code == 404


def test_promote_idea_requires_create_permission(client, no_auth_client):
    idea = create_idea(client)
    read_key = client.post("/api/api-keys", json={"name": "reader", "role": "read_only"}).json()
    headers = {"Authorization": f"Bearer {read_key['api_key']}"}
    assert no_auth_client.post(
        f"/api/ideas/{idea['id']}/promote",
        json=[{"title": "task"}],
        headers=headers,
    ).status_code == 403
