from __future__ import annotations

"""Configurable per-IP and per-key rate limiting for sensitive endpoints."""

import threading
import time
from collections import defaultdict

from fastapi import HTTPException, Request

from .config import get_settings


class RateLimiter:
    """Token-bucket style rate limiter over a fixed rolling window."""

    def __init__(self, max_requests: int = 60, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests = defaultdict(list)
        self._lock = threading.Lock()

    def check(self, key: str, max_requests: int | None = None):
        if not get_settings().rate_limit_enabled:
            return
        now = time.time()
        limit = max_requests or self.max_requests
        with self._lock:
            self._requests[key] = [timestamp for timestamp in self._requests[key] if now - timestamp < self.window_seconds]
            if len(self._requests[key]) >= limit:
                raise HTTPException(status_code=429, detail="Rate limit exceeded. Try again later.")
            self._requests[key].append(now)

    def reset(self):
        with self._lock:
            self._requests.clear()


def client_ip(request: Request) -> str:
    if request.client is None:
        return "unknown"
    return request.client.host


key_creation_limiter = RateLimiter(max_requests=10, window_seconds=60)
auth_limiter = RateLimiter(max_requests=20, window_seconds=60)
mutation_limiter = RateLimiter(max_requests=120, window_seconds=60)
