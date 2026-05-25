"""Automation service layer: shared business logic for REST and MCP transports."""

from __future__ import annotations

from sqlalchemy.orm import Session

from flow_app.models import AutomationRule
from flow_app.repository import (
    create_automation_rule,
    evaluate_rules,
    get_automation_rule,
    list_automation_rules,
    update_automation_rule,
)
from flow_app.schemas import AutomationEvent, AutomationRuleCreate, AutomationRuleUpdate


class AutomationError(Exception):
    """Base exception for automation service errors."""

    def __init__(self, message: str, error_type: str = "automation_error"):
        self.message = message
        self.error_type = error_type
        super().__init__(message)


class AutomationRuleNotFoundError(AutomationError):
    def __init__(self, rule_id: str):
        super().__init__(f"Automation rule not found: {rule_id}", "not_found")


class AutomationService:
    """Shared automation service operations."""

    def __init__(self, db: Session):
        self.db = db

    def list_rules(
        self,
        enabled_only: bool = False,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[AutomationRule]:
        return list_automation_rules(self.db, enabled_only=enabled_only, limit=limit, offset=offset)

    def get_rule(self, rule_id: str) -> AutomationRule:
        return self._require_rule(rule_id)

    def create_rule(self, payload: AutomationRuleCreate) -> AutomationRule:
        rule = create_automation_rule(self.db, payload)
        self._commit()
        return rule

    def update_rule(self, rule_id: str, payload: AutomationRuleUpdate) -> AutomationRule:
        rule = update_automation_rule(self.db, self._require_rule(rule_id), payload)
        self._commit()
        return rule

    def evaluate_rules(self, payload: AutomationEvent, actor=None) -> list[dict]:
        matches = evaluate_rules(self.db, payload, actor=actor)
        self._commit()
        return matches

    def _require_rule(self, rule_id: str) -> AutomationRule:
        rule = get_automation_rule(self.db, rule_id)
        if rule is None:
            raise AutomationRuleNotFoundError(rule_id)
        return rule

    def _commit(self) -> None:
        self.db.commit()
