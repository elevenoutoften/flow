from __future__ import annotations

from dataclasses import replace
from unittest.mock import Mock

import pytest

from flow_app.config import get_settings
from flow_app.database import build_engine


def test_build_engine_sets_wal_and_busy_timeout_for_sqlite(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'flow.sqlite'}")

    with engine.connect() as connection:
        journal_mode = connection.exec_driver_sql("PRAGMA journal_mode").scalar()
        busy_timeout = connection.exec_driver_sql("PRAGMA busy_timeout").scalar()

    engine.dispose()

    assert journal_mode == "wal"
    assert busy_timeout == 5000


def test_build_engine_respects_sqlite_settings_override(tmp_path):
    settings = replace(get_settings(), sqlite_journal_mode="DELETE", sqlite_busy_timeout_ms=250)
    engine = build_engine(f"sqlite:///{tmp_path / 'flow.sqlite'}", settings=settings)

    with engine.connect() as connection:
        journal_mode = connection.exec_driver_sql("PRAGMA journal_mode").scalar()
        busy_timeout = connection.exec_driver_sql("PRAGMA busy_timeout").scalar()

    engine.dispose()

    assert journal_mode == "delete"
    assert busy_timeout == 250


def test_build_engine_rejects_unknown_sqlite_journal_mode(tmp_path):
    settings = replace(get_settings(), sqlite_journal_mode="INVALID")

    with pytest.raises(ValueError, match="Unsupported SQLite journal mode"):
        build_engine(f"sqlite:///{tmp_path / 'flow.sqlite'}", settings=settings)


def test_build_engine_does_not_apply_sqlite_pragmas_to_non_sqlite(monkeypatch):
    fake_engine = object()
    create_engine = Mock(return_value=fake_engine)
    listen = Mock()

    monkeypatch.setattr("flow_app.database.create_engine", create_engine)
    monkeypatch.setattr("flow_app.database.event.listen", listen)

    engine = build_engine("postgresql://user:pass@localhost/flow")

    assert engine is fake_engine
    create_engine.assert_called_once_with("postgresql://user:pass@localhost/flow", connect_args={}, future=True)
    listen.assert_not_called()


def test_get_settings_reads_sqlite_env_overrides(monkeypatch):
    monkeypatch.setenv("FLOW_SQLITE_JOURNAL_MODE", "DELETE")
    monkeypatch.setenv("FLOW_SQLITE_BUSY_TIMEOUT_MS", "1234")

    settings = get_settings()

    assert settings.sqlite_journal_mode == "DELETE"
    assert settings.sqlite_busy_timeout_ms == 1234
