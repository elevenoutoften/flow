"""Task service layer: shared business logic for REST and MCP transports."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.orm import Session

from flow_app.models import ApiKeyRole, Task
from flow_app.repository import (
    add_note,
    auto_promote_unblocked_children,
    cas_update_task,
    create_task,
    create_task_handoff,
    get_task,
    list_tasks,
    list_task_handoffs,
    task_update_changes,
)
from flow_app.schemas import HandoffRequest, TaskCreate, TaskUpdate
from flow_app.security import Actor, can_note_task, is_valid_transition


class TaskError(Exception):
    """Base exception for task service errors."""

    def __init__(self, message: str, error_type: str = "task_error"):
        self.message = message
        self.error_type = error_type
        super().__init__(message)


class TaskNotFoundError(TaskError):
    def __init__(self, task_id: str):
        super().__init__(f"Task not found: {task_id}", "not_found")


class TaskAlreadyClaimedError(TaskError):
    def __init__(self, assignee: str):
        super().__init__(f"Task is already claimed by {assignee}.", "already_claimed")


class TaskClaimKeyConflictError(TaskError):
    def __init__(self):
        super().__init__("Task is already claimed by a different key.", "claim_key_conflict")


class TaskConcurrentModificationError(TaskError):
    def __init__(self, task_id: str):
        super().__init__(
            f"Task {task_id} was modified by another request. Please retry.",
            "concurrent_modification",
        )


class InvalidTransitionError(TaskError):
    def __init__(self, role: str, from_status: str, to_status: str, *, action: str = "move task"):
        if action == "mark task as done":
            message = f"Role '{role}' cannot mark task as done from {from_status}."
        else:
            message = f"Role '{role}' cannot move task from {from_status} to {to_status}."
        super().__init__(message, "invalid_transition")


class SendbackContractError(TaskError):
    def __init__(self):
        super().__init__(
            "Send-back requires a reviewer-authored note or handoff.",
            "sendback_contract",
        )


class NotePermissionError(TaskError):
    def __init__(self):
        super().__init__("Insufficient permission to note this task.", "note_permission")


class MissingAssigneeError(TaskError):
    def __init__(self):
        super().__init__("agent_name is required.", "missing_assignee")


class TaskService:
    """Shared task mutation service.

    Transport adapters call these methods and translate TaskError subclasses
    into HTTP or JSON-RPC specific responses.

    Mutation methods keep business state, rule events, webhook outbox rows, and
    notification delivery rows in one transaction. A visible task mutation must
    not be committed unless its associated delivery rows are committed too.
    """

    def __init__(self, db: Session, commit_fn: Callable[[Session], None], webhook_notifier, telegram_notifier, rule_emitter):
        self.db = db
        self._commit = commit_fn
        self._webhook = webhook_notifier
        self._telegram = telegram_notifier
        self._emit_rule = rule_emitter

    def create_task(self, payload: TaskCreate, actor: Actor | None = None) -> Task:
        task = create_task(self.db, payload)
        self._emit_rule(self.db, "task_created", task_id=task.id, actor=actor)
        self._webhook.send(self.db, "task_created", task)
        self._telegram.send(self.db, "task_created", task)
        self._commit(self.db)
        return task

    def list_tasks(
        self,
        project: str | None = None,
        status: str | None = None,
        assignee: str | None = None,
        unclaimed: bool = False,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Task]:
        return list_tasks(
            self.db,
            project=project,
            status=status,
            assignee=assignee,
            unclaimed=unclaimed,
            limit=limit,
            offset=offset,
        )

    def update_task(self, task_id: str, payload: TaskUpdate, actor: Actor) -> Task:
        task = self._require_task(task_id)
        expected_version = task.version
        was_human_required = bool(task.human_required)
        if payload.human_required is False:
            payload = payload.model_copy(update={"blocker_reason": ""})
        changes = task_update_changes(payload)
        if changes and not cas_update_task(self.db, task_id, expected_version, changes):
            self.db.rollback()
            raise TaskConcurrentModificationError(task_id)
        task = self._require_task(task_id)
        if not was_human_required and bool(task.human_required):
            self._emit_rule(self.db, "task_blocked", task_id=task.id, actor=actor)
            self._webhook.send(
                self.db,
                "task_blocked",
                task,
                {"human_required": True, "blocker_reason": task.blocker_reason},
            )
            self._telegram.send(
                self.db,
                "task_blocked",
                task,
                {"human_required": True, "blocker_reason": task.blocker_reason},
            )
        self._commit(self.db)
        return task

    def claim_task(self, task_id: str, actor: Actor, agent_name: str | None = None) -> Task:
        task = self._require_task(task_id)
        expected_version = task.version
        old_status = task.status
        assignee = agent_name or actor.name
        if not assignee:
            raise MissingAssigneeError()
        if task.assignee and task.assignee != assignee:
            raise TaskAlreadyClaimedError(task.assignee)
        if task.claimer_key_id and task.claimer_key_id != actor.key_id:
            raise TaskClaimKeyConflictError()
        target_status = task.status
        if task.status in {"backlog", "todo"}:
            if not is_valid_transition(actor, task.status, "doing"):
                raise InvalidTransitionError(actor.role.value, task.status, "doing")
            target_status = "doing"
        if not cas_update_task(
            self.db,
            task_id,
            expected_version,
            {"assignee": assignee, "claimer_key_id": actor.key_id, "status": target_status},
        ):
            self.db.rollback()
            raise TaskConcurrentModificationError(task_id)
        task = self._require_task(task_id)
        self._emit_rule(self.db, "task_claimed", task_id=task_id, data={"assignee": assignee}, actor=actor)
        data = {"status": {"from": old_status, "to": task.status}, "assignee": task.assignee}
        self._webhook.send(self.db, "task_claimed", task, data)
        self._telegram.send(self.db, "task_claimed", task, data)
        self._commit(self.db)
        return task

    def release_task(self, task_id: str) -> Task:
        task = self._require_task(task_id)
        expected_version = task.version
        target_status = task.status
        if task.status == "doing":
            target_status = "todo"
        if not cas_update_task(
            self.db,
            task_id,
            expected_version,
            {"assignee": None, "claimer_key_id": None, "status": target_status},
        ):
            self.db.rollback()
            raise TaskConcurrentModificationError(task_id)
        task = self._require_task(task_id)
        self._commit(self.db)
        return task

    def move_task(self, task_id: str, target_status: str, actor: Actor) -> Task:
        task = self._require_task(task_id)
        expected_version = task.version
        old_status = task.status
        if not is_valid_transition(actor, task.status, target_status):
            raise InvalidTransitionError(actor.role.value, task.status, target_status)
        if task.status == "review" and target_status == "todo" and actor.role == ApiKeyRole.reviewer:
            has_reviewer_note = any(note.author == actor.name for note in task.notes)
            has_reviewer_handoff = any(
                handoff.author == actor.name for handoff in list_task_handoffs(self.db, task.id)
            )
            if not has_reviewer_note and not has_reviewer_handoff:
                raise SendbackContractError()
        if not cas_update_task(self.db, task_id, expected_version, {"status": target_status}):
            self.db.rollback()
            raise TaskConcurrentModificationError(task_id)
        task = self._require_task(task_id)
        if target_status == "done":
            auto_promote_unblocked_children(self.db, task.id)
        self._emit_rule(
            self.db,
            "task_moved",
            task_id=task_id,
            data={"from_status": old_status, "to_status": target_status},
            actor=actor,
        )
        data = {"status": {"from": old_status, "to": task.status}}
        self._webhook.send(self.db, "task_moved", task, data)
        self._telegram.send(self.db, "task_moved", task, data)
        self._commit(self.db)
        return task

    def add_note(self, task_id: str, note_text: str, actor: Actor, author: str | None = None) -> Task:
        task = self._require_task(task_id)
        if not can_note_task(actor, task):
            raise NotePermissionError()
        add_note(self.db, task, note_text, author=author or actor.name)
        self._commit(self.db)
        return self._require_task(task_id)

    def done_task(
        self,
        task_id: str,
        actor: Actor,
        summary: str,
        author: str | None = None,
        handoff: HandoffRequest | None = None,
    ) -> Task:
        task = self._require_task(task_id)
        expected_version = task.version
        old_status = task.status
        if not is_valid_transition(actor, task.status, "done"):
            raise InvalidTransitionError(actor.role.value, task.status, "done", action="mark task as done")
        if not cas_update_task(self.db, task_id, expected_version, {"status": "done"}):
            self.db.rollback()
            raise TaskConcurrentModificationError(task_id)
        task = self._require_task(task_id)
        effective_author = author or actor.name
        if handoff is not None:
            create_task_handoff(
                self.db,
                task.id,
                handoff.author or effective_author,
                handoff.summary,
                handoff.changed_files,
                handoff.commands_run,
                handoff.tests_run,
                handoff.artifacts,
                handoff.attempted_but_failed,
                handoff.remaining_work,
                handoff.outcome,
                handoff.next_recommended_agent,
                handoff.capabilities,
            )
        add_note(self.db, task, summary, author=effective_author)
        auto_promote_unblocked_children(self.db, task.id)
        self._emit_rule(self.db, "task_completed", task_id=task_id, actor=actor)
        data = {"status": {"from": old_status, "to": "done"}}
        self._webhook.send(self.db, "task_completed", task, data)
        self._telegram.send(self.db, "task_completed", task, data)
        self._commit(self.db)
        return self._require_task(task_id)

    def set_human_required(
        self,
        task_id: str,
        human_required: bool,
        actor: Actor,
        blocker_reason: str | None = None,
        assignee_type: str | None = None,
    ) -> Task:
        task = self._require_task(task_id)
        expected_version = task.version
        changes: dict[str, object] = {"human_required": human_required}
        if human_required:
            if blocker_reason is not None:
                changes["blocker_reason"] = blocker_reason
            if assignee_type is not None:
                changes["assignee_type"] = assignee_type
        else:
            changes["blocker_reason"] = ""
        changes = task_update_changes(TaskUpdate(**changes))
        if not cas_update_task(self.db, task_id, expected_version, changes):
            self.db.rollback()
            raise TaskConcurrentModificationError(task_id)
        task = self._require_task(task_id)
        self._commit(self.db)
        return task

    def _require_task(self, task_id: str) -> Task:
        task = get_task(self.db, task_id)
        if task is None:
            raise TaskNotFoundError(task_id)
        return task
