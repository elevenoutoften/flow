from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from flow_app.database import Base, build_engine, build_session_factory
from flow_app.models import ApiKeyRole
from flow_app.repository import create_task, generate_counter_id, get_task
from flow_app.schemas import TaskCreate
from flow_app.security import Actor
from flow_app.services.task import TaskConcurrentModificationError, TaskService


class _NoopNotifier:
    def send(self, *args, **kwargs):
        return None


def _service(db):
    return TaskService(
        db=db,
        commit_fn=lambda session: session.commit(),
        webhook_notifier=_NoopNotifier(),
        telegram_notifier=_NoopNotifier(),
        discord_notifier=_NoopNotifier(),
        rule_emitter=lambda *args, **kwargs: None,
    )


def test_generate_counter_id_is_unique_under_concurrent_calls(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'ids.sqlite'}")
    Base.metadata.create_all(bind=engine)
    session_factory = build_session_factory(engine)

    def generate_one(_index: int) -> str:
        with session_factory() as session:
            value = generate_counter_id(session, "task", "flow")
            session.commit()
            return value

    with ThreadPoolExecutor(max_workers=12) as executor:
        ids = list(executor.map(generate_one, range(100)))

    assert len(ids) == 100
    assert len(set(ids)) == 100
    assert sorted(ids) == [f"flow_{index:06d}" for index in range(1, 101)]


def test_stale_task_move_raises_concurrent_modification(tmp_path, monkeypatch):
    engine = build_engine(f"sqlite:///{tmp_path / 'tasks.sqlite'}")
    Base.metadata.create_all(bind=engine)
    session_factory = build_session_factory(engine)
    actor = Actor(name="admin", role=ApiKeyRole.admin, source="test", key_id="key-a")

    with session_factory() as setup:
        task = create_task(setup, TaskCreate(title="Race", status="todo"))
        task_id = task.id
        setup.commit()

    stale_session = session_factory()
    fresh_session = session_factory()
    try:
        stale_task = get_task(stale_session, task_id)
        assert stale_task.version == 1

        moved = _service(fresh_session).move_task(task_id, "doing", actor)
        assert moved.version == 2

        monkeypatch.setattr("flow_app.services.task.get_task", lambda _session, _task_id: stale_task)
        with pytest.raises(TaskConcurrentModificationError):
            _service(stale_session).move_task(task_id, "review", actor)
    finally:
        stale_session.close()
        fresh_session.close()


def test_rest_maps_concurrent_modification_to_409(client, monkeypatch):
    task = client.post("/api/tasks", json={"title": "Conflict", "status": "todo"}).json()

    monkeypatch.setattr("flow_app.services.task.cas_update_task", lambda *args, **kwargs: False)

    response = client.post(f"/api/tasks/{task['id']}/move", json={"status": "doing"})

    assert response.status_code == 409
    assert response.json()["detail"] == f"Task {task['id']} was modified by another request. Please retry."
