from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from ..repository import (
    serialize_workspace_config,
)
from ..schemas import (
    WorkspaceCleanupRequest,
    WorkspaceConfigCreate,
    WorkspaceConfigResponse,
    WorkspaceConfigUpdate,
)
from ..security import Actor, Permission, require_permission
from ..services.workspace import WorkspaceConfigNotFoundError, WorkspaceService
from .dependencies import get_db


router = APIRouter()

@router.get("/workspace-configs", response_model=list[WorkspaceConfigResponse])
def api_list_workspace_configs(
        db: Session = Depends(get_db),
        enabled_only: bool = Query(default=False),
        _actor: Actor = Depends(require_permission(Permission.WORKSPACE_READ)),
    ):
        svc = WorkspaceService(db)
        return [serialize_workspace_config(config) for config in svc.list_configs(enabled_only=enabled_only)]

@router.get("/workspace-configs/{config_id}", response_model=WorkspaceConfigResponse)
def api_get_workspace_config(
        config_id: str,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.WORKSPACE_READ)),
    ):
        svc = WorkspaceService(db)
        try:
            config = svc.get_config(config_id)
        except WorkspaceConfigNotFoundError as exc:
            raise HTTPException(status_code=404, detail=exc.message) from exc
        return serialize_workspace_config(config)

@router.post("/workspace-configs", response_model=WorkspaceConfigResponse, status_code=status.HTTP_201_CREATED)
def api_create_workspace_config(
        payload: WorkspaceConfigCreate,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.WORKSPACE_MANAGE)),
    ):
        svc = WorkspaceService(db)
        config = svc.create_config(payload)
        return serialize_workspace_config(config)

@router.patch("/workspace-configs/{config_id}", response_model=WorkspaceConfigResponse)
def api_update_workspace_config(
        config_id: str,
        payload: WorkspaceConfigUpdate,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.WORKSPACE_MANAGE)),
    ):
        svc = WorkspaceService(db)
        try:
            config = svc.update_config(config_id, payload)
        except WorkspaceConfigNotFoundError as exc:
            raise HTTPException(status_code=404, detail=exc.message) from exc
        return serialize_workspace_config(config)

@router.post("/workspace-configs/{config_id}/provision")
async def api_provision_workspace(
        config_id: str,
        task_id: str = Query(...),
        repo_path: str | None = Query(default=None),
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.WORKSPACE_MANAGE)),
    ):
        svc = WorkspaceService(db)
        try:
            config = svc.get_config(config_id)
        except WorkspaceConfigNotFoundError as exc:
            raise HTTPException(status_code=404, detail=exc.message) from exc
        return svc.provision(config, task_id, repo_path)

@router.post("/workspace-configs/{config_id}/cleanup")
async def api_cleanup_workspace(
        config_id: str,
        payload: WorkspaceCleanupRequest,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.WORKSPACE_MANAGE)),
    ):
        svc = WorkspaceService(db)
        try:
            config = svc.get_config(config_id)
        except WorkspaceConfigNotFoundError as exc:
            raise HTTPException(status_code=404, detail=exc.message) from exc
        return {
            "workspace_id": config_id,
            "strategy": payload.strategy,
            "path": payload.path,
            "cleaned": svc.cleanup(config_id, payload.strategy, payload.path, config),
        }
