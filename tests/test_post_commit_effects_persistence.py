"""flow_000988 — Persist and report post-commit effect outcomes.

Verifies that ``fire_post_commit_effects`` persists notification delivery rows,
automation rule ``last_run_at``, and rule action results (e.g. added notes) in an
explicit post-commit transaction, that the already-committed task batch is never
re-created or rolled back, and that bare except/pass handling has been replaced
with structured logging and a persisted failure record.

Acceptance criteria covered:
  1. Mocked successful Telegram + Discord HTTP → NotificationDelivery rows and
     final success status persist in a new session.
  2. A task_created automation rule whose action (add_note) changes DB state —
     after the request closes, a new session observes the note AND the rule's
     last_run_at.
  3. Post-commit effect state persisted in an explicit transaction without
     re-creating or rolling back the already-committed imported tasks.
  4. A provider that raises before recording a delivery row: the task stays
     committed, exactly one board event exists, a failure record (delivery row
     with status=failed) exists, and caplog contains provider + task identity.
  5. Zero pre-commit side effects, exact-once success calls, atomic task
     rollback on staging failure, and the full isolated suite stays green
     (verified by the rest of the suite, not re-asserted here).
"""
from __future__ import annotations

import json
import logging
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import select

from flow_app.models import NotificationDelivery, Task
from flow_app.realtime import board_events


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _deliveries(client) -> list[NotificationDelivery]:
    with client.app.state.SessionLocal() as db:
        return list(db.scalars(select(NotificationDelivery)).all())


def _import_one(client, *, title: str = "Persist effect task", priority: int = 50):
    """Import a single task via the markdown commit endpoint."""
    response = client.post(
        "/api/import/markdown/commit",
        json={
            "items": [
                {
                    "preview_id": "test-1",
                    "title": title,
                    "status": "todo",
                    "priority": priority,
                    "project": "default",
                }
            ]
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _import_two(client, title_a: str, title_b: str):
    response = client.post(
        "/api/import/markdown/commit",
        json={
            "items": [
                {
                    "preview_id": "test-1",
                    "title": title_a,
                    "status": "todo",
                    "priority": 50,
                    "project": "default",
                },
                {
                    "preview_id": "test-2",
                    "title": title_b,
                    "status": "todo",
                    "priority": 50,
                    "project": "default",
                },
            ]
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _make_settings():
    from flow_app.config import get_settings
    return replace(
        get_settings(),
        telegram_bot_token="123456:ABC",
        telegram_chat_id="-1001234567890",
        discord_webhook_url="https://discord.example/webhook",
    )


# ---------------------------------------------------------------------------
# AC 1 — NotificationDelivery rows + success status persist in a new session
# ---------------------------------------------------------------------------

def test_ac1_telegram_and_discord_delivery_rows_persist_after_request_close(client, monkeypatch):
    """Mocked successful Telegram and Discord HTTP responses.  After the
    request closes (session disposed), a new session must observe each
    NotificationDelivery row with status=success."""
    from flow_app.routes import dependencies as deps_mod

    # Wire real providers but with test settings so they actually create rows.
    settings = _make_settings()
    telegram = deps_mod._telegram_notifier
    telegram._settings = settings
    discord = deps_mod._discord_notifier
    discord._settings = settings

    board_events.clear()

    # Mock the HTTP layer for both providers so no real network calls happen.
    # Both providers import the same `httpx` module, so patching
    # flow_app.telegram.httpx.post and flow_app.discord.httpx.post patches the
    # same object.  Use a single side_effect keyed by URL to return the right
    # response for each provider.
    def _fake_post(url, *args, **kwargs):
        if "api.telegram.org" in url:
            return SimpleNamespace(status_code=200, text="ok", headers={})
        return SimpleNamespace(status_code=204, text="", headers={})

    with patch("flow_app.telegram.httpx.post", side_effect=_fake_post):
        payload = _import_one(client, title="AC1 persist task")

    task_id = payload["created"][0]["id"]

    # A *new* session (not the request's session) must observe persisted rows.
    deliveries = _deliveries(client)
    assert len(deliveries) == 2, f"Expected 2 delivery rows, got {len(deliveries)}"

    by_provider = {d.provider: d for d in deliveries}
    assert "telegram" in by_provider
    assert "discord" in by_provider

    tg = by_provider["telegram"]
    assert tg.task_id == task_id
    assert tg.status == "success", f"Telegram status={tg.status}, expected success"
    assert tg.attempts == 1
    assert tg.last_response_code == 200

    dc = by_provider["discord"]
    assert dc.task_id == task_id
    assert dc.status == "success", f"Discord status={dc.status}, expected success"
    assert dc.attempts == 1
    assert dc.last_response_code == 204


# ---------------------------------------------------------------------------
# AC 2 — Rule action result + last_run_at persist in a new session
# ---------------------------------------------------------------------------

def test_ac2_rule_add_note_action_and_last_run_at_persist(client):
    """Create a task_created automation rule whose action adds a note.  After
    the import request closes, a new session must observe the added note AND
    the rule's last_run_at stamp."""
    board_events.clear()

    # Create an automation rule that adds a note on task_created.
    rule = client.post(
        "/api/automation-rules",
        json={
            "name": "AC2 auto-note",
            "trigger": "task_created",
            "conditions": json.dumps([{"field": "priority", "operator": "gte", "value": 40}]),
            "actions": json.dumps([{"type": "add_note", "text": "auto-added by rule"}]),
        },
    )
    assert rule.status_code == 201, rule.text
    rule_id = rule.json()["id"]

    payload = _import_one(client, title="AC2 rule task", priority=50)
    task_id = payload["created"][0]["id"]

    # New session: the note must be present on the task.
    with client.app.state.SessionLocal() as db:
        task = db.get(Task, task_id)
        assert task is not None, "Imported task not found in new session"
        note_texts = [n.body for n in task.notes]
        assert "auto-added by rule" in note_texts, (
            f"Rule-added note not persisted; notes={note_texts}"
        )

    # New session: the rule's last_run_at must be stamped.
    with client.app.state.SessionLocal() as db:
        from flow_app.models import AutomationRule
        rule_model = db.get(AutomationRule, rule_id)
        assert rule_model is not None
        assert rule_model.last_run_at is not None, (
            "rule.last_run_at was not persisted after post-commit effects"
        )


# ---------------------------------------------------------------------------
# AC 3 — Explicit post-commit transaction, no task re-creation/rollback
# ---------------------------------------------------------------------------

def test_ac3_post_commit_commit_does_not_recreate_or_rollback_tasks(client, monkeypatch):
    """The post-commit effect transaction must persist effect rows without
    re-creating or rolling back the already-committed imported tasks.  We
    verify by importing two tasks, confirming they persist, and confirming a
    second import of the same titles does not duplicate them."""
    board_events.clear()

    payload = _import_two(client, "AC3 task A", "AC3 task B")
    task_ids = {t["id"] for t in payload["created"]}
    assert len(task_ids) == 2

    # Tasks persist in a new session.
    with client.app.state.SessionLocal() as db:
        titles = sorted(t.title for t in db.query(Task).all() if t.id in task_ids)
    assert titles == ["AC3 task A", "AC3 task B"]

    # Re-importing the same titles must be detected as duplicates (not re-created).
    dup = _import_two(client, "AC3 task A", "AC3 task B")
    assert len(dup["created"]) == 0, "Tasks were re-created on second import"
    assert len(dup["skipped"]) == 2, "Duplicate detection failed"

    # Exactly 2 task_created board events (one per task, no re-emission).
    events = [e for e in board_events.since(0) if e.event == "task_created"]
    assert len(events) == 2, f"Expected 2 board events, got {len(events)}"


def test_ac3_post_commit_commit_failure_does_not_rollback_tasks(client, monkeypatch):
    """If the post-commit effect commit fails, the committed task batch must
    survive (it was persisted by the caller's earlier commit) — only the effect
    rows are rolled back."""
    from flow_app.routes import dependencies as deps_mod

    board_events.clear()

    # Make the post-commit commit fail by replacing _commit with one that
    # raises on the *second* call (first = task commit, second = effect commit).
    real_commit = deps_mod._commit
    call_count = {"n": 0}

    def flaky_commit(db):
        call_count["n"] += 1
        if call_count["n"] == 2:
            # Simulate effect-commit failure.
            db.rollback()
            raise RuntimeError("effect commit exploded")
        return real_commit(db)

    monkeypatch.setattr(deps_mod, "_commit", flaky_commit)

    payload = _import_one(client, title="AC3 commit-fail task")
    task_id = payload["created"][0]["id"]

    # The task must survive despite the effect commit failing.
    with client.app.state.SessionLocal() as db:
        task = db.get(Task, task_id)
        assert task is not None, "Task was rolled back when effect commit failed"
        assert task.title == "AC3 commit-fail task"

    # Board events are published before the effect commit, so they survive.
    events = [e for e in board_events.since(0) if e.event == "task_created"]
    assert len(events) == 1


# ---------------------------------------------------------------------------
# AC 4 — Provider raises before recording: task committed, one board event,
#        failure record exists, caplog has provider + task identity
# ---------------------------------------------------------------------------

def test_ac4_provider_raises_before_recording_persists_failure_and_logs(client, monkeypatch, caplog):
    """Inject a Telegram provider that raises before creating a delivery row.
    Assert: task stays committed, exactly one board event, a failure record
    (NotificationDelivery status=failed) exists, and caplog contains the
    provider name plus task identity."""
    from flow_app.routes import dependencies as deps_mod

    board_events.clear()

    class RaisingTelegram:
        def send(self, db, event, task, changes=None):
            raise RuntimeError("telegram provider crashed before recording")

    monkeypatch.setattr(deps_mod, "_telegram_notifier", RaisingTelegram())

    with caplog.at_level(logging.ERROR, logger="flow.services.task"):
        payload = _import_one(client, title="AC4 raising provider task")

    task_id = payload["created"][0]["id"]

    # Task stays committed.
    with client.app.state.SessionLocal() as db:
        task = db.get(Task, task_id)
        assert task is not None, "Task was rolled back by raising provider"
        assert task.title == "AC4 raising provider task"

    # Exactly one board event.
    events = [e for e in board_events.since(0) if e.event == "task_created"]
    assert len(events) == 1, f"Expected 1 board event, got {len(events)}"

    # A failure record must exist.
    deliveries = _deliveries(client)
    tg_failures = [d for d in deliveries if d.provider == "telegram" and d.status == "failed"]
    assert len(tg_failures) == 1, (
        f"Expected 1 telegram failure record, got {len(tg_failures)} (all: {[(d.provider, d.status) for d in deliveries]})"
    )
    failure = tg_failures[0]
    assert failure.task_id == task_id
    assert failure.attempts == 1
    assert "provider raised before recording" in (failure.last_response_body or "")
    assert "telegram provider crashed" in (failure.last_response_body or "")

    # caplog must contain provider name and task identity.
    log_text = caplog.text
    assert "telegram" in log_text, "caplog missing provider name 'telegram'"
    assert task_id in log_text, f"caplog missing task_id {task_id}"


def test_ac4_discord_provider_raises_before_recording_persists_failure(client, monkeypatch):
    """Same contract for the Discord provider."""
    from flow_app.routes import dependencies as deps_mod

    board_events.clear()

    class RaisingDiscord:
        def send(self, db, event, task, changes=None):
            raise RuntimeError("discord provider crashed before recording")

    monkeypatch.setattr(deps_mod, "_discord_notifier", RaisingDiscord())

    payload = _import_one(client, title="AC4 discord raising provider")
    task_id = payload["created"][0]["id"]

    with client.app.state.SessionLocal() as db:
        task = db.get(Task, task_id)
        assert task is not None

    deliveries = _deliveries(client)
    dc_failures = [d for d in deliveries if d.provider == "discord" and d.status == "failed"]
    assert len(dc_failures) == 1, (
        f"Expected 1 discord failure record, got {len(dc_failures)}"
    )
    assert dc_failures[0].task_id == task_id
    assert "discord provider crashed" in (dc_failures[0].last_response_body or "")


# ---------------------------------------------------------------------------
# AC 5 — Exact-once success calls + atomic task rollback on staging failure
# ---------------------------------------------------------------------------

def test_ac5_exact_once_success_calls(client, monkeypatch):
    """On a successful import, each provider's send() must be called exactly
    once per task (2 tasks = 2 calls each)."""
    from flow_app.routes import dependencies as deps_mod

    board_events.clear()
    calls = {"telegram": 0, "discord": 0, "rule": 0}

    class CountingTelegram:
        def send(self, db, event, task, changes=None):
            calls["telegram"] += 1

    class CountingDiscord:
        def send(self, db, event, task, changes=None):
            calls["discord"] += 1

    def counting_rule(session, trigger, task_id=None, data=None, actor=None, rule_id=None, dry_run=False):
        calls["rule"] += 1
        return []

    monkeypatch.setattr(deps_mod, "_telegram_notifier", CountingTelegram())
    monkeypatch.setattr(deps_mod, "_discord_notifier", CountingDiscord())
    monkeypatch.setattr(deps_mod, "emit_rule_event", counting_rule)

    _import_two(client, "AC5 once A", "AC5 once B")

    assert calls["telegram"] == 2, f"Telegram called {calls['telegram']}x, expected 2"
    assert calls["discord"] == 2, f"Discord called {calls['discord']}x, expected 2"
    assert calls["rule"] == 2, f"Rule emitter called {calls['rule']}x, expected 2"


def test_ac5_atomic_rollback_on_staging_failure(client, monkeypatch):
    """If staging raises, the batch is rolled back atomically: no tasks, no
    side effects, no board events."""
    from flow_app.realtime import board_events as _be
    from flow_app.services import task as task_service_mod

    _be.clear()
    side_effect_calls = {"n": 0}

    original_create = task_service_mod.create_task
    call_count = {"n": 0}

    def flaky_create(session, payload):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("staging failure on second task")
        return original_create(session, payload)

    monkeypatch.setattr(task_service_mod, "create_task", flaky_create)

    response = client.post(
        "/api/import/markdown/commit",
        json={
            "items": [
                {
                    "preview_id": "test-1",
                    "title": "Rollback A",
                    "status": "todo",
                    "priority": 50,
                    "project": "default",
                },
                {
                    "preview_id": "test-2",
                    "title": "Rollback B",
                    "status": "todo",
                    "priority": 50,
                    "project": "default",
                },
            ]
        },
    )
    assert response.status_code == 500, response.text

    with client.app.state.SessionLocal() as db:
        titles = [t.title for t in db.query(Task).all()]
    assert "Rollback A" not in titles
    assert "Rollback B" not in titles

    events = [e for e in _be.since(0) if e.event == "task_created"]
    assert len(events) == 0


def test_ac5_zero_pre_commit_side_effects(client, monkeypatch):
    """No provider or rule side effects may fire before the task batch commit.
    We force staging to fail and assert zero calls."""
    from flow_app.routes import dependencies as deps_mod
    from flow_app.services import task as task_service_mod

    calls = {"telegram": 0, "discord": 0, "rule": 0}

    class CountingTelegram:
        def send(self, db, event, task, changes=None):
            calls["telegram"] += 1

    class CountingDiscord:
        def send(self, db, event, task, changes=None):
            calls["discord"] += 1

    def counting_rule(session, trigger, task_id=None, data=None, actor=None, rule_id=None, dry_run=False):
        calls["rule"] += 1
        return []

    monkeypatch.setattr(deps_mod, "_telegram_notifier", CountingTelegram())
    monkeypatch.setattr(deps_mod, "_discord_notifier", CountingDiscord())
    monkeypatch.setattr(deps_mod, "emit_rule_event", counting_rule)

    original_create = task_service_mod.create_task
    n = {"i": 0}

    def fail_second(session, payload):
        n["i"] += 1
        if n["i"] == 2:
            raise RuntimeError("staging boom")
        return original_create(session, payload)

    monkeypatch.setattr(task_service_mod, "create_task", fail_second)

    response = client.post(
        "/api/import/markdown/commit",
        json={
            "items": [
                {"preview_id": "a", "title": "Pre A", "status": "todo", "priority": 50, "project": "default"},
                {"preview_id": "b", "title": "Pre B", "status": "todo", "priority": 50, "project": "default"},
            ]
        },
    )
    assert response.status_code == 500

    assert calls["telegram"] == 0, f"Telegram fired pre-commit: {calls['telegram']}"
    assert calls["discord"] == 0, f"Discord fired pre-commit: {calls['discord']}"
    assert calls["rule"] == 0, f"Rule fired pre-commit: {calls['rule']}"