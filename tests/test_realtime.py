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


def test_board_events_since_filters_by_sequence(client):
    """Events returned by since() should only include events after the given id."""
    board_events.clear()
    client.post("/api/tasks", json={"title": "First", "project": "default"})
    id_after_first = board_events.since(0)[-1].id
    client.post("/api/tasks", json={"title": "Second", "project": "default"})

    later_events = board_events.since(id_after_first)
    assert len(later_events) >= 1
    assert later_events[-1].event == "task_created"


def test_board_events_ring_buffer_evicts_old_entries(client):
    """The ring buffer should evict old entries when capacity is exceeded."""
    board_events.clear()
    # Publish many events to fill and overflow the buffer (maxlen=200)
    for i in range(250):
        board_events.publish("task_updated", task_id=f"flow_test_{i}")
    # Old events should be gone — only recent ones remain
    all_events = board_events.since(0)
    assert len(all_events) <= 200  # bounded by buffer capacity
    # The most recent event should be present
    assert all_events[-1].data["task_id"] == "flow_test_249"
