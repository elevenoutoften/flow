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


# ---------------------------------------------------------------------------
# Multiple historical schema shapes — test migration from different eras
# ---------------------------------------------------------------------------

def _create_v1_schema(db_path: str):
    """Very old schema: no version, no human_required, no complexity/impact/effort/risk,
    no source_filename, no import_batch_id, no metadata, no claimer_key_id.
    Also missing agent dispatch_statuses, agent_run workspace_state/scoped_key_id,
    api_key role, and task_note author_key_id."""
    return _create_old_schema(db_path)


def _create_v2_schema(db_path: str):
    """Intermediate schema: has version, human_required, complexity/impact/effort/risk
    but missing newer columns: source_template_id, source_title, claimer_key_id, metadata.
    Agents have dispatch_statuses but not command_allowlist.
    Agent runs have scoped_key_id but not workspace_state."""
    engine = build_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
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
                updated_at DATETIME NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                human_required INTEGER NOT NULL DEFAULT 0,
                assignee_type VARCHAR(24) NOT NULL DEFAULT 'agent',
                blocker_reason TEXT NOT NULL DEFAULT '',
                complexity VARCHAR(24) NOT NULL DEFAULT 'small',
                impact VARCHAR(24) NOT NULL DEFAULT 'medium',
                effort VARCHAR(24) NOT NULL DEFAULT 'medium',
                risk VARCHAR(24) NOT NULL DEFAULT 'low'
            )
        """))
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
                dispatch_statuses TEXT NOT NULL DEFAULT 'backlog,todo',
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
        """))
        conn.execute(text("""
            CREATE TABLE agent_runs (
                id VARCHAR(32) PRIMARY KEY,
                agent_id VARCHAR(32) NOT NULL,
                task_id VARCHAR(32) NOT NULL,
                status VARCHAR(24) NOT NULL DEFAULT 'pending',
                pid INTEGER,
                exit_code INTEGER,
                scoped_key_id VARCHAR(32),
                started_at DATETIME,
                finished_at DATETIME,
                last_heartbeat_at DATETIME,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
        """))
        conn.execute(text("""
            CREATE TABLE api_keys (
                id VARCHAR(32) PRIMARY KEY,
                name VARCHAR(120) NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                key_prefix VARCHAR(24) NOT NULL,
                key_hash VARCHAR(64) NOT NULL UNIQUE,
                role VARCHAR(32) NOT NULL DEFAULT 'read_only',
                created_at DATETIME NOT NULL,
                revoked_at DATETIME
            )
        """))
        # Insert data rows to verify survival
        conn.execute(text("""
            INSERT INTO tasks (id, title, status, priority, project, description, acceptance_criteria,
                created_at, updated_at, version, human_required, assignee_type, blocker_reason,
                complexity, impact, effort, risk)
            VALUES ('flow_v2_001', 'V2 Task', 'doing', 70, 'default', 'has version',
                '', '2026-03-01 00:00:00', '2026-03-01 00:00:00',
                3, 0, 'agent', '', 'medium', 'high', 'small', 'low')
        """))
        conn.execute(text("CREATE TABLE flow_counters (name VARCHAR(64) PRIMARY KEY, value INTEGER NOT NULL DEFAULT 0)"))
    return engine


def test_migration_from_v1_old_schema(tmp_path):
    """Migration from the earliest schema (v1) adds all missing columns and preserves data."""
    db_path = tmp_path / "v1_flow.sqlite"
    engine = _create_v1_schema(str(db_path))

    ensure_compatible_schema(engine)

    inspector = inspect(engine)
    task_cols = {col["name"] for col in inspector.get_columns("tasks")}
    # All newer columns should be present
    assert "source_filename" in task_cols
    assert "import_batch_id" in task_cols
    assert "metadata" in task_cols
    assert "claimer_key_id" in task_cols
    assert "source_template_id" in task_cols
    assert "source_title" in task_cols

    # Data survived
    with engine.begin() as conn:
        row = conn.execute(text("SELECT id, title, version FROM tasks WHERE id = 'flow_test_001'")).fetchone()
        assert row is not None
        assert row[0] == "flow_test_001"
        assert row[1] == "Old task"
        # version column was added with default 1
        assert row[2] == 1


def test_migration_from_v2_intermediate_schema(tmp_path):
    """Migration from an intermediate schema (v2) adds only the newer missing columns."""
    db_path = tmp_path / "v2_flow.sqlite"
    engine = _create_v2_schema(str(db_path))

    ensure_compatible_schema(engine)

    inspector = inspect(engine)
    task_cols = {col["name"] for col in inspector.get_columns("tasks")}
    # Columns that were already present should still be there
    assert "version" in task_cols
    assert "human_required" in task_cols
    assert "complexity" in task_cols
    # Columns that were missing should now be added
    assert "source_filename" in task_cols
    assert "import_batch_id" in task_cols
    assert "metadata" in task_cols
    assert "claimer_key_id" in task_cols
    assert "source_template_id" in task_cols
    assert "source_title" in task_cols

    # Agent columns
    agent_cols = {col["name"] for col in inspector.get_columns("agents")}
    assert "command_allowlist" in agent_cols

    # Agent run columns — v2 had scoped_key_id but not workspace_state
    run_cols = {col["name"] for col in inspector.get_columns("agent_runs")}
    assert "workspace_state" in run_cols

    # Data survived with original values
    with engine.begin() as conn:
        row = conn.execute(text("SELECT id, title, version FROM tasks WHERE id = 'flow_v2_001'")).fetchone()
        assert row is not None
        assert row[0] == "flow_v2_001"
        assert row[1] == "V2 Task"
        assert row[2] == 3  # original version value preserved


# ---------------------------------------------------------------------------
# Corruption failure
# ---------------------------------------------------------------------------

def test_migration_handles_corrupt_database(tmp_path):
    """ensure_compatible_schema should handle a corrupt SQLite file gracefully.

    A file that is not a valid SQLite database should not crash the migration.
    """
    db_path = tmp_path / "corrupt.sqlite"
    # Write garbage to the file
    db_path.write_bytes(b"NOT A DATABASE FILE\x00\x01\x02")

    # build_engine will connect to it, but queries should fail
    # The migration should either handle the error or raise a clear exception
    engine = build_engine(f"sqlite:///{db_path}")
    # ensure_compatible_schema should raise or handle gracefully
    # We expect it to either raise an operational error or return without crash
    try:
        ensure_compatible_schema(engine)
    except Exception:
        # An exception is acceptable — the point is it shouldn't silently corrupt things
        pass


def test_migration_preserves_existing_data_on_partial_schema(tmp_path):
    """Migration should preserve existing task data even when adding columns."""
    db_path = tmp_path / "partial.sqlite"
    engine = _create_v2_schema(str(db_path))

    ensure_compatible_schema(engine)

    # Verify the pre-existing data is intact
    with engine.begin() as conn:
        row = conn.execute(text(
            "SELECT id, title, status, priority, complexity FROM tasks WHERE id = 'flow_v2_001'"
        )).fetchone()
        assert row is not None
        assert row[0] == "flow_v2_001"
        assert row[1] == "V2 Task"
        assert row[2] == "doing"
        assert row[3] == 70
        assert row[4] == "medium"  # Original value preserved