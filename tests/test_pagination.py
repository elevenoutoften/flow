from __future__ import annotations

import pytest

from flow_app.database import Base, build_engine, build_session_factory
from flow_app.repository import (
    create_agent,
    create_agent_run,
    create_idea,
    create_task,
    create_webhook_config,
    create_webhook_delivery,
    list_agent_runs,
    list_ideas,
    list_tasks,
    list_webhook_deliveries,
)
from flow_app.schemas import AgentCreate, IdeaCreate, TaskCreate


@pytest.fixture
def db(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'pagination.sqlite'}")
    Base.metadata.create_all(bind=engine)
    session = build_session_factory(engine)()
    try:
        yield session
    finally:
        session.close()


def test_repository_list_functions_with_limit_none_return_all_rows(db):
    agent = create_agent(db, AgentCreate(name="pagination-agent", command="python -m flow_app.hermes_wrapper"))
    webhook, _ = create_webhook_config(
        db,
        name="pagination-webhook",
        url="https://example.com/webhook",
        events=["task_created"],
        project="default",
        max_retries=3,
        retry_backoff_seconds=60,
    )

    created_count = 7
    for index in range(created_count):
        task = create_task(db, TaskCreate(title=f"Task {index}", project="default"))
        create_idea(db, IdeaCreate(title=f"Idea {index}", project="default"))
        create_agent_run(db, agent_id=agent.id, task_id=task.id, status="running")
        create_webhook_delivery(db, webhook.id, "task_created", f'{{"task_id":"{task.id}"}}')

    db.commit()

    assert len(list_tasks(db, limit=None)) == created_count
    assert len(list_ideas(db, limit=None)) == created_count
    assert len(list_agent_runs(db, limit=None)) == created_count
    assert len(list_webhook_deliveries(db, webhook.id, limit=None)) == created_count


def test_list_tasks_unclaimed_filter_is_unbounded_for_internal_callers(db):
    unclaimed_count = 105
    for index in range(unclaimed_count):
        create_task(db, TaskCreate(title=f"Unclaimed {index}", status="todo", project="default"))
    create_task(db, TaskCreate(title="Claimed", status="todo", project="default", assignee="codex"))
    db.commit()

    tasks = list_tasks(db, unclaimed=True)

    assert len(tasks) == unclaimed_count
    assert all(task.assignee is None for task in tasks)
