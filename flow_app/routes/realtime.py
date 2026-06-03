from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse

from ..realtime import board_events, format_sse_event
from ..security import Actor, Permission, require_permission


router = APIRouter()


@router.get("/events/board")
async def stream_board_events(
    request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    since: int | None = Query(default=None, ge=0),
    once: bool = Query(default=False),
    _actor: Actor = Depends(require_permission(Permission.TASKS_READ)),
):
    start_id = since if since is not None else _parse_event_id(last_event_id)

    async def event_stream():
        last_id = start_id
        yield ": flow board stream\n\n"
        while True:
            items = board_events.since(last_id)
            for item in items:
                last_id = item.id
                yield format_sse_event(item)
            if once or await request.is_disconnected():
                break
            await asyncio.sleep(1.0)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _parse_event_id(value: str | None) -> int:
    try:
        return max(0, int(value or "0"))
    except ValueError:
        return 0
