from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..repository import (
    create_project,
    get_project,
    list_projects,
    serialize_project,
    update_project,
)
from ..schemas import (
    ProjectCreate,
    ProjectResponse,
    ProjectUpdate,
)
from ..security import Actor, Permission, require_permission
from .dependencies import _commit, get_db


router = APIRouter()

@router.get("/projects", response_model=list[ProjectResponse])
def api_list_projects(
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.TASKS_READ)),
    ):
        return [serialize_project(project) for project in list_projects(db)]

@router.post("/projects", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
def api_create_project(
        payload: ProjectCreate,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.TASKS_CREATE)),
    ):
        project = create_project(db, payload)
        _commit(db)
        return serialize_project(project)

@router.get("/projects/{slug}", response_model=ProjectResponse)
def api_get_project(
        slug: str,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.TASKS_READ)),
    ):
        project = get_project(db, slug)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found.")
        return serialize_project(project)

@router.patch("/projects/{slug}", response_model=ProjectResponse)
def api_update_project(
        slug: str,
        payload: ProjectUpdate,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.TASKS_EDIT)),
    ):
        project = get_project(db, slug)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found.")
        project = update_project(db, project, payload)
        _commit(db)
        return serialize_project(project)
