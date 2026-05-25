from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import FlowSettings, get_settings


class Base(DeclarativeBase):
    pass


def default_database_url() -> str:
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings.database_url


def _sqlite_journal_mode_pragma(value: str) -> str:
    mode = value.strip().upper()
    allowed_modes = {"DELETE", "TRUNCATE", "PERSIST", "MEMORY", "WAL", "OFF"}
    if mode not in allowed_modes:
        raise ValueError(f"Unsupported SQLite journal mode: {value!r}")
    return mode


def build_engine(database_url: str, settings: FlowSettings | None = None):
    settings = settings or get_settings()
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    engine = create_engine(database_url, connect_args=connect_args, future=True)
    if database_url.startswith("sqlite"):
        journal_mode = _sqlite_journal_mode_pragma(settings.sqlite_journal_mode)
        busy_timeout_ms = settings.sqlite_busy_timeout_ms

        def _set_sqlite_pragmas(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute(f"PRAGMA journal_mode={journal_mode}")
                cursor.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
            finally:
                cursor.close()

        event.listen(engine, "connect", _set_sqlite_pragmas)
    return engine


def build_session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
