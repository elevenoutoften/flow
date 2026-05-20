from __future__ import annotations


def create_task(client, **overrides):
    payload = {
        "title": "Dependency task",
        "status": "backlog",
        "priority": 50,
        "project": "default",
        "description": "",
        "acceptance_criteria": "",
    }
    payload.update(overrides)
    response = client.post("/api/tasks", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def create_role_headers(client, role: str) -> dict[str, str]:
    created = client.post("/api/api-keys", json={"name": f"{role}-links", "role": role})
    assert created.status_code == 201, created.text
    return {"Authorization": f"Bearer {created.json()['api_key']}"}


def rpc(client, name: str, arguments: dict, headers: dict | None = None):
    return client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}},
        headers=headers or {},
    )


def link_tasks(client, parent_id: str, child_id: str, link_type: str = "blocks"):
    response = client.post(
        f"/api/tasks/{parent_id}/link",
        json={"parent_id": parent_id, "child_id": child_id, "link_type": link_type},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_link_creation_accepts_valid_types(client):
    parent = create_task(client, title="Parent")
    child = create_task(client, title="Child")
    third = create_task(client, title="Third")
    fourth = create_task(client, title="Fourth")

    assert link_tasks(client, parent["id"], child["id"], "blocks")["link_type"] == "blocks"
    assert link_tasks(client, parent["id"], third["id"], "depends_on")["link_type"] == "depends_on"
    assert link_tasks(client, parent["id"], fourth["id"], "related")["link_type"] == "related"


def test_self_link_rejected(client):
    task = create_task(client)

    response = client.post(
        f"/api/tasks/{task['id']}/link",
        json={"parent_id": task["id"], "child_id": task["id"], "link_type": "blocks"},
    )

    assert response.status_code == 422


def test_cycle_detection_for_blocking_links(client):
    first = create_task(client, title="First")
    second = create_task(client, title="Second")
    third = create_task(client, title="Third")
    link_tasks(client, first["id"], second["id"], "blocks")
    link_tasks(client, second["id"], third["id"], "depends_on")

    response = client.post(
        f"/api/tasks/{third['id']}/link",
        json={"parent_id": third["id"], "child_id": first["id"], "link_type": "blocks"},
    )

    assert response.status_code == 409
    assert "cycle" in response.json()["detail"]


def test_related_links_can_form_cycles(client):
    first = create_task(client, title="First")
    second = create_task(client, title="Second")
    link_tasks(client, first["id"], second["id"], "related")

    response = client.post(
        f"/api/tasks/{second['id']}/link",
        json={"parent_id": second["id"], "child_id": first["id"], "link_type": "related"},
    )

    assert response.status_code == 201, response.text


def test_link_deletion(client):
    parent = create_task(client)
    child = create_task(client)
    link = link_tasks(client, parent["id"], child["id"])

    deleted = client.delete(f"/api/tasks/{parent['id']}/link/{link['id']}")
    assert deleted.status_code == 204
    assert client.get(f"/api/tasks/{parent['id']}/links").json() == []


def test_dependency_summary_groups_blocking_links(client):
    parent = create_task(client, title="Parent")
    child = create_task(client, title="Child")
    related = create_task(client, title="Related")
    blocking = link_tasks(client, parent["id"], child["id"], "blocks")
    relation = link_tasks(client, child["id"], related["id"], "related")

    summary = client.get(f"/api/tasks/{child['id']}/dependencies").json()

    assert [link["id"] for link in summary["parents"]] == [blocking["id"]]
    assert [link["id"] for link in summary["children"]] == [relation["id"]]
    assert [link["id"] for link in summary["blocked_by"]] == [blocking["id"]]
    assert summary["blocking"] == []
    assert summary["parent_tasks"] == [
        {
            "id": parent["id"],
            "title": "Parent",
            "status": "backlog",
            "priority": 50,
            "project": "default",
            "assignee": None,
        }
    ]
    assert summary["child_tasks"] == [
        {
            "id": related["id"],
            "title": "Related",
            "status": "backlog",
            "priority": 50,
            "project": "default",
            "assignee": None,
        }
    ]
    assert summary["blocked_by_tasks"] == summary["parent_tasks"]
    assert summary["blocking_tasks"] == []


def test_dependency_summary_empty_for_task_without_links(client):
    task = create_task(client, title="Standalone")

    response = client.get(f"/api/tasks/{task['id']}/dependencies")

    assert response.status_code == 200
    assert response.json() == {
        "parents": [],
        "children": [],
        "blocked_by": [],
        "blocking": [],
        "parent_tasks": [],
        "child_tasks": [],
        "blocked_by_tasks": [],
        "blocking_tasks": [],
    }


def test_auto_promotion_when_all_blocking_parents_done(client):
    parent = create_task(client, title="Parent", status="review")
    child = create_task(client, title="Child", status="backlog")
    link_tasks(client, parent["id"], child["id"], "blocks")

    done = client.post(f"/api/tasks/{parent['id']}/done", json={"summary": "Complete"})
    assert done.status_code == 200, done.text

    promoted = client.get(f"/api/tasks/{child['id']}").json()
    assert promoted["status"] == "todo"
    assert any(note["author"] == "system" and "unblocked" in note["body"] for note in promoted["notes"])


def test_auto_promotion_waits_for_all_blocking_parents(client):
    first = create_task(client, title="First parent", status="review")
    second = create_task(client, title="Second parent", status="todo")
    child = create_task(client, title="Child", status="backlog")
    link_tasks(client, first["id"], child["id"], "blocks")
    link_tasks(client, second["id"], child["id"], "depends_on")

    done = client.post(f"/api/tasks/{first['id']}/done", json={"summary": "Complete"})
    assert done.status_code == 200, done.text

    assert client.get(f"/api/tasks/{child['id']}").json()["status"] == "backlog"


def test_link_permissions(client, no_auth_client):
    reader_headers = create_role_headers(client, "read_only")
    implementer_headers = create_role_headers(client, "implementer")
    parent = create_task(client)
    child = create_task(client)
    link = link_tasks(client, parent["id"], child["id"])

    listed = no_auth_client.get(f"/api/tasks/{parent['id']}/links", headers=reader_headers)
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == link["id"]

    denied = no_auth_client.post(
        f"/api/tasks/{parent['id']}/link",
        json={"parent_id": parent["id"], "child_id": child["id"], "link_type": "related"},
        headers=implementer_headers,
    )
    assert denied.status_code == 403


def test_mcp_task_link_tools(client, no_auth_client):
    parent = create_task(client)
    child = create_task(client)

    link_response = rpc(client, "flow_link_task", {"parent_id": parent["id"], "child_id": child["id"]})
    assert link_response.status_code == 200
    link = link_response.json()["result"]["structuredContent"]["link"]

    list_response = rpc(client, "flow_list_task_links", {"task_id": parent["id"]})
    assert list_response.json()["result"]["structuredContent"]["links"][0]["id"] == link["id"]

    dependency_response = rpc(client, "flow_get_dependencies", {"task_id": child["id"]})
    assert dependency_response.json()["result"]["structuredContent"]["dependencies"]["blocked_by"][0]["id"] == link["id"]

    reader_headers = create_role_headers(client, "read_only")
    denied = rpc(no_auth_client, "flow_link_task", {"parent_id": parent["id"], "child_id": child["id"]}, reader_headers)
    assert denied.json()["error"]["code"] == -32603

    unlink_response = rpc(client, "flow_unlink_task", {"link_id": link["id"]})
    assert unlink_response.json()["result"]["structuredContent"]["deleted"] is True
