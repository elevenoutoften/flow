from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from .config import get_settings
from .repository import create_audit_log
from .secrets_resolver import redact_secret

_SECRET_MARKERS = ("secret", "token", "key", "password", "credential", "authorization")


def audit(
    session: Session,
    actor_id: str,
    action: str,
    target_type: str = "",
    target_id: str = "",
    detail: dict[str, Any] | None = None,
) -> None:
    """Write an audit log entry with JSON details and redacted secret-like values."""
    if not get_settings().audit_enabled:
        return
    detail_json = json.dumps(_redact_detail(detail or {}), default=str, sort_keys=True)
    create_audit_log(
        session,
        actor_id=actor_id or "system",
        action=action,
        target_type=target_type,
        target_id=target_id,
        detail=detail_json,
    )


def actor_id_for(actor) -> str:
    return getattr(actor, "key_id", None) or getattr(actor, "name", None) or "system"


def _redact_detail(value: Any, parent_key: str = "") -> Any:
    if isinstance(value, dict):
        return {str(key): _redact_detail(item, str(key)) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_detail(item, parent_key) for item in value]
    if any(marker in parent_key.lower() for marker in _SECRET_MARKERS):
        return redact_secret("" if value is None else str(value))
    return value
