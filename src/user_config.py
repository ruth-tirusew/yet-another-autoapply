"""Per-user configuration stored in SQLite collections."""

from __future__ import annotations

import copy
from typing import Any

from src.config_store import (
    get_all_collections,
    get_collection,
    list_sources,
    save_collection,
    save_sources,
)
from src.tenant import resolve_user_id


def default_config_template() -> dict[str, Any]:
    from src.config_store import default_config_template as _default

    return _default()


def load_user_config_raw(user_id: int | None = None) -> dict[str, Any]:
    uid = resolve_user_id(user_id)
    raw = get_all_collections(uid)
    raw["sources"] = list_sources(uid)
    return raw


def load_user_config(user_id: int | None = None) -> dict[str, Any]:
    from src.config_store import get_merged_config

    return get_merged_config(user_id)


def save_user_config_section(user_id: int, section: str, data: Any) -> None:
    if section == "root":
        if not isinstance(data, dict):
            raise ValueError("root section must be a dict")
        for key, value in data.items():
            if key == "sources":
                save_sources(value, user_id)
            elif key in (
                "pipeline", "keywords", "models", "applicant", "auto_apply",
                "cover_letter", "hiring_agent", "ever_jobs", "scheduler",
                "source_discovery", "filters", "output",
            ):
                save_collection(key, value, user_id=user_id)
        return
    if section == "sources":
        save_sources(data, user_id)
        return
    save_collection(section, data, user_id=user_id)


def get_user_sources(user_id: int | None = None) -> list[dict[str, Any]]:
    return list_sources(user_id)


def save_user_sources(user_id: int, sources: list[dict[str, Any]]) -> None:
    save_sources(sources, user_id)
