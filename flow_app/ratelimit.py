from __future__ import annotations

"""Configurable per-IP and per-key rate limiting for sensitive endpoints."""

import threading
import time
from collections import defaultdict

from fastapi import Cookie, Header, HTTPException, Request

from .config import get_settings
from .security import SESSION_COOKIE_NAME, resolve_actor


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


async def require_mutation_limit(
    request: Request,
    authorization: str | None = Header(default=None),
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    x_axis_admin: str | None = Header(default=None),
    x_axis_user: str | None = Header(default=None),
    x_axis_agent: str | None = Header(default=None),
) -> None:
    settings = request.app.state.settings
    session_factory = request.app.state.SessionLocal
    with session_factory() as db:
        actor = resolve_actor(
            db,
            authorization,
            x_axis_admin,
            x_axis_user,
            x_axis_agent,
            trusted_headers=settings.trusted_headers,
            session_cookie=session_cookie,
            session_secret=settings.session_secret or None,
        )
    key = actor.key_id if actor and actor.key_id else client_ip(request)
    mutation_limiter.check(key, settings.rate_limit_mutations)


key_creation_limiter = RateLimiter(max_requests=10, window_seconds=60)
auth_limiter = RateLimiter(max_requests=20, window_seconds=60)
mutation_limiter = RateLimiter(max_requests=120, window_seconds=60)
