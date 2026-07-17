from __future__ import annotations

from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..audit import actor_id_for, audit
from ..config import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT
from ..metrics import metrics
from ..markdown_import import parse_markdown_tasks
from ..ratelimit import require_mutation_limit
from ..repository import (
    count_tasks,
    create_task_handoff,
    create_task,
    create_task_link,
    delete_task_link,
    find_import_duplicate,
    get_task_handoff,
    get_dependency_summary,
    list_task_links,
    list_task_handoffs,
    next_task,
    serialize_task,
    serialize_task_handoff,
    serialize_task_link,
    serialize_task_list,
)
from ..realtime import publish_board_event
from ..rules_engine import emit_event
from ..schemas import (
    MarkdownImportCommitRequest,
    MarkdownImportCommitResponse,
    MarkdownImportPreviewRequest,
    MarkdownImportPreviewResponse,
    MoveRequest,
    NoteRequest,
    PaginatedResponse,
    STATUSES,
    ClaimRequest,
    DoneRequest,
    DependencySummary,
    HandoffRequest,
    HandoffResponse,
    TaskCreate,
    TaskLinkCreate,
    TaskLinkResponse,
    TaskResponse,
    TaskUpdate,
)
from ..security import (
    Actor,
    Permission,
    PermissionDenied,
    authorize_task_update,
    require_permission,
)
from ..services.task import (
    InvalidTransitionError,
    MissingAssigneeError,
    NotePermissionError,
    SendbackContractError,
    TaskAlreadyClaimedError,
    TaskClaimKeyConflictError,
    TaskConcurrentModificationError,
    TaskNotFoundError,
)
from .dependencies import _commit, _make_task_service, _require_task, get_db


router = APIRouter()

@router.post("/import/markdown/preview", response_model=MarkdownImportPreviewResponse)
def api_preview_markdown_import(
        payload: MarkdownImportPreviewRequest,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.TASKS_CREATE)),
    ):
        items = parse_markdown_tasks(
            payload.markdown,
            source_filename=payload.source_filename,
            default_project=payload.default_project,
            default_status=payload.default_status,
            default_priority=payload.default_priority,
        )
        for item in items:
            duplicate = find_import_duplicate(
                db,
                project=item.project,
                title=item.title,
                source_filename=item.source_filename,
                source_line=item.source_line,
            )
            if duplicate:
                item.duplicate = True
                item.duplicate_task_id = duplicate.id
        return MarkdownImportPreviewResponse(items=items)

@router.post(
    "/import/markdown/commit",
    response_model=MarkdownImportCommitResponse,
    dependencies=[Depends(require_mutation_limit)],
)
def api_commit_markdown_import(
        payload: MarkdownImportCommitRequest,
        db: Session = Depends(get_db),
        actor: Actor = Depends(require_permission(Permission.TASKS_CREATE)),
    ):
        batch_id = f"import_{uuid4().hex[:12]}"
        created = []
        skipped = []
        svc = _make_task_service(db)

        # Partition items into duplicates (skipped) and new tasks (to stage).
        # The new tasks are staged atomically: every item is flushed into the
        # session before the single committing commit.  If any item raises
        # during staging the whole batch is rolled back — no partial import.
        stage_payloads: list[TaskCreate] = []
        stage_indices: list[int] = []
        for index, item in enumerate(payload.items):
            duplicate = find_import_duplicate(
                db,
                project=item.project,
                title=item.title,
                source_filename=item.source_filename,
                source_line=item.source_line,
            )
            if item.duplicate or duplicate:
                item.duplicate = True
                item.duplicate_task_id = duplicate.id if duplicate else item.duplicate_task_id
                skipped.append(item)
                continue

            stage_payloads.append(
                TaskCreate(
                    title=item.title,
                    status=item.status,
                    priority=item.priority,
                    project=item.project,
                    assignee=item.assignee,
                    description=item.description,
                    acceptance_criteria=item.acceptance_criteria,
                    source_filename=item.source_filename,
                    source_line=item.source_line,
                    import_batch_id=batch_id,
                    source_title=item.source_title,
                )
            )
            stage_indices.append(index)

        # Stage every new task in one transaction.  create_tasks_batch stages
        # only DB state (task rows + webhook delivery rows) — no external HTTP,
        # no automation actions, no spawned agents.  Side effects are deferred
        # to fire_post_commit_effects after the commit succeeds.
        try:
            created_tasks = svc.create_tasks_batch(stage_payloads, actor=actor)
        except Exception as exc:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Import failed and was rolled back: {exc}",
            ) from exc
        # Single commit for the entire batch.  If this fails, every staged row
        # (tasks + webhook delivery outbox) is rolled back together.
        _commit(db)

        # Fire non-transactional side effects AFTER the commit succeeds so a
        # rolled-back batch never triggers external HTTP or spawned processes.
        # A post-commit notification failure is recorded through delivery
        # semantics and does NOT roll back the committed task batch.
        svc.fire_post_commit_effects(created_tasks, actor=actor)

        # Board events are in-memory (BoardEventHub, not the DB).  Publish them
        # only AFTER the commit succeeds so a rolled-back batch publishes nothing.
        # Carry import_batch_id in the event payload so consumers can correlate.
        for task in created_tasks:
            publish_board_event("task_created", task, import_batch_id=batch_id)
            created.append(serialize_task(task))

        return MarkdownImportCommitResponse(import_batch_id=batch_id, created=created, skipped=skipped)

@router.get("/tasks/next", response_model=TaskResponse)
def api_next_task(
        db: Session = Depends(get_db),
        project: str | None = Query(default=None),
        _actor: Actor = Depends(require_permission(Permission.TASKS_READ)),
    ):
        task = next_task(db, project=project)
        if task is None:
            raise HTTPException(status_code=404, detail="No unclaimed task is available.")
        return serialize_task(task)

@router.get("/tasks", response_model=PaginatedResponse)
def api_list_tasks(
        db: Session = Depends(get_db),
        project: str | None = Query(default=None),
        status_filter: str | None = Query(default=None, alias="status"),
        assignee: str | None = Query(default=None),
        unclaimed: bool = Query(default=False),
        limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
        offset: int = Query(default=0, ge=0),
        _actor: Actor = Depends(require_permission(Permission.TASKS_READ)),
    ):
        if status_filter and status_filter not in STATUSES:
            raise HTTPException(status_code=422, detail="Invalid task status.")
        items = [
            serialize_task_list(task)
            for task in _make_task_service(db).list_tasks(
                project=project,
                status=status_filter,
                assignee=assignee,
                unclaimed=unclaimed,
                limit=limit,
                offset=offset,
            )
        ]
        total = count_tasks(db, project=project, status=status_filter, assignee=assignee, unclaimed=unclaimed)
        return {"items": items, "total": total, "limit": limit, "offset": offset}

@router.post(
    "/tasks",
    response_model=TaskResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_mutation_limit)],
)
def api_create_task(
        payload: TaskCreate,
        db: Session = Depends(get_db),
        actor: Actor = Depends(require_permission(Permission.TASKS_CREATE)),
    ):
        task = _make_task_service(db).create_task(payload, actor)
        metrics.inc("task.created")
        audit(db, actor_id_for(actor), "task.create", "task", task.id, {"status": task.status, "project": task.project})
        _commit(db)
        return serialize_task(task)

@router.get("/tasks/{task_id}", response_model=TaskResponse)
def api_get_task(
        task_id: str,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.TASKS_READ)),
    ):
        return serialize_task(_require_task(db, task_id))

@router.get("/tasks/{task_id}/handoffs", response_model=list[HandoffResponse])
def api_list_task_handoffs(
        task_id: str,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.TASKS_READ)),
    ):
        _require_task(db, task_id)
        return [serialize_task_handoff(handoff) for handoff in list_task_handoffs(db, task_id)]

@router.get("/tasks/{task_id}/handoffs/{handoff_id}", response_model=HandoffResponse)
def api_get_task_handoff(
        task_id: str,
        handoff_id: str,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.TASKS_READ)),
    ):
        _require_task(db, task_id)
        handoff = get_task_handoff(db, handoff_id)
        if handoff is None or handoff.task_id != task_id:
            raise HTTPException(status_code=404, detail="Task handoff not found.")
        return serialize_task_handoff(handoff)

@router.post(
    "/tasks/{task_id}/handoffs",
    response_model=HandoffResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_mutation_limit)],
)
@router.post(
    "/tasks/{task_id}/handoff",
    response_model=HandoffResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_mutation_limit)],
)
def api_create_task_handoff(
        task_id: str,
        payload: HandoffRequest,
        db: Session = Depends(get_db),
        actor: Actor = Depends(require_permission(Permission.HANDOFF_CREATE)),
    ):
        task = _require_task(db, task_id)
        handoff = create_task_handoff(
            db,
            task_id,
            payload.author or actor.name,
            payload.summary,
            payload.changed_files,
            payload.commands_run,
            payload.tests_run,
            payload.artifacts,
            payload.attempted_but_failed,
            payload.remaining_work,
            payload.outcome,
            payload.next_recommended_agent,
            payload.capabilities,
            author_key_id=actor.key_id,
        )
        audit(
            db,
            actor_id_for(actor),
            "task.handoff",
            "task",
            task_id,
            {"to_status": task.status, "to_assignee": payload.next_recommended_agent},
        )
        _commit(db)
        publish_board_event("task_updated", _require_task(db, task_id), changed="handoff")
        return serialize_task_handoff(handoff)

@router.get("/tasks/{task_id}/links", response_model=list[TaskLinkResponse])
def api_list_task_links(
        task_id: str,
        db: Session = Depends(get_db),
        link_type: str | None = Query(default=None),
        _actor: Actor = Depends(require_permission(Permission.LINKS_READ)),
    ):
        _require_task(db, task_id)
        return [serialize_task_link(link) for link in list_task_links(db, task_id=task_id, link_type=link_type)]

@router.get("/tasks/{task_id}/dependencies", response_model=DependencySummary)
def api_get_dependencies(
        task_id: str,
        db: Session = Depends(get_db),
        _actor: Actor = Depends(require_permission(Permission.LINKS_READ)),
    ):
        try:
            return get_dependency_summary(db, task_id)
        except ValueError:
            raise HTTPException(status_code=404, detail="Task not found.")

@router.post(
    "/tasks/{task_id}/link",
    response_model=TaskLinkResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_mutation_limit)],
)
def api_link_task(
        task_id: str,
        payload: TaskLinkCreate,
        db: Session = Depends(get_db),
        actor: Actor = Depends(require_permission(Permission.LINKS_MANAGE)),
    ):
        _require_task(db, task_id)
        if task_id not in {payload.parent_id, payload.child_id}:
            raise HTTPException(status_code=400, detail="Path task_id must be one endpoint of the link.")
        try:
            link = create_task_link(db, payload)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        audit(
            db,
            actor_id_for(actor),
            "task.link",
            "task",
            task_id,
            {"parent_id": payload.parent_id, "link_type": payload.link_type},
        )
        _commit(db)
        publish_board_event("task_updated", _require_task(db, task_id), changed="dependencies")
        return serialize_task_link(link)

@router.delete(
    "/tasks/{task_id}/link/{link_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_mutation_limit)],
)
def api_unlink_task(
        task_id: str,
        link_id: str,
        db: Session = Depends(get_db),
        actor: Actor = Depends(require_permission(Permission.LINKS_MANAGE)),
    ):
        _require_task(db, task_id)
        link = next((item for item in list_task_links(db, task_id=task_id) if item.id == link_id), None)
        if link is None:
            raise HTTPException(status_code=404, detail="Task link not found.")
        delete_task_link(db, link_id)
        audit(db, actor_id_for(actor), "task.unlink", "task", task_id, {"link_id": link_id})
        _commit(db)
        publish_board_event("task_updated", _require_task(db, task_id), changed="dependencies")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

@router.patch("/tasks/{task_id}", response_model=TaskResponse, dependencies=[Depends(require_mutation_limit)])
def api_update_task(
        task_id: str,
        payload: TaskUpdate,
        db: Session = Depends(get_db),
        actor: Actor = Depends(require_permission(Permission.TASKS_READ)),
    ):
        task = _require_task(db, task_id)
        try:
            authorize_task_update(actor, task, payload)
        except PermissionDenied as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=exc.detail)
        try:
            task = _make_task_service(db).update_task(task_id, payload, actor)
        except TaskConcurrentModificationError as exc:
            raise HTTPException(status_code=409, detail=exc.message)
        audit(
            db,
            actor_id_for(actor),
            "task.update",
            "task",
            task.id,
            {"changes": sorted(payload.model_fields_set)},
        )
        _commit(db)
        return serialize_task(task)

@router.post("/tasks/{task_id}/claim", response_model=TaskResponse, dependencies=[Depends(require_mutation_limit)])
def api_claim_task(
        task_id: str,
        payload: ClaimRequest,
        db: Session = Depends(get_db),
        actor: Actor = Depends(require_permission(Permission.TASKS_CLAIM)),
    ):
        try:
            task = _make_task_service(db).claim_task(task_id, actor, agent_name=payload.agent_name)
        except TaskNotFoundError:
            raise HTTPException(status_code=404, detail="Task not found.")
        except (TaskAlreadyClaimedError, TaskClaimKeyConflictError) as exc:
            raise HTTPException(status_code=409, detail=exc.message)
        except TaskConcurrentModificationError as exc:
            raise HTTPException(status_code=409, detail=exc.message)
        except InvalidTransitionError as exc:
            raise HTTPException(status_code=403, detail=exc.message)
        except MissingAssigneeError as exc:
            raise HTTPException(status_code=400, detail=exc.message)
        audit(db, actor_id_for(actor), "task.claim", "task", task.id)
        _commit(db)
        return serialize_task(task)

@router.post("/tasks/{task_id}/release", response_model=TaskResponse, dependencies=[Depends(require_mutation_limit)])
def api_release_task(
        task_id: str,
        db: Session = Depends(get_db),
        actor: Actor = Depends(require_permission(Permission.TASKS_CLAIM)),
    ):
        old_status = _require_task(db, task_id).status
        try:
            task = _make_task_service(db).release_task(task_id)
        except TaskNotFoundError:
            raise HTTPException(status_code=404, detail="Task not found.")
        except TaskConcurrentModificationError as exc:
            raise HTTPException(status_code=409, detail=exc.message)
        audit(db, actor_id_for(actor), "task.release", "task", task.id, {"from": old_status})
        _commit(db)
        return serialize_task(task)

@router.post("/tasks/{task_id}/move", response_model=TaskResponse, dependencies=[Depends(require_mutation_limit)])
def api_move_task(
        task_id: str,
        payload: MoveRequest,
        db: Session = Depends(get_db),
        actor: Actor = Depends(require_permission(Permission.TASKS_MOVE)),
    ):
        old_status = _require_task(db, task_id).status
        try:
            task = _make_task_service(db).move_task(task_id, payload.status, actor)
        except TaskNotFoundError:
            raise HTTPException(status_code=404, detail="Task not found.")
        except InvalidTransitionError as exc:
            raise HTTPException(status_code=403, detail=exc.message)
        except SendbackContractError as exc:
            raise HTTPException(status_code=409, detail=exc.message)
        except TaskConcurrentModificationError as exc:
            raise HTTPException(status_code=409, detail=exc.message)
        metrics.inc("task.moved")
        audit(db, actor_id_for(actor), "task.move", "task", task.id, {"from": old_status, "to": task.status})
        _commit(db)
        return serialize_task(task)

@router.post("/tasks/{task_id}/note", response_model=TaskResponse, dependencies=[Depends(require_mutation_limit)])
def api_add_note(
        task_id: str,
        payload: NoteRequest,
        db: Session = Depends(get_db),
        actor: Actor = Depends(require_permission(Permission.TASKS_NOTE)),
    ):
        try:
            task = _make_task_service(db).add_note(task_id, payload.note, actor, author=payload.author)
        except TaskNotFoundError:
            raise HTTPException(status_code=404, detail="Task not found.")
        except NotePermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=exc.message)
        audit(db, actor_id_for(actor), "task.note", "task", task.id, {"note_length": len(payload.note)})
        _commit(db)
        return serialize_task(task)

@router.post("/tasks/{task_id}/done", response_model=TaskResponse, dependencies=[Depends(require_mutation_limit)])
def api_done_task(
        task_id: str,
        payload: DoneRequest,
        db: Session = Depends(get_db),
        actor: Actor = Depends(require_permission(Permission.TASKS_DONE)),
    ):
        try:
            task = _make_task_service(db).done_task(
                task_id,
                actor,
                payload.summary,
                author=payload.author,
                handoff=payload.handoff,
            )
        except TaskNotFoundError:
            raise HTTPException(status_code=404, detail="Task not found.")
        except InvalidTransitionError as exc:
            raise HTTPException(status_code=403, detail=exc.message)
        except TaskConcurrentModificationError as exc:
            raise HTTPException(status_code=409, detail=exc.message)
        audit(db, actor_id_for(actor), "task.done", "task", task.id, {})
        _commit(db)
        return serialize_task(task)
