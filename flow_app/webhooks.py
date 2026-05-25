from __future__ import annotations

import hashlib
import hmac
import json
from datetime import timedelta
from uuid import uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Task, WebhookConfig, WebhookDelivery, utcnow
from .repository import create_webhook_delivery, get_webhook_secret, update_webhook_delivery
from .ssrf import resolve_webhook_target

WEBHOOK_EVENTS = [
    "task_created",
    "task_moved",
    "task_claimed",
    "task_completed",
    "task_blocked",
    "idea_promoted",
]


def emit_event(db: Session, event_name: str, task: Task, changes: dict | None = None) -> None:
    if event_name not in WEBHOOK_EVENTS:
        return

    configs = list(
        db.scalars(
            select(WebhookConfig)
            .where(WebhookConfig.active == 1)
            .where(WebhookConfig.project.in_([task.project, "*"]))
        ).all()
    )
    if not configs:
        return

    payload = {
        "event": event_name,
        "event_id": str(uuid4()),
        "timestamp": utcnow().isoformat(),
        "project": task.project,
        "task_id": task.id,
        "task_title": task.title,
        "changes": changes or {},
    }
    payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    for config in configs:
        if event_name in _config_events(config):
            create_webhook_delivery(db, config.id, event_name, payload_json)


def deliver_webhook(db: Session, delivery: WebhookDelivery, config: WebhookConfig) -> None:
    try:
        resolved_ip, _ = resolve_webhook_target(config.url)
    except ValueError:
        _record_failure(db, delivery, config, None, "Webhook URL targets unacceptable address.")
        return

    payload_bytes = delivery.payload.encode("utf-8")
    secret = get_webhook_secret(config)
    if secret is None:
        _record_failure(db, delivery, config, None, "Webhook secret could not be decrypted.")
        return
    headers = {
        "Content-Type": "application/json",
        "X-Flow-Event": delivery.event,
        "X-Flow-Signature": sign_payload(secret, payload_bytes),
        "X-Flow-Delivery-ID": delivery.id,
    }

    try:
        transport = _PinnedDNSTransport(resolved_ip)
        with httpx.Client(transport=transport) as client:
            response = client.post(config.url, content=payload_bytes, headers=headers, timeout=10.0)
    except httpx.HTTPError as exc:
        _record_failure(db, delivery, config, None, str(exc))
        return

    body = response.text
    if 200 <= response.status_code < 300:
        update_webhook_delivery(
            db,
            delivery,
            status="success",
            next_attempt_at=None,
            last_response_code=response.status_code,
            last_response_body=body,
        )
        return

    _record_failure(db, delivery, config, response.status_code, body)


def sign_payload(secret: str, payload_bytes: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()


class _PinnedDNSTransport(httpx.BaseTransport):
    """Connect to a pre-validated IP while preserving the original hostname.

    The request URL is rewritten only inside the transport so the TCP connection
    targets the resolved IP. The HTTP Host header and HTTPS SNI hostname remain
    the user-provided hostname, preserving normal certificate verification.
    """

    def __init__(self, pinned_ip: str):
        self._pinned_ip = pinned_ip
        self._inner = httpx.HTTPTransport()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        original_host = request.url.host
        if not original_host:
            raise httpx.TransportError("Webhook URL must have a hostname.")

        pinned_request = httpx.Request(
            method=request.method,
            url=request.url.copy_with(host=self._pinned_ip),
            headers=request.headers,
            stream=request.stream,
            extensions=dict(request.extensions),
        )
        pinned_request.headers["Host"] = _host_header_value(request.url)
        if request.url.scheme == "https":
            pinned_request.extensions["sni_hostname"] = original_host

        return self._inner.handle_request(pinned_request)

    def close(self) -> None:
        self._inner.close()


def _host_header_value(url: httpx.URL) -> str:
    host = url.host
    if not host:
        return ""

    default_port = 443 if url.scheme == "https" else 80
    if url.port is not None and url.port != default_port:
        return f"{host}:{url.port}"
    return host


def _record_failure(
    db: Session,
    delivery: WebhookDelivery,
    config: WebhookConfig,
    status_code: int | None,
    response_body: str,
) -> None:
    attempts = delivery.attempts + 1
    status = "retrying" if attempts < config.max_retries else "failed"
    next_attempt_at = None
    if status == "retrying":
        delay = config.retry_backoff_seconds * (2 ** (attempts - 1))
        next_attempt_at = utcnow() + timedelta(seconds=delay)

    update_webhook_delivery(
        db,
        delivery,
        status=status,
        attempts=attempts,
        next_attempt_at=next_attempt_at,
        last_response_code=status_code,
        last_response_body=response_body,
    )


def _config_events(config: WebhookConfig) -> set[str]:
    return {event.strip() for event in config.events.split(",") if event.strip()}
