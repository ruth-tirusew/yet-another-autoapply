"""One-time import of config.yaml and sources.yaml into DB collections."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from src.config_store import (
    COLLECTION_NAMES,
    DEFAULT_COLLECTIONS,
    PLATFORM_SCOPE_ID,
    PLATFORM_SOURCES_USER_ID,
    SCOPE_PLATFORM,
)
from src import settings

_LEGACY_TOP_LEVEL = {"stale_job_days", "auto_apply_sources", "max_applications_per_day"}


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _normalize_config_yaml(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Map config.yaml top-level keys into collection rows."""
    collections: dict[str, dict[str, Any]] = {}

    for name in COLLECTION_NAMES:
        if name in raw and isinstance(raw[name], dict):
            collections[name] = raw[name]

    if "models" in raw and isinstance(raw["models"], dict):
        models = dict(raw["models"])
        models.setdefault("provider", "ollama")
        models.setdefault("ollama_host", "http://localhost:11434")
        collections["models"] = models

    if "stale_job_days" in raw:
        pipeline = dict(collections.get("pipeline") or DEFAULT_COLLECTIONS["pipeline"])
        pipeline["stale_days"] = raw["stale_job_days"]
        collections["pipeline"] = pipeline

    if "auto_apply_sources" in raw:
        auto = dict(collections.get("auto_apply") or DEFAULT_COLLECTIONS["auto_apply"])
        auto["allowlist"] = raw["auto_apply_sources"]
        collections["auto_apply"] = auto

    if "max_applications_per_day" in raw:
        auto = dict(collections.get("auto_apply") or DEFAULT_COLLECTIONS["auto_apply"])
        auto["max_per_day"] = raw["max_applications_per_day"]
        collections["auto_apply"] = auto

    for name in COLLECTION_NAMES:
        if name not in collections:
            collections[name] = DEFAULT_COLLECTIONS.get(name, {})

    return collections


def import_yaml_to_db(*, rename_after: bool = True) -> dict[str, Any]:
    """Import YAML files into platform collections and sources."""
    from src.db import (
        count_config_collections,
        list_user_ids,
        save_config_collection,
        save_user_sources_bulk,
    )
    summary: dict[str, Any] = {"collections": 0, "sources": 0, "users_seeded": 0}

    config_path = settings.ROOT / "config.yaml"
    sources_path = settings.ROOT / "config" / "sources.yaml"

    if count_config_collections(SCOPE_PLATFORM, PLATFORM_SCOPE_ID) == 0:
        raw = _load_yaml(config_path)
        if raw:
            collections = _normalize_config_yaml(raw)
            for name, data in collections.items():
                save_config_collection(SCOPE_PLATFORM, PLATFORM_SCOPE_ID, name, data)
                summary["collections"] += 1
        else:
            from src.config_store import seed_platform_defaults

            seed_platform_defaults()
            summary["collections"] = len(COLLECTION_NAMES)

    sources_raw = _load_yaml(sources_path)
    sources = list(sources_raw.get("sources", []))
    if sources:
        save_user_sources_bulk(PLATFORM_SOURCES_USER_ID, sources)
        summary["sources"] = len(sources)
        for uid in list_user_ids():
            from src.db import count_user_sources

            if count_user_sources(uid) == 0:
                save_user_sources_bulk(uid, sources)
                summary["users_seeded"] += 1

    if rename_after:
        if config_path.exists():
            config_path.rename(config_path.with_suffix(".yaml.imported"))
        if sources_path.exists():
            sources_path.rename(sources_path.with_suffix(".yaml.imported"))

    return summary


def auto_import_if_empty() -> bool:
    """Import YAML when platform collections are empty. Returns True if import ran."""
    from src.db import count_config_collections

    if count_config_collections(SCOPE_PLATFORM, PLATFORM_SCOPE_ID) > 0:
        return False

    config_path = settings.ROOT / "config.yaml"
    sources_path = settings.ROOT / "config" / "sources.yaml"
    if not config_path.exists() and not sources_path.exists():
        from src.config_store import seed_platform_defaults

        seed_platform_defaults()
        print("[config] Seeded platform defaults (no YAML files found).")
        return True

    print("[config] Importing config.yaml / sources.yaml into database (deprecated YAML).")
    summary = import_yaml_to_db(rename_after=True)
    print(f"[config] Imported {summary['collections']} collections, {summary['sources']} sources.")
    return True
