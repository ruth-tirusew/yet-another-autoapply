"""DB-backed configuration collections (platform + per-user)."""

from __future__ import annotations

import copy
from typing import Any

from src.tenant import get_tenant_user_id, resolve_user_id

SCOPE_PLATFORM = "platform"
SCOPE_USER = "user"
PLATFORM_SCOPE_ID = 0
PLATFORM_SOURCES_USER_ID = 0

COLLECTION_NAMES = [
    "pipeline",
    "keywords",
    "models",
    "matching",
    "applicant",
    "auto_apply",
    "cover_letter",
    "hiring_agent",
    "ever_jobs",
    "scheduler",
    "source_discovery",
    "filters",
    "output",
]

DEFAULT_COLLECTIONS: dict[str, Any] = {
    "pipeline": {
        "match_threshold": 70,
        "role_fit_min": 15,
        "maybe_score_boost": 10,
        "stale_days": 30,
        "enrich_delay_seconds": 1.0,
        "crawl_delay_seconds": 1.5,
        "enrich_limit": 25,
        "match_limit": 10,
        "match_workers": 4,
        "generate_limit": 5,
        "sync_limit": 500,
        "embed_limit": 200,
    },
    "matching": {
        "vector_prefilter_enabled": True,
        "vector_min_score": 0.35,
        "vector_llm_min_score": 0.50,
        "vector_borderline_band": [0.35, 0.50],
        "embedding_model": "mxbai-embed-large",
        # "auto" follows llm_provider; set explicitly to embed with a
        # different backend than the one used for chat.
        "embedding_provider": "auto",
        "niche_terms_extra": [],
        # grounded: extract requirements, retrieve evidence, judge each one,
        # recompute scores deterministically. llm/rules/hybrid are the legacy
        # single-shot engines.
        "scoring_mode": "grounded",
        "max_match_attempts": 3,
        "rules": {
            "hybrid_band": 10,
            "seniority_levels": [
                "intern",
                "junior",
                "mid",
                "senior",
                "staff",
                "principal",
                "lead",
                "manager",
                "director",
                "vp",
            ],
            "candidate_seniority": "senior",
            "synonyms": {
                "js": "javascript",
                "ts": "typescript",
                "k8s": "kubernetes",
                "go": "golang",
            },
        },
    },
    "keywords": {
        "primary_groups": ["stack", "roles"],
        "include": {
            "stack": ["python", "golang", "springboot", "django", "fastapi"],
            "roles": ["full stack", "fullstack", "full-stack", "backend", "platform engineer", "software engineer"],
            "frameworks": ["spring boot", "django", "fastapi", "prefect", "next.js", "vue js", "nextjs", "django", "DRF"],
            "environment": ["remote", "worldwide", "distributed", "anywhere"],
            "contract_type": ["full-time", "contract", "freelance"],
            "seniority": ["senior", "mid-senior"],
            "industry": ["fintech", "open source"],
        },
        "exclude": ["unpaid", "volunteer", "intern only"],
    },
    "models": {
        "provider": "ollama",
        "default": "deepseek-r1:1.5b",
        "quality": "gemma3:4b",
        "ollama_host": "http://localhost:11434",
    },
    "applicant": {
        "name": "",
        "email": "",
        "phone": "",
        "linkedin": "",
        "github": "",
        "portfolio": "",
        "location": "",
        "work_authorization": "",
        "salary_expectation": "",
    },
    "auto_apply": {
        "enabled": False,
        "allowlist": ["greenhouse", "lever", "email", "generic"],
        "max_per_day": 5,
        "headless": True,
        "otp_auto_fetch": False,
        "otp_imap": {
            "host": "imap.gmail.com",
            "port": 993,
            "username": "",
            "use_tls": True,
            "poll_timeout_sec": 90,
            "poll_interval_sec": 5,
        },
    },
    "cover_letter": {"max_words": 350, "tone": "concise"},
    "hiring_agent": {"evaluate_on_upload": True},
    "ever_jobs": {
        "enabled": False,
        "api_url": "http://localhost:3001",
        "search_mode": "auto",
        "search_term": "",
        "search_groups": ["stack", "roles", "frameworks"],
        "is_remote": True,
        "results_wanted": 30,
        "site_type": [
            "remoteok", "remotive", "jobicy", "himalayas", "arbeitnow",
            "weworkremotely", "fourdayweek", "golangjobs",
        ],
    },
    "scheduler": {"enabled": False, "crawl_interval_hours": 8},
    "source_discovery": {
        "awesome_job_boards_url": "https://raw.githubusercontent.com/tramcar/awesome-job-boards/master/README.md",
        "auto_enable_discovered": True,
        "max_discovered": 40,
    },
    "filters": {
        "worldwide_markers": ["worldwide", "anywhere", "global", "international"],
        "restricted_markers": [
            "us only",
            "uk only",
            "eu only",
            "canada only",
            "usa only",
            "must be located in",
            "must reside in",
        ],
        "require_worldwide": True,
    },
    "output": {"excel": "jobs_results.xlsx"},
}


def _resolve_scope(user_id: int | None = None) -> tuple[str, int]:
    if user_id is not None:
        return SCOPE_USER, user_id
    uid = get_tenant_user_id()
    if uid is not None:
        return SCOPE_USER, uid
    return SCOPE_PLATFORM, PLATFORM_SCOPE_ID


def get_collection(
    collection: str,
    *,
    scope: str | None = None,
    scope_id: int | None = None,
    user_id: int | None = None,
) -> dict[str, Any]:
    from src.db import get_config_collection

    if scope is None or scope_id is None:
        scope, scope_id = _resolve_scope(user_id)
    data = get_config_collection(scope, scope_id, collection)
    if data is not None:
        return copy.deepcopy(data)
    return copy.deepcopy(DEFAULT_COLLECTIONS.get(collection, {}))


def save_collection(
    collection: str,
    data: Any,
    *,
    scope: str | None = None,
    scope_id: int | None = None,
    user_id: int | None = None,
) -> None:
    from src.db import save_config_collection

    if scope is None or scope_id is None:
        scope, scope_id = _resolve_scope(user_id)
    save_config_collection(scope, scope_id, collection, data)


def get_all_collections(user_id: int | None = None) -> dict[str, Any]:
    from src.db import list_config_collections

    scope, scope_id = _resolve_scope(user_id)
    stored = list_config_collections(scope, scope_id)
    merged = copy.deepcopy(DEFAULT_COLLECTIONS)
    merged.update(stored)
    return merged


def get_merged_config(user_id: int | None = None) -> dict[str, Any]:
    """Return config dict with computed top-level fields callers expect."""
    raw = get_all_collections(user_id)
    pipeline = raw.get("pipeline", {})
    auto = raw.get("auto_apply", {})
    models = raw.get("models", {})
    ever = raw.get("ever_jobs", {})

    cfg = copy.deepcopy(raw)
    cfg["match_threshold"] = int(pipeline.get("match_threshold", 70))
    cfg["auto_apply_flag"] = bool(auto.get("enabled", False))
    cfg["auto_apply"] = bool(auto.get("enabled", False))
    cfg["auto_apply_sources"] = list(auto.get("allowlist", ["greenhouse", "lever"]))
    cfg["max_applications_per_day"] = int(auto.get("max_per_day", 5))
    cfg["auto_apply_headless"] = bool(auto.get("headless", True))
    cfg["otp_auto_fetch"] = bool(auto.get("otp_auto_fetch", False))
    cfg["otp_imap"] = copy.deepcopy(auto.get("otp_imap", DEFAULT_COLLECTIONS["auto_apply"]["otp_imap"]))
    cfg["stale_job_days"] = int(pipeline.get("stale_days", 30))
    cfg["default_model"] = models.get("default", "deepseek-r1:1.5b")
    cfg["quality_model"] = models.get("quality", "gemma3:4b")
    cfg["ollama_host"] = models.get("ollama_host", "http://localhost:11434")
    cfg["llm_provider"] = models.get("provider", "ollama")
    cfg["ever_jobs_url"] = ever.get("api_url", "http://localhost:3001")
    cfg["ever_jobs_enabled"] = bool(ever.get("enabled", False))
    if "output" not in cfg:
        cfg["output"] = {"excel": "jobs_results.xlsx"}
    return cfg


def _resolve_sources_user_id(user_id: int | None = None) -> int:
    if user_id is not None:
        return user_id
    uid = get_tenant_user_id()
    if uid is not None:
        return uid
    return PLATFORM_SOURCES_USER_ID


def list_sources(user_id: int | None = None) -> list[dict[str, Any]]:
    from src.db import list_user_sources

    return list_user_sources(_resolve_sources_user_id(user_id))


def save_sources(sources: list[dict[str, Any]], user_id: int | None = None) -> None:
    from src.db import save_user_sources_bulk

    save_user_sources_bulk(_resolve_sources_user_id(user_id), sources)


def copy_platform_to_user(user_id: int) -> None:
    from src.db import copy_platform_config_to_user, copy_platform_sources_to_user

    copy_platform_config_to_user(user_id)
    copy_platform_sources_to_user(user_id)


def seed_platform_defaults() -> None:
    from src.db import count_config_collections, save_config_collection, list_user_sources, save_user_sources_bulk

    if count_config_collections(SCOPE_PLATFORM, PLATFORM_SCOPE_ID) == 0:
        for name, data in DEFAULT_COLLECTIONS.items():
            save_config_collection(SCOPE_PLATFORM, PLATFORM_SCOPE_ID, name, data)

    existing = list_user_sources(PLATFORM_SOURCES_USER_ID)
    if existing:
        return
    default_sources = [
        {
            "id": "job-board-aggregator",
            "name": "Job Board Aggregator (200k+ ATS jobs)",
            "adapter": "job_board_aggregator",
            "enabled": True,
            "remote_only": True,
            "exclude_recruiters": True,
            "max_chunks": 6,
            "max_jobs": 500,
        },
    ]
    save_user_sources_bulk(PLATFORM_SOURCES_USER_ID, default_sources)


def default_config_template() -> dict[str, Any]:
    """Merged platform collections for seeding new users."""
    return get_all_collections(user_id=None)
