"""Keyword groups, relevance matching, and search query building."""

from __future__ import annotations

from typing import Any

from src.settings import get_config

INCLUDE_GROUPS = [
    "stack",
    "roles",
    "frameworks",
    "environment",
    "contract_type",
    "seniority",
    "industry",
]

DEFAULT_PRIMARY_GROUPS = ["stack", "roles"]
DEFAULT_SEARCH_GROUPS = ["stack", "roles", "frameworks"]

_LEGACY_MAP = {
    "go": "stack",
    "fullstack": "roles",
}


def _normalize_keywords_raw(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize legacy flat groups into include/primary_groups/exclude schema."""
    if not raw:
        return {
            "primary_groups": list(DEFAULT_PRIMARY_GROUPS),
            "include": {g: [] for g in INCLUDE_GROUPS},
            "exclude": [],
        }

    if "include" in raw and isinstance(raw.get("include"), dict):
        include = {g: list(raw["include"].get(g) or []) for g in INCLUDE_GROUPS}
        return {
            "primary_groups": list(raw.get("primary_groups") or DEFAULT_PRIMARY_GROUPS),
            "include": include,
            "exclude": list(raw.get("exclude") or []),
        }

    include: dict[str, list[str]] = {g: [] for g in INCLUDE_GROUPS}
    for key, value in raw.items():
        if key in ("primary_groups", "exclude", "include"):
            continue
        if not isinstance(value, list):
            continue
        target = _LEGACY_MAP.get(key, key)
        if target in include:
            include[target].extend(str(v) for v in value)

    return {
        "primary_groups": list(DEFAULT_PRIMARY_GROUPS),
        "include": include,
        "exclude": [],
    }


def get_keywords_config() -> dict[str, Any]:
    cfg = get_config()
    return _normalize_keywords_raw(cfg.get("keywords"))


def flatten_keywords(group_names: list[str] | None = None) -> list[str]:
    """Return deduplicated lowercase terms from named include groups."""
    kw = get_keywords_config()
    include = kw.get("include") or {}
    names = group_names or INCLUDE_GROUPS
    seen: set[str] = set()
    result: list[str] = []
    for name in names:
        for term in include.get(name) or []:
            t = str(term).strip().lower()
            if t and t not in seen:
                seen.add(t)
                result.append(t)
    return result


def all_keywords() -> list[str]:
    """All include terms (backward-compatible flat list)."""
    return flatten_keywords(INCLUDE_GROUPS)


def _job_text(title: str, tags: str = "", description: str = "") -> str:
    return (title + " " + tags + " " + description).lower()


def _matches_any(text: str, terms: list[str]) -> bool:
    return any(term in text for term in terms if term)


def is_relevant(title: str, tags: str = "", description: str = "") -> bool:
    """
    Primary-group matching:
    1. Exclude terms disqualify
    2. At least one term from any primary group qualifies
    """
    kw = get_keywords_config()
    text = _job_text(title, tags, description)
    exclude = [str(t).strip().lower() for t in (kw.get("exclude") or []) if str(t).strip()]
    if exclude and _matches_any(text, exclude):
        return False

    primary = kw.get("primary_groups") or DEFAULT_PRIMARY_GROUPS
    include = kw.get("include") or {}
    for group in primary:
        terms = [str(t).strip().lower() for t in (include.get(group) or []) if str(t).strip()]
        if terms and _matches_any(text, terms):
            return True

    # Legacy fallback: flat OR across all groups if no primary terms configured
    all_terms = all_keywords()
    if not all_terms:
        return True
    if not primary:
        return _matches_any(text, all_terms)
    return False


def build_search_query(
    groups: list[str] | None = None,
    max_terms: int = 6,
) -> str:
    """Build a space-separated search string from keyword groups."""
    names = groups or DEFAULT_SEARCH_GROUPS
    terms = flatten_keywords(names)[:max_terms]
    return " ".join(terms)


def migrate_legacy_keywords(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return new schema dict if raw uses legacy keys; else None."""
    if not raw or "include" in raw:
        return None
    if not any(k in raw for k in ("go", "fullstack", "frameworks")):
        return None
    return _normalize_keywords_raw(raw)
