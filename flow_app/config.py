from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path


STATIC_DIR = Path(__file__).parent / "static"


def _compute_asset_version() -> str:
    """Content hash of static files for cache busting."""
    digest = hashlib.sha256()
    for path in sorted(STATIC_DIR.rglob("*")):
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()[:8]


FLOW_VERSION = _compute_asset_version()


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
    session_secret: str
    session_cookie_secure: bool
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    @property
    def session_cookie_enabled(self) -> bool:
        return bool(self.session_secret)


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
        session_secret=_env("FLOW_SESSION_SECRET", ""),
        session_cookie_secure=_env_bool("FLOW_SESSION_COOKIE_SECURE", default=False),
        telegram_bot_token=_env("FLOW_TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=_env("FLOW_TELEGRAM_CHAT_ID", ""),
    )
    return _settings_cache


def reset_settings_cache() -> None:
    global _settings_cache
    _settings_cache = None


def default_project() -> str:
    return get_settings().default_project
