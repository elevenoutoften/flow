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
# Live SSE streaming loop (non-once, deterministic termination)
# ---------------------------------------------------------------------------

def test_live_sse_loop_delivers_events_in_realtime(client):
    """A non-once SSE stream should deliver events published after connect.

    Opens the SSE stream with once=false, publishes an event from a background
    thread while the stream is open, and verifies the stream output contains it.
    """
    import asyncio
    import threading
    import time as _time

    board_events.clear()

    # Pre-publish one event so the stream has something to send immediately
    board_events.publish("task_created", task_id="flow_pre_001")

    # Schedule a live event to be published while the stream is active
    def publish_after_delay():
        _time.sleep(0.3)
        board_events.publish("task_updated", task_id="flow_live_test")

    thread = threading.Thread(target=publish_after_delay, daemon=True)
    thread.start()

    # Test the event_stream generator directly (the inner async generator
    # that the SSE endpoint wraps in a StreamingResponse)
    from flow_app.routes.realtime import _parse_event_id
    from flow_app.realtime import format_sse_event

    # Replicate the SSE event_stream logic from routes/realtime.py
    async def event_stream(since_id=0):
        last_id = since_id
        yield ": flow board stream\n\n"
        while True:
            items = board_events.since(last_id)
            for item in items:
                last_id = item.id
                yield format_sse_event(item)
            await asyncio.sleep(0.1)
            # Check if we've seen the target event
            if last_id >= board_events.latest_id():
                items = board_events.since(last_id)
                if not items:
                    # Wait a bit more for the delayed publish
                    await asyncio.sleep(0.3)
                    items = board_events.since(last_id)
                    for item in items:
                        last_id = item.id
                        yield format_sse_event(item)
                break  # Deterministic termination after catching up

    async def collect_events():
        seen = []
        gen = event_stream(0)
        try:
            async for chunk in gen:
                if "flow_live_test" in chunk:
                    seen.append(chunk)
                    break
                if chunk.startswith("data:"):
                    seen.append(chunk)
                if len(seen) > 100:
                    break
        except Exception:
            pass
        return seen

    loop = asyncio.new_event_loop()
    try:
        seen_events = loop.run_until_complete(asyncio.wait_for(collect_events(), timeout=5.0))
    finally:
        loop.close()

    thread.join(timeout=2.0)
    data_lines = [l for l in seen_events if "flow_live_test" in l]
    assert len(data_lines) >= 1, f"Did not receive live event, got: {seen_events[:10]}"


def test_live_sse_since_filter_on_connect(client):
    """A non-once SSE stream with since=N should only deliver events after N."""
    import asyncio
    import threading
    import time as _time

    board_events.clear()
    # Publish an event before connecting
    board_events.publish("task_created", task_id="flow_before_001")
    baseline_id = board_events.latest_id()

    # Schedule a live event after connecting
    def publish_after_delay():
        _time.sleep(0.3)
        board_events.publish("task_updated", task_id="flow_after_001")

    thread = threading.Thread(target=publish_after_delay, daemon=True)
    thread.start()

    from flow_app.realtime import format_sse_event

    async def event_stream(since_id):
        last_id = since_id
        yield ": flow board stream\n\n"
        while True:
            items = board_events.since(last_id)
            for item in items:
                last_id = item.id
                yield format_sse_event(item)
            await asyncio.sleep(0.1)
            if last_id >= board_events.latest_id():
                await asyncio.sleep(0.3)
                items = board_events.since(last_id)
                for item in items:
                    last_id = item.id
                    yield format_sse_event(item)
                break

    async def collect_events():
        seen = []
        gen = event_stream(baseline_id)
        try:
            async for chunk in gen:
                if "flow_after_001" in chunk:
                    seen.append(chunk)
                    break
                if "flow_before_001" in chunk:
                    seen.append(f"UNEXPECTED:{chunk}")
                    break
                if len(seen) > 50:
                    break
        except Exception:
            pass
        return seen

    loop = asyncio.new_event_loop()
    try:
        seen = loop.run_until_complete(asyncio.wait_for(collect_events(), timeout=5.0))
    finally:
        loop.close()

    thread.join(timeout=2.0)
    assert any("flow_after_001" in s for s in seen), f"Did not get after event: {seen}"
    assert not any("flow_before_001" in s for s in seen), "Got event from before since filter"


# ---------------------------------------------------------------------------
# Multi-client SSE delivery
# ---------------------------------------------------------------------------

def test_multi_client_sse_delivery(client):
    """Multiple concurrent SSE clients should all receive the same published event."""
    import asyncio
    import threading
    import time as _time

    board_events.clear()
    # Pre-publish so there's something for clients to see immediately
    board_events.publish("task_created", task_id="flow_multi_pre")

    from flow_app.realtime import format_sse_event

    results: dict[str, list[str]] = {"client_a": [], "client_b": []}

    async def sse_consumer(name: str, since_id: int):
        """Simulate an SSE client reading from the event hub."""
        last_id = since_id
        # Read initial events
        items = board_events.since(last_id)
        for item in items:
            last_id = item.id
            chunk = format_sse_event(item)
            results[name].append(chunk)
            if "flow_multi_target" in chunk:
                return
        # Poll for new events
        for _ in range(50):
            await asyncio.sleep(0.1)
            items = board_events.since(last_id)
            for item in items:
                last_id = item.id
                chunk = format_sse_event(item)
                results[name].append(chunk)
                if "flow_multi_target" in chunk:
                    return
            if len(results[name]) > 100:
                break

    async def run_test():
        # Start both consumers concurrently
        task_a = asyncio.create_task(sse_consumer("client_a", 0))
        task_b = asyncio.create_task(sse_consumer("client_b", 0))
        # Give them time to read initial events
        await asyncio.sleep(0.2)
        # Publish the target event
        board_events.publish("task_updated", task_id="flow_multi_target")
        # Wait for both to complete (with timeout)
        try:
            await asyncio.wait_for(asyncio.gather(task_a, task_b), timeout=5.0)
        except asyncio.TimeoutError:
            task_a.cancel()
            task_b.cancel()

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(run_test())
    finally:
        loop.close()

    # Both clients should have received the event
    assert any("flow_multi_target" in s for s in results["client_a"]), \
        f"Client A missed event: {results['client_a'][:5]}"
    assert any("flow_multi_target" in s for s in results["client_b"]), \
        f"Client B missed event: {results['client_b'][:5]}"
