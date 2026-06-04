"""Recurring task template service layer for REST and MCP transports."""

from __future__ import annotations

from sqlalchemy.orm import Session

from flow_app.models import RecurringTaskTemplate
from flow_app.repository import (
    count_recurring_task_templates,
    create_recurring_task_template,
    get_recurring_task_template,
    list_recurring_task_templates,
    serialize_recurring_task_template,
    update_recurring_task_template,
)
from flow_app.schemas import PaginatedResponse, RecurringTaskTemplateCreate, RecurringTaskTemplateUpdate


class RecurringTemplateError(Exception):
    """Base exception for recurring task template service errors."""

    def __init__(self, message: str, error_type: str = "recurring_template_error"):
        self.message = message
        self.error_type = error_type
        super().__init__(message)


class RecurringTemplateNotFoundError(RecurringTemplateError):
    def __init__(self, template_id: str):
        super().__init__(f"Recurring task template not found: {template_id}", "not_found")


class RecurringTemplateService:
    def __init__(self, db: Session):
        self.db = db

    def create(self, payload: RecurringTaskTemplateCreate) -> dict:
        template = create_recurring_task_template(self.db, payload)
        self._commit()
        return serialize_recurring_task_template(template).model_dump(mode="json")

    def get(self, template_id: str) -> dict:
        template = self._require_template(template_id)
        return serialize_recurring_task_template(template).model_dump(mode="json")

    def list(
        self,
        project: str | None = None,
        enabled_only: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> dict:
        templates = list_recurring_task_templates(
            self.db,
            project=project,
            enabled_only=enabled_only,
            limit=limit,
            offset=offset,
        )
        total = count_recurring_task_templates(self.db, project=project, enabled_only=enabled_only)
        return PaginatedResponse(
            items=[serialize_recurring_task_template(template).model_dump(mode="json") for template in templates],
            total=total,
            limit=limit,
            offset=offset,
        ).model_dump(mode="json")

    def update(self, template_id: str, payload: RecurringTaskTemplateUpdate) -> dict:
        template = self._require_template(template_id)
        updated = update_recurring_task_template(self.db, template, payload)
        self._commit()
        return serialize_recurring_task_template(updated).model_dump(mode="json")

    def _require_template(self, template_id: str) -> RecurringTaskTemplate:
        try:
            return get_recurring_task_template(self.db, template_id)
        except ValueError as exc:
            raise RecurringTemplateNotFoundError(template_id) from exc

    def _commit(self) -> None:
        self.db.commit()
