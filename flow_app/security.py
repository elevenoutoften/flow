from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from fastapi import Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from .config import get_settings
from .models import ApiKeyRole, Task
from .repository import hash_api_key, lookup_api_key_by_hash


@dataclass(frozen=True)
class Actor:
    """Resolved identity making the request."""

    name: str
    role: ApiKeyRole
    source: str
    key_id: str | None = None


class Permission(Enum):
    TASKS_READ = "tasks:read"
    TASKS_CREATE = "tasks:create"
    TASKS_EDIT = "tasks:edit"
    TASKS_CLAIM = "tasks:claim"
    TASKS_MOVE = "tasks:move"
    TASKS_DONE = "tasks:done"
    TASKS_SET_HUMAN_REQUIRED = "tasks:set_human_required"
    KEY_MANAGE = "key:manage"
    BOARD_VIEW = "board:view"
    AGENT_READ = "agent:read"
    AGENT_MANAGE = "agent:manage"
    DISPATCH = "dispatch"
    RULES_READ = "rules:read"
    RULES_MANAGE = "rules:manage"
    RULES_EVALUATE = "rules:evaluate"
    LINKS_READ = "links:read"
    LINKS_MANAGE = "links:manage"
    WORKSPACE_READ = "workspace:read"
    WORKSPACE_MANAGE = "workspace:manage"
    WEBHOOK_MANAGE = "webhook:manage"
    WEBHOOK_READ = "webhook:read"
    HANDOFF_READ = "handoff:read"
    HANDOFF_CREATE = "handoff:create"
    HANDOFF_MANAGE = "handoff:manage"


ROLE_PERMISSIONS: dict[ApiKeyRole, set[Permission]] = {
    ApiKeyRole.admin: {
        Permission.TASKS_READ,
        Permission.TASKS_CREATE,
        Permission.TASKS_EDIT,
        Permission.TASKS_CLAIM,
        Permission.TASKS_MOVE,
        Permission.TASKS_DONE,
        Permission.TASKS_SET_HUMAN_REQUIRED,
        Permission.KEY_MANAGE,
        Permission.BOARD_VIEW,
        Permission.AGENT_READ,
        Permission.AGENT_MANAGE,
        Permission.DISPATCH,
        Permission.RULES_READ,
        Permission.RULES_MANAGE,
        Permission.RULES_EVALUATE,
        Permission.LINKS_READ,
        Permission.LINKS_MANAGE,
        Permission.WORKSPACE_READ,
        Permission.WORKSPACE_MANAGE,
        Permission.WEBHOOK_MANAGE,
        Permission.WEBHOOK_READ,
        Permission.HANDOFF_READ,
        Permission.HANDOFF_CREATE,
        Permission.HANDOFF_MANAGE,
    },
    ApiKeyRole.architect: {
        Permission.TASKS_READ,
        Permission.TASKS_CREATE,
        Permission.TASKS_EDIT,
        Permission.TASKS_CLAIM,
        Permission.TASKS_MOVE,
        Permission.TASKS_DONE,
        Permission.TASKS_SET_HUMAN_REQUIRED,
        Permission.BOARD_VIEW,
        Permission.AGENT_READ,
        Permission.AGENT_MANAGE,
        Permission.DISPATCH,
        Permission.RULES_READ,
        Permission.RULES_MANAGE,
        Permission.RULES_EVALUATE,
        Permission.LINKS_READ,
        Permission.LINKS_MANAGE,
        Permission.WORKSPACE_READ,
        Permission.WORKSPACE_MANAGE,
        Permission.WEBHOOK_MANAGE,
        Permission.WEBHOOK_READ,
        Permission.HANDOFF_READ,
        Permission.HANDOFF_CREATE,
        Permission.HANDOFF_MANAGE,
    },
    ApiKeyRole.implementer: {
        Permission.TASKS_READ,
        Permission.TASKS_CLAIM,
        Permission.TASKS_MOVE,
        Permission.BOARD_VIEW,
        Permission.AGENT_READ,
        Permission.DISPATCH,
        Permission.RULES_READ,
        Permission.LINKS_READ,
        Permission.WORKSPACE_READ,
        Permission.WEBHOOK_READ,
        Permission.HANDOFF_READ,
        Permission.HANDOFF_CREATE,
    },
    ApiKeyRole.reviewer: {
        Permission.TASKS_READ,
        Permission.TASKS_CLAIM,
        Permission.TASKS_MOVE,
        Permission.TASKS_DONE,
        Permission.BOARD_VIEW,
        Permission.AGENT_READ,
        Permission.RULES_READ,
        Permission.LINKS_READ,
        Permission.WORKSPACE_READ,
        Permission.WEBHOOK_READ,
        Permission.HANDOFF_READ,
        Permission.HANDOFF_CREATE,
    },
    ApiKeyRole.read_only: {
        Permission.TASKS_READ,
        Permission.BOARD_VIEW,
        Permission.AGENT_READ,
        Permission.RULES_READ,
        Permission.LINKS_READ,
        Permission.WORKSPACE_READ,
        Permission.WEBHOOK_READ,
        Permission.HANDOFF_READ,
    },
}


def verify_bearer_token(db: Session, token: str) -> Actor | None:
    api_key = lookup_api_key_by_hash(db, hash_api_key(token))
    if api_key is None or api_key.revoked_at is not None:
        return None

    try:
        role = ApiKeyRole(api_key.role)
    except ValueError:
        role = ApiKeyRole.read_only

    return Actor(name=api_key.name, role=role, source="bearer_key", key_id=api_key.id)


def resolve_actor(
    db: Session,
    authorization: str | None,
    x_axis_admin: str | None,
    x_axis_user: str | None,
    x_axis_agent: str | None,
    trusted_headers: bool | None = None,
) -> Actor | None:
    token = _extract_bearer_token(authorization)
    if token:
        return verify_bearer_token(db, token)

    # Trusted headers are only accepted when explicitly enabled via config.
    # Deployments behind a reverse proxy that strips inbound identity headers
    # should set FLOW_TRUSTED_HEADERS=true.
    if trusted_headers is None:
        trusted_headers = get_settings().trusted_headers
    if not trusted_headers:
        return None

    if x_axis_admin == "1":
        return Actor(
            name=_first_present(x_axis_user, x_axis_agent) or "admin",
            role=ApiKeyRole.admin,
            source="admin_header",
        )

    browser_actor = _first_present(x_axis_user, x_axis_agent)
    if browser_actor:
        return Actor(name=browser_actor, role=ApiKeyRole.read_only, source="browser")

    return None


def has_permission(actor: Actor, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS.get(actor.role, set())


def is_valid_transition(actor: Actor, current_status: str, target_status: str) -> bool:
    if actor.role in {ApiKeyRole.admin, ApiKeyRole.architect}:
        return True

    if "backlog" in {current_status, target_status}:
        return False
    if current_status == "done":
        return False

    allowed_transitions: dict[ApiKeyRole, set[tuple[str, str]]] = {
        ApiKeyRole.implementer: {
            ("todo", "doing"),
            ("doing", "review"),
            ("review", "doing"),
        },
        ApiKeyRole.reviewer: {
            ("todo", "doing"),
            ("review", "done"),
            ("review", "todo"),
            ("review", "doing"),
        },
        ApiKeyRole.read_only: set(),
    }
    return (current_status, target_status) in allowed_transitions.get(actor.role, set())


def require_permission(permission: Permission):
    def dependency(
        request: Request,
        authorization: str | None = Header(default=None),
        # Header aliases can be changed here for deployments that do not use X-Axis-*.
        x_axis_admin: str | None = Header(default=None),
        x_axis_user: str | None = Header(default=None),
        x_axis_agent: str | None = Header(default=None),
    ) -> Actor:
        session_factory = request.app.state.SessionLocal
        with session_factory() as db:
            actor = resolve_actor(
                db,
                authorization,
                x_axis_admin,
                x_axis_user,
                x_axis_agent,
                trusted_headers=request.app.state.settings.trusted_headers,
            )

        if actor is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication is required.")
        if not has_permission(actor, permission):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permission.")
        return actor

    return dependency


def _extract_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, separator, token = authorization.strip().partition(" ")
    if not separator or scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def _first_present(*candidates: str | None) -> str | None:
    for candidate in candidates:
        if candidate and candidate.strip():
            return candidate.strip()
    return None


HUMAN_REQUIRED_TASK_FIELDS = {"human_required", "blocker_reason"}


class PermissionDenied(Exception):
    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


def authorize_task_update(actor: Actor | None, task: Task, payload) -> None:
    if actor is None:
        raise PermissionDenied("Authentication is required.")

    fields = set(payload.model_dump(exclude_unset=True))
    if not fields:
        return

    human_required_fields = fields & HUMAN_REQUIRED_TASK_FIELDS
    regular_fields = fields - HUMAN_REQUIRED_TASK_FIELDS

    if regular_fields and not has_permission(actor, Permission.TASKS_EDIT):
        raise PermissionDenied("Insufficient permission.")

    if not human_required_fields or has_permission(actor, Permission.TASKS_SET_HUMAN_REQUIRED):
        return

    if payload.human_required is not True:
        raise PermissionDenied("Insufficient permission.")

    if actor.role == ApiKeyRole.implementer and task.assignee == actor.name:
        return

    if actor.role == ApiKeyRole.reviewer and task.status == "review":
        return

    raise PermissionDenied("Insufficient permission.")
