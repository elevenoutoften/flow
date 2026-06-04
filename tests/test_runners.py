from __future__ import annotations


def bearer_headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


def create_role_headers(client, role: str, name: str) -> dict[str, str]:
    response = client.post("/api/api-keys", json={"name": name, "role": role})
    assert response.status_code == 201, response.text
    return bearer_headers(response.json()["api_key"])


def create_runner(client, **overrides) -> dict:
    payload = {
        "name": "remote-runner",
        "description": "Remote runner",
        "capabilities": "python,tests",
        "agent_names": "implementer,reviewer",
    }
    payload.update(overrides)
    response = client.post("/api/runners", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_runner_crud_and_soft_delete(client):
    runner = create_runner(client)

    assert runner["id"].startswith("runner_")
    assert runner["enabled"] is True
    assert runner["runner_type"] == "poll"
    assert runner["status"] == "offline"
    assert runner["lease_duration_seconds"] == 600
    assert runner["heartbeat_interval_seconds"] == 60
    assert runner["max_concurrent_leases"] == 1

    listed = client.get("/api/runners").json()
    assert listed["total"] == 1
    assert listed["items"][0]["id"] == runner["id"]

    fetched = client.get(f"/api/runners/{runner['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "remote-runner"

    updated = client.patch(
        f"/api/runners/{runner['id']}",
        json={
            "description": "Updated",
            "enabled": False,
            "runner_type": "push",
            "status": "draining",
            "lease_duration_seconds": 120,
            "heartbeat_interval_seconds": 15,
            "max_concurrent_leases": 3,
            "api_key_ref": "env:FLOW_RUNNER_KEY",
        },
    )
    assert updated.status_code == 200, updated.text
    data = updated.json()
    assert data["description"] == "Updated"
    assert data["enabled"] is False
    assert data["runner_type"] == "push"
    assert data["status"] == "draining"
    assert data["lease_duration_seconds"] == 120
    assert data["heartbeat_interval_seconds"] == 15
    assert data["max_concurrent_leases"] == 3
    assert data["api_key_ref"] == "env:FLOW_RUNNER_KEY"

    deleted = client.delete(f"/api/runners/{runner['id']}")
    assert deleted.status_code == 200, deleted.text
    data = deleted.json()
    assert data["enabled"] is False
    assert data["status"] == "offline"


def test_runner_list_filters(client):
    first = create_runner(client, name="runner-a")
    create_runner(client, name="runner-b")
    client.patch(f"/api/runners/{first['id']}", json={"enabled": False, "status": "online"})

    enabled_only = client.get("/api/runners", params={"enabled_only": "true"})
    assert enabled_only.status_code == 200
    assert [item["name"] for item in enabled_only.json()["items"]] == ["runner-b"]

    online = client.get("/api/runners", params={"status": "online"})
    assert online.status_code == 200
    assert [item["name"] for item in online.json()["items"]] == ["runner-a"]


def test_runner_permissions_admin_manages_implementer_reads(client, no_auth_client):
    runner = create_runner(client)
    implementer_headers = create_role_headers(client, "implementer", "runner-impl")

    read_response = no_auth_client.get("/api/runners", headers=implementer_headers)
    assert read_response.status_code == 200

    get_response = no_auth_client.get(f"/api/runners/{runner['id']}", headers=implementer_headers)
    assert get_response.status_code == 200

    create_response = no_auth_client.post(
        "/api/runners",
        json={"name": "forbidden-runner"},
        headers=implementer_headers,
    )
    assert create_response.status_code == 403

    update_response = no_auth_client.patch(
        f"/api/runners/{runner['id']}",
        json={"status": "online"},
        headers=implementer_headers,
    )
    assert update_response.status_code == 403


def test_runner_name_uniqueness(client):
    create_runner(client, name="unique-runner")
    duplicate = client.post("/api/runners", json={"name": "unique-runner"})
    assert duplicate.status_code == 409


def test_runner_type_validation(client):
    response = client.post("/api/runners", json={"name": "bad-type", "runner_type": "sidecar"})
    assert response.status_code == 422

    runner = create_runner(client, name="valid-type")
    response = client.patch(f"/api/runners/{runner['id']}", json={"runner_type": "sidecar"})
    assert response.status_code == 422


def test_runner_status_validation(client):
    runner = create_runner(client, name="status-runner")
    response = client.patch(f"/api/runners/{runner['id']}", json={"status": "busy"})
    assert response.status_code == 422


def test_runner_lease_duration_minimum(client):
    response = client.post("/api/runners", json={"name": "short-lease", "lease_duration_seconds": 59})
    assert response.status_code == 422

    runner = create_runner(client, name="lease-runner")
    response = client.patch(f"/api/runners/{runner['id']}", json={"lease_duration_seconds": 59})
    assert response.status_code == 422
