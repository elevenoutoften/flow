"""Webhook service layer: shared business logic for REST and MCP transports."""

from __future__ import annotations

from sqlalchemy.orm import Session

from flow_app.models import WebhookConfig, WebhookDelivery
from flow_app.repository import (
    create_webhook_config,
    delete_webhook_config,
    get_webhook_config,
    get_webhook_delivery,
    list_webhook_configs,
    list_webhook_deliveries,
    update_webhook_config,
)
from flow_app.schemas import WebhookConfigCreate, WebhookConfigUpdate
from flow_app.webhooks import WEBHOOK_EVENTS


class WebhookError(Exception):
    """Base exception for webhook service errors."""

    def __init__(self, message: str, error_type: str = "webhook_error"):
        self.message = message
        self.error_type = error_type
        super().__init__(message)


class WebhookNotFoundError(WebhookError):
    def __init__(self, webhook_id: str):
        super().__init__(f"Webhook not found: {webhook_id}", "not_found")


class WebhookService:
    """Shared webhook service operations."""

    def __init__(self, db: Session):
        self.db = db

    def list_configs(self, project: str | None = None) -> list[WebhookConfig]:
        return list_webhook_configs(self.db, project=project)

    def get_config(self, config_id: str) -> WebhookConfig:
        return self._require_config(config_id)

    def create_config(self, payload: WebhookConfigCreate) -> tuple[WebhookConfig, str]:
        self._validate_events(payload.events)
        config, raw_secret = create_webhook_config(
            self.db,
            payload.name,
            payload.url,
            payload.events,
            payload.project,
            payload.max_retries,
            payload.retry_backoff_seconds,
        )
        self._commit()
        return config, raw_secret

    def update_config(self, config_id: str, payload: WebhookConfigUpdate) -> WebhookConfig:
        config = self._require_config(config_id)
        updates = {key: value for key, value in payload.model_dump(exclude_unset=True).items() if value is not None}
        if "events" in updates:
            self._validate_events(updates["events"])
        config = update_webhook_config(self.db, config, updates)
        self._commit()
        return config

    def delete_config(self, config_id: str) -> None:
        delete_webhook_config(self.db, self._require_config(config_id))
        self._commit()

    def list_deliveries(
        self,
        webhook_id: str,
        limit: int | None = None,
        offset: int = 0,
        status: str | None = None,
    ) -> list[WebhookDelivery]:
        self._require_config(webhook_id)
        return list_webhook_deliveries(self.db, webhook_id, status=status, limit=limit, offset=offset)

    def get_delivery(self, delivery_id: str) -> WebhookDelivery | None:
        return get_webhook_delivery(self.db, delivery_id)

    def _require_config(self, config_id: str) -> WebhookConfig:
        config = get_webhook_config(self.db, config_id)
        if config is None:
            raise WebhookNotFoundError(config_id)
        return config

    def _validate_events(self, events: list[str]) -> None:
        invalid = [event for event in events if event not in WEBHOOK_EVENTS]
        if invalid:
            raise WebhookError(f"Invalid webhook event: {invalid[0]}", "invalid_event")

    def _commit(self) -> None:
        self.db.commit()
