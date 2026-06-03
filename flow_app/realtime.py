from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import timezone
import json
from threading import Lock
from typing import Any

from .models import Task, utcnow


@dataclass(frozen=True)
class BoardEvent:
    id: int
    event: str
    data: dict[str, Any]


class BoardEventHub:
    def __init__(self, max_events: int = 200) -> None:
        self._events: deque[BoardEvent] = deque(maxlen=max_events)
        self._lock = Lock()
        self._next_id = 1

    def publish(self, event: str, task: Task | None = None, **data: Any) -> BoardEvent:
        payload = {
            "event": event,
            "created_at": utcnow().astimezone(timezone.utc).isoformat(),
            **data,
        }
        if task is not None:
            payload.update(
                {
                    "task_id": task.id,
                    "project": task.project,
                    "status": task.status,
                    "assignee": task.assignee,
                    "human_required": bool(task.human_required),
                }
            )
        with self._lock:
            item = BoardEvent(self._next_id, event, payload)
            self._next_id += 1
            self._events.append(item)
            return item

    def since(self, event_id: int = 0, limit: int | None = None) -> list[BoardEvent]:
        with self._lock:
            items = [item for item in self._events if item.id > event_id]
        if limit is not None:
            return items[:limit]
        return items

    def latest_id(self) -> int:
        with self._lock:
            if not self._events:
                return 0
            return self._events[-1].id

    def clear(self) -> None:
        with self._lock:
            self._events.clear()
            self._next_id = 1


board_events = BoardEventHub()


def publish_board_event(event: str, task: Task | None = None, **data: Any) -> BoardEvent:
    return board_events.publish(event, task, **data)


def format_sse_event(item: BoardEvent) -> str:
    payload = json.dumps(item.data, separators=(",", ":"), default=str)
    return f"id: {item.id}\nevent: {item.event}\ndata: {payload}\n\n"
