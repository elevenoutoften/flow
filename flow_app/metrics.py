from __future__ import annotations

"""Lightweight metrics counters suitable for scraping or health diagnostics."""

import threading
from collections import defaultdict


class Metrics:
    """Thread-safe in-memory metrics counters and timers."""

    def __init__(self):
        self._counters = defaultdict(int)
        self._timers = defaultdict(list)
        self._lock = threading.Lock()

    def inc(self, name: str, value: int = 1):
        with self._lock:
            self._counters[name] += value

    def observe(self, name: str, seconds: float):
        with self._lock:
            self._timers[name].append(seconds)

    def snapshot(self) -> dict:
        with self._lock:
            counters = dict(self._counters)
            timers = {
                key: {"count": len(values), "sum": sum(values), "avg": sum(values) / len(values) if values else 0}
                for key, values in self._timers.items()
                if values
            }
            return {"counters": counters, "timers": timers}

    def reset(self):
        with self._lock:
            self._counters.clear()
            self._timers.clear()


metrics = Metrics()
