"""Idea service layer: shared business logic for REST and MCP transports."""

from __future__ import annotations

from sqlalchemy.orm import Session

from flow_app.models import Idea
from flow_app.repository import (
    archive_idea,
    create_idea,
    get_idea,
    list_ideas,
    promote_tasks,
    serialize_idea,
    unarchive_idea,
    update_idea,
)
from flow_app.realtime import publish_board_event
from flow_app.schemas import IdeaCreate, IdeaUpdate, PromoteTaskSpec


class IdeaError(Exception):
    """Base exception for idea service errors."""

    def __init__(self, message: str, error_type: str = "idea_error"):
        self.message = message
        self.error_type = error_type
        super().__init__(message)


class IdeaNotFoundError(IdeaError):
    def __init__(self, idea_id: str):
        super().__init__(f"Idea not found: {idea_id}", "not_found")


class IdeaService:
    """Shared idea service operations."""

    def __init__(self, db: Session):
        self.db = db

    def list_ideas(
        self,
        project: str | None = None,
        archived: bool = False,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Idea]:
        return list_ideas(self.db, project=project, archived=archived, limit=limit, offset=offset)

    def get_idea(self, idea_id: str) -> Idea:
        return self._require_idea(idea_id)

    def create_idea(self, payload: IdeaCreate) -> Idea:
        idea = create_idea(self.db, payload)
        self._commit()
        return idea

    def update_idea(self, idea_id: str, payload: IdeaUpdate) -> Idea:
        idea = self._require_idea(idea_id)
        idea = update_idea(self.db, idea, payload)
        self._commit()
        return idea

    def archive_idea(self, idea_id: str) -> Idea:
        idea = archive_idea(self.db, self._require_idea(idea_id))
        self._commit()
        return idea

    def unarchive_idea(self, idea_id: str) -> Idea:
        idea = unarchive_idea(self.db, self._require_idea(idea_id))
        self._commit()
        return idea

    def promote_idea(self, idea_id: str, specs: list[PromoteTaskSpec], webhook_notifier, db: Session | None = None) -> Idea:
        session = db or self.db
        tasks, idea = promote_tasks(session, self._require_idea(idea_id, session=session), specs)
        for task in tasks:
            webhook_notifier.send(session, "idea_promoted", task, {"idea_id": idea.id})
        self._commit(session)
        publish_board_event(
            "idea_promoted",
            task=tasks[0] if tasks else None,
            idea_id=idea.id,
            task_ids=[task.id for task in tasks],
            project=idea.project,
        )
        return idea

    def serialize_idea(self, idea: Idea):
        return serialize_idea(idea, self.db)

    def _require_idea(self, idea_id: str, *, session: Session | None = None) -> Idea:
        idea = get_idea(session or self.db, idea_id)
        if idea is None:
            raise IdeaNotFoundError(idea_id)
        return idea

    def _commit(self, session: Session | None = None) -> None:
        (session or self.db).commit()
