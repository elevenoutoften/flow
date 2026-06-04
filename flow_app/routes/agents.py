from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from ..config import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT
from ..dispatcher import DispatchError
from ..repository import (
    count_agent_runs,
    create_agent_api_key,
    get_agent_by_name,
    get_agent_api_key,
    list_agent_api_keys,
    revoke_agent_api_key,
    serialize_agent,
    serialize_agent_api_key,
    serialize_agent_run,
    serialize_created_agent_api_key,
)
from ..schemas import (
    AgentApiKeyCreate,
    AgentApiKeyCreateResponse,
    AgentApiKeyResponse,
    AgentCreate,
    AgentResponse,
    AgentRunResponse,
    AgentUpdate,
    PaginatedResponse,
)
from ..security import Actor, Permission, require_permission
from ..services.agent import AgentError, AgentNotFoundError, AgentRunNotFoundError, AgentService
from .dependencies import _commit, get_db


router = APIRouter()

@router.get("/api-keys", response_model=list[AgentApiKeyResponse])
def api_list_agent_api_keys(
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.KEY_MANAGE)),
    ):
        return [serialize_agent_api_key(api_key) for api_key in list_agent_api_keys(db)]

@router.post("/api-keys", response_model=AgentApiKeyCreateResponse, status_code=status.HTTP_201_CREATED)
def api_create_agent_api_key(
        payload: AgentApiKeyCreate,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.KEY_MANAGE)),
    ):
        api_key, raw_key = create_agent_api_key(db, payload)
        _commit(db)
        return serialize_created_agent_api_key(api_key, raw_key)

@router.post("/api-keys/{api_key_id}/revoke", response_model=AgentApiKeyResponse)
def api_revoke_agent_api_key(
        api_key_id: str,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.KEY_MANAGE)),
    ):
        api_key = get_agent_api_key(db, api_key_id)
        if api_key is None:
            raise HTTPException(status_code=404, detail="API key not found.")
        api_key = revoke_agent_api_key(db, api_key)
        _commit(db)
        return serialize_agent_api_key(api_key)

@router.get("/agents", response_model=list[AgentResponse])
def api_list_agents(
        db: Session = Depends(get_db),
        enabled_only: bool = Query(default=False),
        _actor: Actor = Depends(require_permission(Permission.AGENT_READ)),
    ):
        svc = AgentService(db)
        return [serialize_agent(agent) for agent in svc.list_agents(enabled_only=enabled_only)]

@router.get("/adapter-templates", response_model=list[dict])
def api_list_adapter_templates(
        _actor: Actor = Depends(require_permission(Permission.AGENT_READ)),
    ):
        from ..adapter_templates import BUILTIN_TEMPLATES

        return [template.model_dump() for template in BUILTIN_TEMPLATES]

@router.get("/adapter-templates/{template_name}", response_model=dict)
def api_get_adapter_template(
        template_name: str,
        _actor: Actor = Depends(require_permission(Permission.AGENT_READ)),
    ):
        from ..adapter_templates import get_template

        template = get_template(template_name)
        if template is None:
            raise HTTPException(status_code=404, detail=f"Adapter template '{template_name}' not found.")
        return template.model_dump()

@router.post("/adapter-templates/{template_name}/import", response_model=AgentResponse, status_code=status.HTTP_201_CREATED)
def api_import_adapter_template(
        template_name: str,
        overrides: AgentCreate | None = None,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.AGENT_MANAGE)),
    ):
        from ..adapter_templates import agent_create_from_template, get_template, validate_template

        template = get_template(template_name)
        if template is None:
            raise HTTPException(status_code=404, detail=f"Adapter template '{template_name}' not found.")
        validate_template(template)
        payload = agent_create_from_template(template, overrides)
        existing = get_agent_by_name(db, payload.name)
        if existing is not None:
            raise HTTPException(
                status_code=409,
                detail=f"Agent with name '{payload.name}' already exists (id={existing.id}).",
            )
        svc = AgentService(db)
        agent = svc.create_agent(payload)
        return serialize_agent(agent)

@router.get("/agents/{agent_id}", response_model=AgentResponse)
def api_get_agent(
        agent_id: str,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.AGENT_READ)),
    ):
        svc = AgentService(db)
        try:
            agent = svc.get_agent(agent_id)
        except AgentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=exc.message) from exc
        return serialize_agent(agent)

@router.post("/agents", response_model=AgentResponse, status_code=status.HTTP_201_CREATED)
def api_create_agent(
        payload: AgentCreate,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.AGENT_MANAGE)),
    ):
        svc = AgentService(db)
        agent = svc.create_agent(payload)
        return serialize_agent(agent)

@router.patch("/agents/{agent_id}", response_model=AgentResponse)
def api_update_agent(
        agent_id: str,
        payload: AgentUpdate,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.AGENT_MANAGE)),
    ):
        svc = AgentService(db)
        try:
            agent = svc.update_agent(agent_id, payload)
        except AgentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=exc.message) from exc
        return serialize_agent(agent)

@router.get("/agent-runs", response_model=PaginatedResponse)
def api_list_agent_runs(
        db: Session = Depends(get_db),
        agent_id: str | None = Query(default=None),
        task_id: str | None = Query(default=None),
        status_filter: str | None = Query(default=None, alias="status"),
        limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
        offset: int = Query(default=0, ge=0),
        _actor: Actor = Depends(require_permission(Permission.TASKS_READ)),
    ):
        svc = AgentService(db)
        items = [
            serialize_agent_run(run)
            for run in svc.list_runs(agent_id=agent_id, task_id=task_id, status=status_filter, limit=limit, offset=offset)
        ]
        total = count_agent_runs(db, agent_id=agent_id, task_id=task_id, status=status_filter)
        return {"items": items, "total": total, "limit": limit, "offset": offset}

@router.get("/agent-runs/{run_id}", response_model=AgentRunResponse)
def api_get_agent_run(
        run_id: str,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.TASKS_READ)),
    ):
        svc = AgentService(db)
        try:
            run = svc.get_run(run_id)
        except AgentRunNotFoundError as exc:
            raise HTTPException(status_code=404, detail=exc.message) from exc
        return serialize_agent_run(run)

@router.post("/agents/{agent_id}/dispatch", response_model=AgentRunResponse)
def api_dispatch_agent(
        agent_id: str,
        request: Request,
        db: Session = Depends(get_db),
        task_id: str | None = Query(default=None),
        actor: Actor = Depends(require_permission(Permission.DISPATCH)),
    ):
        svc = AgentService(db)
        try:
            run = svc.dispatch(
                agent_id,
                task_id,
                base_url=str(request.base_url).rstrip("/"),
                api_key_value=request.headers.get("authorization", "").removeprefix("Bearer ").strip(),
            )
        except AgentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=exc.message) from exc
        except AgentError as exc:
            status_code = 404 if exc.error_type == "not_found" else 400
            raise HTTPException(status_code=status_code, detail=exc.message) from exc
        except DispatchError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return serialize_agent_run(run)

@router.post("/agent-runs/{run_id}/heartbeat", response_model=AgentRunResponse)
def api_heartbeat_agent_run(
        run_id: str,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.DISPATCH)),
    ):
        svc = AgentService(db)
        try:
            run = svc.heartbeat(run_id)
        except AgentRunNotFoundError as exc:
            raise HTTPException(status_code=404, detail=exc.message) from exc
        return serialize_agent_run(run)

@router.post("/agent-runs/{run_id}/complete", response_model=AgentRunResponse)
def api_complete_agent_run(
        run_id: str,
        db: Session = Depends(get_db),
        exit_code: int = Query(...),
        _actor: Actor = Depends(require_permission(Permission.DISPATCH)),
    ):
        svc = AgentService(db)
        try:
            run = svc.complete_run(run_id, exit_code)
        except AgentRunNotFoundError as exc:
            raise HTTPException(status_code=404, detail=exc.message) from exc
        return serialize_agent_run(run)

@router.post("/agent-runs/stale-recovery")
def api_stale_recovery(
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.TASKS_EDIT)),
    ):
        svc = AgentService(db)
        recovered = svc.stale_recovery()
        return {"recovered_run_ids": recovered, "count": len(recovered)}
