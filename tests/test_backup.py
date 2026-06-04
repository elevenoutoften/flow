"""Tests for flow_app.backup — SQLite backup and restore CLI."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from flow_app.backup import cmd_backup, cmd_restore, _db_path, _lock_marker_path


def _make_test_db(path: Path) -> None:
    """Create a minimal Flow-like SQLite database for testing."""
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE IF NOT EXISTS flow_counters (name VARCHAR(64) PRIMARY KEY, value INTEGER NOT NULL DEFAULT 0)")
    conn.execute("INSERT OR REPLACE INTO flow_counters (name, value) VALUES ('test', 1)")
    conn.commit()
    conn.close()


def test_backup_creates_file(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOW_DATA_DIR", str(tmp_path))
    from flow_app.config import reset_settings_cache
    reset_settings_cache()

    db_path = tmp_path / "flow.sqlite"
    _make_test_db(db_path)

    exit_code = cmd_backup(output_dir=tmp_path / "backups")
    assert exit_code == 0

    backup_dir = tmp_path / "backups"
    backups = list(backup_dir.glob("flow-backup-*.db"))
    assert len(backups) == 1

    # Verify backup content
    conn = sqlite3.connect(str(backups[0]))
    result = conn.execute("SELECT value FROM flow_counters WHERE name = 'test'").fetchone()
    conn.close()
    assert result[0] == 1

    reset_settings_cache()


def test_backup_custom_output_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOW_DATA_DIR", str(tmp_path))
    from flow_app.config import reset_settings_cache
    reset_settings_cache()

    db_path = tmp_path / "flow.sqlite"
    _make_test_db(db_path)

    custom_dir = tmp_path / "custom-backups"
    exit_code = cmd_backup(output_dir=custom_dir)
    assert exit_code == 0

    backups = list(custom_dir.glob("flow-backup-*.db"))
    assert len(backups) == 1
    reset_settings_cache()


def test_restore_dry_run_validates(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOW_DATA_DIR", str(tmp_path))
    from flow_app.config import reset_settings_cache
    reset_settings_cache()

    db_path = tmp_path / "flow.sqlite"
    _make_test_db(db_path)

    # Create a valid backup first
    backup_dir = tmp_path / "backups"
    cmd_backup(output_dir=backup_dir)
    backup_file = list(backup_dir.glob("flow-backup-*.db"))[0]

    # Modify the live db to know if restore actually happened
    conn = sqlite3.connect(str(db_path))
    conn.execute("UPDATE flow_counters SET value = 999 WHERE name = 'test'")
    conn.commit()
    conn.close()

    # Dry run — should NOT overwrite
    exit_code = cmd_restore(backup_file, dry_run=True)
    assert exit_code == 0

    # Verify live db still has modified value
    conn = sqlite3.connect(str(db_path))
    result = conn.execute("SELECT value FROM flow_counters WHERE name = 'test'").fetchone()
    conn.close()
    assert result[0] == 999
    reset_settings_cache()


def test_restore_dry_run_corrupt_file(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOW_DATA_DIR", str(tmp_path))
    from flow_app.config import reset_settings_cache
    reset_settings_cache()

    corrupt = tmp_path / "corrupt.db"
    corrupt.write_text("this is not a sqlite file", encoding="utf-8")

    exit_code = cmd_restore(corrupt, dry_run=True)
    assert exit_code == 1
    reset_settings_cache()


def test_restore_actually_restores(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOW_DATA_DIR", str(tmp_path))
    from flow_app.config import reset_settings_cache
    reset_settings_cache()

    db_path = tmp_path / "flow.sqlite"
    _make_test_db(db_path)

    backup_dir = tmp_path / "backups"
    cmd_backup(output_dir=backup_dir)
    backup_file = list(backup_dir.glob("flow-backup-*.db"))[0]

    # Modify the live db
    conn = sqlite3.connect(str(db_path))
    conn.execute("UPDATE flow_counters SET value = 999 WHERE name = 'test'")
    conn.commit()
    conn.close()

    # Restore
    exit_code = cmd_restore(backup_file, dry_run=False)
    assert exit_code == 0

    # Verify restored value
    conn = sqlite3.connect(str(db_path))
    result = conn.execute("SELECT value FROM flow_counters WHERE name = 'test'").fetchone()
    conn.close()
    assert result[0] == 1
    reset_settings_cache()


def test_restore_refuses_if_lock_exists(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOW_DATA_DIR", str(tmp_path))
    from flow_app.config import reset_settings_cache
    reset_settings_cache()

    db_path = tmp_path / "flow.sqlite"
    _make_test_db(db_path)

    backup_dir = tmp_path / "backups"
    cmd_backup(output_dir=backup_dir)
    backup_file = list(backup_dir.glob("flow-backup-*.db"))[0]

    # Create lock marker
    _lock_marker_path().touch()

    try:
        exit_code = cmd_restore(backup_file, dry_run=False)
        assert exit_code == 1
    finally:
        lock = _lock_marker_path()
        if lock.exists():
            lock.unlink()
        reset_settings_cache()