"""Tests for schema migration upgrades from old database schemas."""
from __future__ import annotations

from sqlalchemy import inspect, text

from flow_app.database import build_engine
from flow_app.migration import ensure_compatible_schema


def _create_old_schema(db_path: str):
    """Create an old-schema SQLite DB missing columns that migration should add."""
    engine = build_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        # Create a minimal old tasks table without newer columns
        conn.execute(text("""
            CREATE TABLE tasks (
                id VARCHAR(32) PRIMARY KEY,
                title VARCHAR(240) NOT NULL,
                status VARCHAR(24) NOT NULL,
                priority INTEGER NOT NULL DEFAULT 50,
                project VARCHAR(120) NOT NULL DEFAULT 'default',
                assignee VARCHAR(120),
                description TEXT NOT NULL DEFAULT '',
                acceptance_criteria TEXT NOT NULL DEFAULT '',
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
        """))
        # Create old task_notes without author_key_id
        conn.execute(text("""
            CREATE TABLE task_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id VARCHAR(32) NOT NULL,
                body TEXT NOT NULL,
                author VARCHAR(120),
                created_at DATETIME NOT NULL,
                FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
            )
        """))
        # Create old agents without dispatch_statuses or command_allowlist
        conn.execute(text("""
            CREATE TABLE agents (
                id VARCHAR(32) PRIMARY KEY,
                name VARCHAR(180) NOT NULL UNIQUE,
                description TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                agent_type VARCHAR(24) NOT NULL DEFAULT 'cli',
                capabilities TEXT NOT NULL DEFAULT '',
                command TEXT NOT NULL DEFAULT '',
                env_allowlist TEXT NOT NULL DEFAULT '',
                working_directory VARCHAR(500) NOT NULL DEFAULT '',
                max_concurrency INTEGER NOT NULL DEFAULT 1,
                heartbeat_timeout_seconds INTEGER NOT NULL DEFAULT 300,
                stale_claim_timeout_seconds INTEGER NOT NULL DEFAULT 600,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
        """))
        # Create old agent_runs without workspace_state and scoped_key_id
        conn.execute(text("""
            CREATE TABLE agent_runs (
                id VARCHAR(32) PRIMARY KEY,
                agent_id VARCHAR(32) NOT NULL,
                task_id VARCHAR(32) NOT NULL,
                status VARCHAR(24) NOT NULL DEFAULT 'pending',
                pid INTEGER,
                exit_code INTEGER,
                started_at DATETIME,
                finished_at DATETIME,
                last_heartbeat_at DATETIME,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
        """))
        # Old api_keys without role
        conn.execute(text("""
            CREATE TABLE api_keys (
                id VARCHAR(32) PRIMARY KEY,
                name VARCHAR(120) NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                key_prefix VARCHAR(24) NOT NULL,
                key_hash VARCHAR(64) NOT NULL UNIQUE,
                created_at DATETIME NOT NULL,
                revoked_at DATETIME
            )
        """))
        # Insert a row so we can verify data survives migration
        conn.execute(text("""
            INSERT INTO tasks (id, title, status, priority, project, description, acceptance_criteria, created_at, updated_at)
            VALUES ('flow_test_001', 'Old task', 'todo', 50, 'default', 'test', '', '2026-01-01 00:00:00', '2026-01-01 00:00:00')
        """))
        conn.execute(text("CREATE TABLE flow_counters (name VARCHAR(64) PRIMARY KEY, value INTEGER NOT NULL DEFAULT 0)"))
    return engine


def test_migration_adds_missing_task_columns(tmp_path):
    """ensure_compatible_schema adds columns missing from an old schema."""
    db_path = tmp_path / "old_flow.sqlite"
    engine = _create_old_schema(str(db_path))

    # Run migration
    ensure_compatible_schema(engine)

    inspector = inspect(engine)
    task_cols = {col["name"] for col in inspector.get_columns("tasks")}
    assert "version" in task_cols
    assert "human_required" in task_cols
    assert "assignee_type" in task_cols
    assert "blocker_reason" in task_cols
    assert "complexity" in task_cols
    assert "impact" in task_cols
    assert "effort" in task_cols
    assert "risk" in task_cols
    assert "source_filename" in task_cols
    assert "import_batch_id" in task_cols
    assert "metadata" in task_cols
    assert "claimer_key_id" in task_cols

    # Data survives migration
    with engine.begin() as conn:
        row = conn.execute(text("SELECT id, title FROM tasks WHERE id = 'flow_test_001'")).fetchone()
        assert row is not None
        assert row[0] == "flow_test_001"
        assert row[1] == "Old task"


def test_migration_adds_missing_agent_columns(tmp_path):
    """ensure_compatible_schema adds dispatch_statuses and command_allowlist to agents."""
    db_path = tmp_path / "old_agents.sqlite"
    engine = _create_old_schema(str(db_path))

    ensure_compatible_schema(engine)

    inspector = inspect(engine)
    agent_cols = {col["name"] for col in inspector.get_columns("agents")}
    assert "dispatch_statuses" in agent_cols
    assert "command_allowlist" in agent_cols


def test_migration_adds_missing_agent_run_columns(tmp_path):
    """ensure_compatible_schema adds workspace_state and scoped_key_id to agent_runs."""
    db_path = tmp_path / "old_runs.sqlite"
    engine = _create_old_schema(str(db_path))

    ensure_compatible_schema(engine)

    inspector = inspect(engine)
    run_cols = {col["name"] for col in inspector.get_columns("agent_runs")}
    assert "workspace_state" in run_cols
    assert "scoped_key_id" in run_cols


def test_migration_adds_role_to_api_keys(tmp_path):
    """ensure_compatible_schema adds role column to api_keys."""
    db_path = tmp_path / "old_keys.sqlite"
    engine = _create_old_schema(str(db_path))

    ensure_compatible_schema(engine)

    inspector = inspect(engine)
    key_cols = {col["name"] for col in inspector.get_columns("api_keys")}
    assert "role" in key_cols


def test_migration_adds_author_key_id_to_task_notes(tmp_path):
    """ensure_compatible_schema adds author_key_id to task_notes."""
    db_path = tmp_path / "old_notes.sqlite"
    engine = _create_old_schema(str(db_path))

    ensure_compatible_schema(engine)

    inspector = inspect(engine)
    note_cols = {col["name"] for col in inspector.get_columns("task_notes")}
    assert "author_key_id" in note_cols


def test_migration_is_idempotent(tmp_path):
    """Running migration twice on a fresh schema should not error."""
    from flow_app.database import Base, build_engine
    engine = build_engine(f"sqlite:///{tmp_path / 'fresh.sqlite'}")
    Base.metadata.create_all(bind=engine)

    # Run migration twice
    ensure_compatible_schema(engine)
    ensure_compatible_schema(engine)

    # Everything still works
    inspector = inspect(engine)
    assert "tasks" in inspector.get_table_names()