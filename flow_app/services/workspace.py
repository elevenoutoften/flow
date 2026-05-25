"""Workspace service layer: shared business logic for REST and MCP transports."""

from __future__ import annotations

from sqlalchemy.orm import Session

from flow_app.models import WorkspaceConfig
from flow_app.repository import (
    create_workspace_config,
    get_workspace_config,
    list_workspace_configs,
    update_workspace_config,
)
from flow_app.schemas import WorkspaceConfigCreate, WorkspaceConfigUpdate
from flow_app.workspace import WorkspaceResult, cleanup_workspace, provision_workspace


class WorkspaceError(Exception):
    """Base exception for workspace service errors."""

    def __init__(self, message: str, error_type: str = "workspace_error"):
        self.message = message
        self.error_type = error_type
        super().__init__(message)


class WorkspaceConfigNotFoundError(WorkspaceError):
    def __init__(self, config_id: str):
        super().__init__(f"Workspace config not found: {config_id}", "not_found")


class WorkspaceService:
    """Shared workspace service operations."""

    def __init__(self, db: Session):
        self.db = db

    def list_configs(self, enabled_only: bool = False) -> list[WorkspaceConfig]:
        return list_workspace_configs(self.db, enabled_only=enabled_only)

    def get_config(self, config_id: str) -> WorkspaceConfig:
        return self._require_config(config_id)

    def create_config(self, payload: WorkspaceConfigCreate) -> WorkspaceConfig:
        config = create_workspace_config(self.db, payload)
        self._commit()
        return config

    def update_config(self, config_id: str, payload: WorkspaceConfigUpdate) -> WorkspaceConfig:
        config = update_workspace_config(self.db, self._require_config(config_id), payload)
        self._commit()
        return config

    def provision(self, config: WorkspaceConfig, task_id: str, repo_path: str | None = None) -> WorkspaceResult:
        return provision_workspace(config, task_id, repo_path)

    def cleanup(
        self,
        config_id: str,
        strategy: str,
        path: str,
        config: WorkspaceConfig | None = None,
    ) -> bool:
        cleaned = cleanup_workspace(config_id, strategy, path, config)
        self._commit()
        return cleaned

    def _require_config(self, config_id: str) -> WorkspaceConfig:
        config = get_workspace_config(self.db, config_id)
        if config is None:
            raise WorkspaceConfigNotFoundError(config_id)
        return config

    def _commit(self) -> None:
        self.db.commit()
