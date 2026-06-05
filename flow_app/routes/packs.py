"""Pack export/import REST API endpoints."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..packs import VALID_CONFLICT_POLICIES, PackImportConflict, export_pack, import_pack, validate_pack
from ..security import Actor, Permission, require_permission
from .dependencies import get_db

router = APIRouter(tags=["packs"])


class PackImportBody(BaseModel):
    """Body for pack import endpoint."""
    schema_version: int = 1
    name: str = "flow-pack"
    description: str = ""
    exported_at: str = ""
    rules: list[dict] = []
    agents: list[dict] = []
    webhook_configs: list[dict] = []
    notification_configs: list[dict] = []


@router.get("/packs/export")
def api_export_pack(
    request: Request,
    download: int = 0,
    db: Session = Depends(get_db),
    _actor: Actor = Depends(require_permission(Permission.KEY_MANAGE)),
):
    """Export automation pack as JSON with secrets redacted."""
    pack = export_pack(db)
    if download:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        filename = f"flow-pack-{timestamp}.json"
        return JSONResponse(
            content=pack,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    return pack


@router.post("/packs/import")
def api_import_pack(
    body: PackImportBody,
    request: Request,
    dry_run: int = 0,
    conflict_policy: str = Query(default="update"),
    db: Session = Depends(get_db),
    _actor: Actor = Depends(require_permission(Permission.KEY_MANAGE)),
):
    """Import an automation pack. Use ?dry_run=1 to validate without writing."""
    if conflict_policy not in VALID_CONFLICT_POLICIES:
        raise HTTPException(
            status_code=422,
            detail={"errors": [f"Invalid conflict_policy: expected one of {sorted(VALID_CONFLICT_POLICIES)}"]},
        )
    pack = body.model_dump()
    errors = validate_pack(pack)
    if errors and not dry_run:
        raise HTTPException(status_code=422, detail={"errors": errors})

    try:
        result = import_pack(db, pack, dry_run=bool(dry_run), conflict_policy=conflict_policy)
    except PackImportConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={"errors": [str(exc)], "section": exc.section, "name": exc.name},
        ) from exc

    if result["errors"]:
        raise HTTPException(status_code=422, detail={"errors": result["errors"]})

    return result
