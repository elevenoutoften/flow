from flow_app.realtime import board_events


def test_task_mutations_publish_board_events(client):
    board_events.clear()

    created = client.post("/api/tasks", json={"title": "Realtime task", "project": "default"}).json()
    assert board_events.since(0)[-1].event == "task_created"

    moved = client.post(f"/api/tasks/{created['id']}/move", json={"status": "doing"})
    assert moved.status_code == 200, moved.text
    event = board_events.since(0)[-1]
    assert event.event == "task_moved"
    assert event.data["task_id"] == created["id"]
    assert event.data["status"] == "doing"

    patched = client.patch(f"/api/tasks/{created['id']}", json={"title": "Realtime task updated"})
    assert patched.status_code == 200, patched.text
    assert board_events.since(0)[-1].event == "task_updated"


def test_board_events_stream_exposes_recent_events(client):
    board_events.clear()
    task = client.post("/api/tasks", json={"title": "Stream me", "project": "default"}).json()

    response = client.get("/api/events/board?since=0&once=true")

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: task_created" in response.text
    assert f'"task_id":"{task["id"]}"' in response.text
