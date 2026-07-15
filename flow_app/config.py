from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path

from cryptography.fernet import Fernet

from .secrets_resolver import resolve_secret


STATIC_DIR = Path(__file__).parent / "static"


def _compute_asset_version() -> str:
    """Content hash of static files for cache busting."""
    digest = hashlib.sha256()
    for path in sorted(STATIC_DIR.rglob("*")):
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()[:8]


FLOW_VERSION = _compute_asset_version()
DEFAULT_PAGE_LIMIT = 100
MAX_PAGE_LIMIT = 500


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


def _env_optional(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def _validate_fernet_key(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        Fernet(value.encode("utf-8"))
    except Exception as exc:
        raise ValueError(f"{name_for_webhook_key()} must be a valid Fernet key") from exc
    return value


def _resolve_if_reference(value: str | None) -> str | None:
    if value is None:
        return None
    return resolve_secret(value)


def name_for_webhook_key() -> str:
    return "FLOW_WEBHOOK_ENCRYPTION_KEY"


@dataclass(frozen=True)
class FlowSettings:
    data_dir: Path
    database_url: str
    default_project: str
    sqlite_journal_mode: str
    sqlite_busy_timeout_ms: int
    host: str
    port: int
    debug: bool
    cors_origins: list[str]
    theme: str
    trusted_headers: bool
    session_secret: str
    session_cookie_secure: bool
    public_url: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    discord_webhook_url: str = ""
    webhook_encryption_key: str | None = None
    max_webhook_payload_bytes: int = 65536
    max_webhook_response_bytes: int = 4096
    max_webhook_delivery_age_days: int = 30
    audit_enabled: bool = True
    rate_limit_enabled: bool = True
    rate_limit_key_creation: int = 10
    rate_limit_mutations: int = 120
    public_board: bool = False

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
        sqlite_journal_mode=_env("FLOW_SQLITE_JOURNAL_MODE", "WAL"),
        sqlite_busy_timeout_ms=_env_int("FLOW_SQLITE_BUSY_TIMEOUT_MS", 5000),
        host=_env("FLOW_HOST", "0.0.0.0"),
        port=_env_int("FLOW_PORT", 8100),
        debug=_env_bool("FLOW_DEBUG"),
        cors_origins=cors_origins,
        theme=_env("FLOW_THEME", "love"),
        trusted_headers=_env_bool("FLOW_TRUSTED_HEADERS", default=False),
        session_secret=_env("FLOW_SESSION_SECRET", ""),
        session_cookie_secure=_env_bool("FLOW_SESSION_COOKIE_SECURE", default=False),
        public_url=_env("FLOW_PUBLIC_URL", "").rstrip("/"),
        telegram_bot_token=_resolve_if_reference(_env("FLOW_TELEGRAM_BOT_TOKEN", "")) or "",
        telegram_chat_id=_env("FLOW_TELEGRAM_CHAT_ID", ""),
        discord_webhook_url=_resolve_if_reference(_env("FLOW_DISCORD_WEBHOOK_URL", "")) or "",
        webhook_encryption_key=_validate_fernet_key(_resolve_if_reference(_env_optional("FLOW_WEBHOOK_ENCRYPTION_KEY"))),
        max_webhook_payload_bytes=_env_int("FLOW_MAX_WEBHOOK_PAYLOAD_BYTES", 65536),
        max_webhook_response_bytes=_env_int("FLOW_MAX_WEBHOOK_RESPONSE_BYTES", 4096),
        max_webhook_delivery_age_days=_env_int("FLOW_MAX_WEBHOOK_DELIVERY_AGE_DAYS", 30),
        audit_enabled=_env_bool("FLOW_AUDIT_ENABLED", default=True),
        rate_limit_enabled=_env_bool("FLOW_RATE_LIMIT_ENABLED", default=True),
        rate_limit_key_creation=_env_int("FLOW_RATE_LIMIT_KEY_CREATION", 10),
        rate_limit_mutations=_env_int("FLOW_RATE_LIMIT_MUTATIONS", 120),
        public_board=_env_bool("FLOW_PUBLIC_BOARD", default=False),
    )
    return _settings_cache


def reset_settings_cache() -> None:
    global _settings_cache
    _settings_cache = None


def default_project() -> str:
    return get_settings().default_project
