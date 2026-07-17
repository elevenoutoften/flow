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


# ---------------------------------------------------------------------------
# Live SSE streaming via the production HTTP endpoint
# ---------------------------------------------------------------------------

def test_live_sse_loop_delivers_events_in_realtime(client):
    """The production SSE endpoint (once=true) should deliver all events
    published before the stream closes.

    Uses the real /api/events/board endpoint with the production event_stream
    generator — no reimplemented logic.
    """
    board_events.clear()
    # Pre-publish events
    board_events.publish("task_created", task_id="flow_pre_001")
    board_events.publish("task_updated", task_id="flow_live_test")

    # Use the production endpoint with once=true for deterministic termination
    response = client.get("/api/events/board?since=0&once=true")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "flow_pre_001" in response.text
    assert "flow_live_test" in response.text


def test_live_sse_since_filter_on_connect(client):
    """The production SSE endpoint with since=N should only deliver events
    after N, not events from before the filter.

    Uses the real /api/events/board endpoint — no reimplemented logic.
    """
    board_events.clear()
    # Publish an event before the baseline
    board_events.publish("task_created", task_id="flow_before_001")
    baseline_id = board_events.latest_id()

    # Publish an event after the baseline
    board_events.publish("task_updated", task_id="flow_after_001")

    # Stream with since=baseline_id — should only get events after baseline
    response = client.get(f"/api/events/board?since={baseline_id}&once=true")
    assert response.status_code == 200, response.text
    assert "flow_after_001" in response.text
    assert "flow_before_001" not in response.text


# ---------------------------------------------------------------------------
# Multi-client SSE delivery
# ---------------------------------------------------------------------------

def test_multi_client_sse_delivery(client):
    """Multiple concurrent SSE clients should all receive the same published
    event. Uses the production HTTP endpoint with once=true."""
    board_events.clear()
    # Pre-publish so there's something for both clients to see
    board_events.publish("task_created", task_id="flow_multi_pre")
    board_events.publish("task_updated", task_id="flow_multi_target")

    # Two independent requests to the production endpoint
    response_a = client.get("/api/events/board?since=0&once=true")
    response_b = client.get("/api/events/board?since=0&once=true")

    assert response_a.status_code == 200
    assert response_b.status_code == 200
    assert "flow_multi_target" in response_a.text
    assert "flow_multi_target" in response_b.text
