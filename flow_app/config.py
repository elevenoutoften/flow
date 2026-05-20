from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


FLOW_VERSION = "0.1.0"


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip() or default


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


@dataclass(frozen=True)
class FlowSettings:
    data_dir: Path
    database_url: str
    default_project: str
    host: str
    port: int
    debug: bool
    cors_origins: list[str]
    theme: str
    trusted_headers: bool


_settings_cache: FlowSettings | None = None


def get_settings() -> FlowSettings:
    global _settings_cache
    if _settings_cache is not None:
        return _settings_cache
    data_dir = Path(_env("FLOW_DATA_DIR", "./data")).expanduser()
    configured_database_url = os.environ.get("FLOW_DATABASE_URL", "").strip()
    database_url = configured_database_url or f"sqlite:///{data_dir / 'flow.sqlite'}"
    cors_raw = _env("FLOW_CORS_ORIGINS", "")
    cors_origins = [origin.strip() for origin in cors_raw.split(",") if origin.strip()] if cors_raw else []
    _settings_cache = FlowSettings(
        data_dir=data_dir,
        database_url=database_url,
        default_project=_env("FLOW_DEFAULT_PROJECT", "default"),
        host=_env("FLOW_HOST", "0.0.0.0"),
        port=_env_int("FLOW_PORT", 8100),
        debug=_env_bool("FLOW_DEBUG"),
        cors_origins=cors_origins,
        theme=_env("FLOW_THEME", "neutral"),
        trusted_headers=_env_bool("FLOW_TRUSTED_HEADERS", default=False),
    )
    return _settings_cache


def reset_settings_cache() -> None:
    global _settings_cache
    _settings_cache = None


def default_project() -> str:
    return get_settings().default_project
