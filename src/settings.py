"""Load platform secrets from .env and config from DB collections."""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
PROFILE_DIR = DATA_DIR / "profile"
APPLICATIONS_DIR = DATA_DIR / "applications"
DB_PATH = DATA_DIR / "jobs.db"

SESSION_SECRET = os.getenv("SESSION_SECRET") or secrets.token_hex(32)
OAUTH_REDIRECT_BASE = os.getenv("OAUTH_REDIRECT_BASE", "http://127.0.0.1:8080").rstrip("/")
GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET", "")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
APP_BASE_URL = os.getenv("APP_BASE_URL", OAUTH_REDIRECT_BASE).rstrip("/")
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USER or "noreply@localhost")
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").lower() in ("1", "true", "yes")
PASSWORD_RESET_DEV = os.getenv("PASSWORD_RESET_DEV", "").lower() in ("1", "true", "yes")
VENDOR_HIRING_AGENT = ROOT / "vendor" / "hiring_agent"
PROMPTS_DIR = ROOT / "prompts"
TEMPLATES_DIR = ROOT / "templates"

load_dotenv(ROOT / ".env")

_config_cache: dict[int | None, dict[str, Any]] = {}
_sources_cache: dict[int | None, list[dict[str, Any]]] = {}


def user_data_dir(user_id: int) -> Path:
    return DATA_DIR / "users" / str(user_id)


def user_profile_dir(user_id: int) -> Path:
    return user_data_dir(user_id) / "profile"


def user_applications_dir(user_id: int) -> Path:
    return user_data_dir(user_id) / "applications"


def applications_dir(user_id: int | None = None) -> Path:
    from src.tenant import resolve_user_id

    return user_applications_dir(resolve_user_id(user_id))


def _cache_key() -> int | None:
    from src.tenant import get_tenant_user_id

    return get_tenant_user_id()


def get_config() -> dict[str, Any]:
    from src.config_store import get_merged_config
    from src.keywords import migrate_legacy_keywords
    from src.config_store import get_collection, save_collection, SCOPE_PLATFORM, PLATFORM_SCOPE_ID

    key = _cache_key()
    if key in _config_cache:
        return _config_cache[key]

    cfg = get_merged_config(key)
    migrated = migrate_legacy_keywords(cfg.get("keywords"))
    if migrated and migrated != cfg.get("keywords"):
        uid = key
        if uid is not None:
            save_collection("keywords", migrated, user_id=uid)
        else:
            save_collection("keywords", migrated, scope=SCOPE_PLATFORM, scope_id=PLATFORM_SCOPE_ID)
        cfg["keywords"] = migrated

    _config_cache[key] = cfg
    return cfg


def get_sources() -> list[dict[str, Any]]:
    from src.config_store import list_sources
    from src.source_discovery import merge_discovered_sources

    key = _cache_key()
    if key in _sources_cache:
        return _sources_cache[key]

    static = [s for s in list_sources(key) if s.get("enabled", True)]
    cfg = get_config()
    if cfg.get("ever_jobs_enabled"):
        for s in static:
            if s.get("adapter") == "ever_jobs":
                s["enabled"] = True
    result = merge_discovered_sources(static)
    _sources_cache[key] = result
    return result


def reload_sources() -> list[dict[str, Any]]:
    key = _cache_key()
    _sources_cache.pop(key, None)
    return get_sources()


def all_keywords() -> list[str]:
    from src.keywords import all_keywords as _all_keywords

    return _all_keywords()


def ensure_dirs(user_id: int | None = None) -> None:
    dirs = [DATA_DIR, PROFILE_DIR, APPLICATIONS_DIR, TEMPLATES_DIR]
    if user_id is not None:
        dirs.extend([user_data_dir(user_id), user_profile_dir(user_id), user_applications_dir(user_id)])
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)


def reload_config() -> dict[str, Any]:
    key = _cache_key()
    _config_cache.pop(key, None)
    _sources_cache.pop(key, None)
    return get_config()


def load_sources_raw() -> list[dict[str, Any]]:
    from src.config_store import list_sources

    return list_sources()


def smtp_configured() -> bool:
    return bool(SMTP_HOST and SMTP_FROM)


def password_reset_show_link() -> bool:
    if PASSWORD_RESET_DEV:
        return True
    return not smtp_configured()
