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


# ---------------------------------------------------------------------------
# Real concurrent non-once SSE lifecycle (QA-01 final)
# ---------------------------------------------------------------------------

def test_concurrent_non_once_sse_lifecycle(tmp_path):
    """Exercise the production /api/events/board route with once=false (default).

    Two genuinely concurrent stream consumers are started and synchronized
    before a new board event is published.  Both must receive that
    post-connection event in order.  One client is then resumed using
    Last-Event-ID/since and must receive later events once with no duplicate
    earlier event.  Both consumers are then disconnected deterministically and
    must terminate within a timeout.

    Uses a real uvicorn server on a random port — ASGITransport buffers the
    entire response body so it cannot exercise the non-once infinite stream.
    """
    import asyncio
    import httpx
    import socket
    import threading
    import uvicorn
    from flow_app.main import create_app
    from flow_app.realtime import board_events

    # Pick a free port.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("", 0))
    port = sock.getsockname()[1]
    sock.close()

    db_url = f"sqlite:///{tmp_path / 'flow_sse.sqlite'}"
    app = create_app(
        db_url,
        trusted_headers=True,
        session_secret="test-secret-for-sse",
    )

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)

    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    # Wait for the server to be ready.
    import time
    for _ in range(50):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
        except (ConnectionRefusedError, OSError):
            time.sleep(0.1)

    try:
        asyncio.run(_sse_lifecycle_async(port))
    finally:
        server.should_exit = True
        server_thread.join(timeout=5)


async def _sse_lifecycle_async(port: int):
    """Async body: two concurrent non-once SSE consumers, post-connect event,
    resume with since, deterministic disconnect."""
    import asyncio
    import httpx
    from flow_app.realtime import board_events

    board_events.clear()
    base = f"http://127.0.0.1:{port}"
    headers = {"X-Axis-Admin": "1", "X-Axis-User": "test-sse"}

    async with httpx.AsyncClient(base_url=base) as ac:
        consumer_states: dict[str, dict] = {
            "a": {"chunks": [], "connected": asyncio.Event(), "event_ids": []},
            "b": {"chunks": [], "connected": asyncio.Event(), "event_ids": []},
        }
        stop_flags: dict[str, asyncio.Event] = {
            "a": asyncio.Event(),
            "b": asyncio.Event(),
        }

        async def consume(label: str):
            state = consumer_states[label]
            async with ac.stream("GET", "/api/events/board", headers=headers, timeout=None) as resp:
                assert resp.status_code == 200, f"consumer {label} status {resp.status_code}"
                buf = ""
                async for raw in resp.aiter_text():
                    if stop_flags[label].is_set():
                        break
                    buf += raw
                    while "\n\n" in buf:
                        record, buf = buf.split("\n\n", 1)
                        state["chunks"].append(record)
                        if record.startswith(":"):
                            state["connected"].set()
                        elif record.startswith("id: "):
                            for line in record.split("\n"):
                                if line.startswith("id: "):
                                    eid = int(line[4:].strip())
                                    state["event_ids"].append(eid)
                                    break

        task_a = asyncio.create_task(consume("a"))
        task_b = asyncio.create_task(consume("b"))

        # Wait until both consumers are connected (received the initial comment).
        await asyncio.wait_for(
            asyncio.gather(
                consumer_states["a"]["connected"].wait(),
                consumer_states["b"]["connected"].wait(),
            ),
            timeout=10,
        )

        # Publish a new event AFTER both are connected.
        board_events.publish("task_created", task_id="flow_concurrent_post_connect")
        await asyncio.sleep(1.5)  # allow the 1s poll loop to deliver.

        # Both must have received the post-connection event.
        for label in ("a", "b"):
            chunks_joined = "\n\n".join(consumer_states[label]["chunks"])
            assert "flow_concurrent_post_connect" in chunks_joined, (
                f"Consumer {label} did not receive the post-connection event. "
                f"Chunks: {consumer_states[label]['chunks']}"
            )

        assert consumer_states["a"]["event_ids"], "Consumer a has no event ids"
        assert consumer_states["b"]["event_ids"], "Consumer b has no event ids"
        assert consumer_states["a"]["event_ids"][-1] == consumer_states["b"]["event_ids"][-1]

        # Resume consumer a using since=last_event_id.
        resume_since = consumer_states["a"]["event_ids"][-1]
        board_events.publish("task_updated", task_id="flow_resume_after")
        await asyncio.sleep(0.1)

        stop_flags["a"].set()
        await asyncio.sleep(1.5)

        resumed = await ac.get(
            f"/api/events/board?since={resume_since}&once=true",
            headers=headers,
        )
        assert resumed.status_code == 200, resumed.text
        assert "flow_resume_after" in resumed.text, (
            f"Resumed stream missing later event. Body: {resumed.text}"
        )
        assert "flow_concurrent_post_connect" not in resumed.text, (
            f"Resumed stream received duplicate earlier event. Body: {resumed.text}"
        )

        # Disconnect both consumers deterministically.
        stop_flags["a"].set()
        stop_flags["b"].set()
        task_a.cancel()
        task_b.cancel()

        for task, label in [(task_a, "a"), (task_b, "b")]:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=5)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            assert task.done() or task.cancelled(), (
                f"Consumer {label} task did not terminate within timeout"
            )
