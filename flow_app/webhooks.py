from __future__ import annotations

import hashlib
import hmac
import json
from datetime import timedelta
from ipaddress import ip_address
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Task, WebhookConfig, WebhookDelivery, utcnow
from .repository import create_webhook_delivery, update_webhook_delivery
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

    request_url, host_header = _resolved_request_target(config.url, resolved_ip)
    payload_bytes = delivery.payload.encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Host": host_header,
        "X-Flow-Event": delivery.event,
        "X-Flow-Signature": sign_payload(config.secret, payload_bytes),
        "X-Flow-Delivery-ID": delivery.id,
    }

    try:
        response = httpx.post(request_url, content=payload_bytes, headers=headers, timeout=10.0)
    except httpx.HTTPError as exc:
        _record_failure(db, delivery, config, None, str(exc))
        return

    body = response.text[:2000]
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


def _resolved_request_target(url: str, resolved_ip: str) -> tuple[str, str]:
    parsed = urlparse(url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    ip = ip_address(resolved_ip)
    host = f"[{resolved_ip}]" if ip.version == 6 else resolved_ip
    netloc = f"{host}:{port}"
    path = parsed.path or "/"
    request_url = urlunparse((parsed.scheme, netloc, path, parsed.params, parsed.query, ""))
    return request_url, parsed.hostname or ""


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
        last_response_body=response_body[:2000],
    )


def _config_events(config: WebhookConfig) -> set[str]:
    return {event.strip() for event in config.events.split(",") if event.strip()}
