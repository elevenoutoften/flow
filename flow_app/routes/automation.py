from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from ..audit import actor_id_for, audit
from ..config import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT
from ..repository import (
    count_automation_rules,
    serialize_automation_rule,
)
from ..ratelimit import require_mutation_limit
from ..schemas import (
    AutomationDryRunRequest,
    AutomationEvent,
    AutomationRuleCreate,
    AutomationRuleResponse,
    AutomationRuleUpdate,
    PaginatedResponse,
)
from ..security import Actor, Permission, require_permission
from ..services.automation import AutomationRuleNotFoundError, AutomationService
from .dependencies import get_db


router = APIRouter()

@router.get("/automation-rules", response_model=PaginatedResponse)
def api_list_automation_rules(
        db: Session = Depends(get_db),
        enabled_only: bool = Query(default=False),
        limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
        offset: int = Query(default=0, ge=0),
        _actor: Actor = Depends(require_permission(Permission.RULES_READ)),
    ):
        svc = AutomationService(db)
        items = [serialize_automation_rule(rule) for rule in svc.list_rules(enabled_only=enabled_only, limit=limit, offset=offset)]
        total = count_automation_rules(db, enabled_only=enabled_only)
        return {"items": items, "total": total, "limit": limit, "offset": offset}

@router.post("/automation-rules/evaluate")
def api_evaluate_automation_rules(
        payload: AutomationEvent,
        db: Session = Depends(get_db),
        actor: Actor = Depends(require_permission(Permission.RULES_EVALUATE)),
    ):
        svc = AutomationService(db)
        matches = svc.evaluate_rules(payload, actor=actor)
        return {"matches": matches, "count": len(matches)}

@router.post("/automation-rules/dry-run")
def api_dry_run_automation_rules(
        payload: AutomationDryRunRequest,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.RULES_EVALUATE)),
    ):
        from ..runner import dry_run_automation_rules

        return dry_run_automation_rules(db, trigger=payload.trigger, rule_id=payload.rule_id, dry_run=True)

@router.get("/automation-rules/{rule_id}", response_model=AutomationRuleResponse)
def api_get_automation_rule(
        rule_id: str,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.RULES_READ)),
    ):
        svc = AutomationService(db)
        try:
            rule = svc.get_rule(rule_id)
        except AutomationRuleNotFoundError as exc:
            raise HTTPException(status_code=404, detail=exc.message) from exc
        return serialize_automation_rule(rule)

@router.post(
        "/automation-rules",
        response_model=AutomationRuleResponse,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(require_mutation_limit)],
    )
def api_create_automation_rule(
        payload: AutomationRuleCreate,
        db: Session = Depends(get_db),
        actor: Actor = Depends(require_permission(Permission.RULES_MANAGE)),
    ):
        svc = AutomationService(db)
        rule = svc.create_rule(payload)
        audit(db, actor_id_for(actor), "rule.create", "rule", rule.id)
        db.commit()
        return serialize_automation_rule(rule)

@router.patch(
        "/automation-rules/{rule_id}",
        response_model=AutomationRuleResponse,
        dependencies=[Depends(require_mutation_limit)],
    )
def api_update_automation_rule(
        rule_id: str,
        payload: AutomationRuleUpdate,
        db: Session = Depends(get_db),
        actor: Actor = Depends(require_permission(Permission.RULES_MANAGE)),
    ):
        svc = AutomationService(db)
        try:
            rule = svc.update_rule(rule_id, payload)
        except AutomationRuleNotFoundError as exc:
            raise HTTPException(status_code=404, detail=exc.message) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        audit(db, actor_id_for(actor), "rule.update", "rule", rule.id)
        db.commit()
        return serialize_automation_rule(rule)
