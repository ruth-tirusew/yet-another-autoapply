"""Config read/write with validation — DB collections only."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from src.config_store import get_all_collections, get_collection, save_collection
from src.db import delete_llm_credential, list_llm_credentials_meta, save_llm_credential
from src.keywords import INCLUDE_GROUPS, get_keywords_config
from src.settings import get_config, reload_config, reload_sources
from src.tenant import get_tenant_user_id, resolve_user_id
from src.user_config import get_user_sources, load_user_config_raw, save_user_config_section, save_user_sources


def _scope_user_id(user_id: int | None = None) -> int | None:
    uid = get_tenant_user_id()
    if uid is not None:
        return uid
    if user_id is not None:
        return user_id
    return None


def _raw_config(user_id: int | None = None) -> dict:
    uid = _scope_user_id(user_id)
    if uid is not None:
        return load_user_config_raw(uid)
    return get_all_collections(None)


class PipelineConfig(BaseModel):
    match_threshold: int = Field(ge=0, le=100)
    role_fit_min: int = Field(ge=0, le=25, default=15)
    maybe_score_boost: int = Field(ge=0, le=50, default=10)
    stale_days: int = Field(ge=1, le=365)
    enrich_delay_seconds: float = Field(ge=0)
    crawl_delay_seconds: float = Field(ge=0)
    enrich_limit: int = Field(ge=1, le=1000)
    match_limit: int = Field(ge=1, le=1000)
    generate_limit: int = Field(ge=1, le=200)


class HiringAgentConfig(BaseModel):
    evaluate_on_upload: bool = True


class OtpImapConfig(BaseModel):
    host: str = "imap.gmail.com"
    port: int = Field(ge=1, le=65535, default=993)
    username: str = ""
    use_tls: bool = True
    poll_timeout_sec: int = Field(ge=10, le=300, default=90)
    poll_interval_sec: int = Field(ge=2, le=60, default=5)


class AutoApplyConfig(BaseModel):
    enabled: bool = False
    allowlist: list[str] = Field(default_factory=lambda: ["greenhouse", "lever"])
    max_per_day: int = Field(ge=1, le=50)
    headless: bool = True
    otp_auto_fetch: bool = False
    otp_imap: OtpImapConfig = Field(default_factory=OtpImapConfig)


class ApplicantConfig(BaseModel):
    name: str = ""
    email: str = ""
    phone: str = ""
    linkedin: str = ""
    github: str = ""
    portfolio: str = ""
    location: str = ""
    work_authorization: str = ""
    salary_expectation: str = ""


class ModelsConfig(BaseModel):
    provider: str = "ollama"
    ollama_host: str = "http://localhost:11434"
    default_model: str
    quality_model: str
    embedding_model: str = "mxbai-embed-large"
    api_key: str = ""

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, v: str) -> str:
        return v if v in ("ollama", "groq", "gemini") else "ollama"


class KeywordsConfig(BaseModel):
    primary_groups: list[str] = Field(default_factory=lambda: ["stack", "roles"])
    include: dict[str, list[str]] = Field(default_factory=dict)
    exclude: list[str] = Field(default_factory=list)

    @field_validator("primary_groups")
    @classmethod
    def validate_primary(cls, v: list[str]) -> list[str]:
        valid = [g for g in v if g in INCLUDE_GROUPS]
        return valid or ["stack", "roles"]

    def model_post_init(self, __context: Any) -> None:
        for g in INCLUDE_GROUPS:
            self.include.setdefault(g, [])


class EverJobsConfig(BaseModel):
    enabled: bool = False
    api_url: str = ""
    search_mode: str = "auto"
    search_term: str = ""
    search_groups: list[str] = Field(default_factory=lambda: ["stack", "roles", "frameworks"])
    is_remote: bool = True
    results_wanted: int = Field(ge=1, le=200, default=30)
    site_type: list[str] = Field(default_factory=list)

    @field_validator("search_mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        return v if v in ("auto", "manual") else "auto"


def _save_section(section: str, data: Any, user_id: int | None = None) -> None:
    uid = _scope_user_id(user_id)
    if uid is not None:
        save_user_config_section(resolve_user_id(user_id), section, data)
        return
    save_collection(section, data, scope="platform", scope_id=0)


def get_full_config() -> dict[str, Any]:
    cfg = get_config()
    raw = _raw_config()
    auto_raw = raw.get("auto_apply", {})
    if not isinstance(auto_raw, dict):
        auto_raw = {}
    models_raw = raw.get("models", {})
    matching_raw = raw.get("matching", {})
    uid = _scope_user_id()
    creds = list_llm_credentials_meta(uid) if uid else {}
    provider = models_raw.get("provider", "ollama")
    return {
        "raw": raw,
        "models": {
            "provider": provider,
            "ollama_host": cfg["ollama_host"],
            "default_model": cfg["default_model"],
            "quality_model": cfg["quality_model"],
            "embedding_model": matching_raw.get("embedding_model", "mxbai-embed-large"),
            "api_key_hint": creds.get(provider, {}).get("key_hint", ""),
            "has_api_key": bool(creds.get(provider, {}).get("has_key")),
        },
        "pipeline": raw.get("pipeline", cfg.get("pipeline", {})),
        "keywords": get_keywords_config(),
        "auto_apply": {
            "enabled": cfg["auto_apply"],
            "allowlist": cfg["auto_apply_sources"],
            "max_per_day": cfg["max_applications_per_day"],
            "headless": auto_raw.get("headless", True),
        },
        "applicant": raw.get("applicant", {}),
        "cover_letter": raw.get("cover_letter", {}),
        "ever_jobs": raw.get("ever_jobs", {}),
        "scheduler": raw.get("scheduler", {}),
        "source_discovery": raw.get("source_discovery", {}),
        "filters": raw.get("filters", {}),
        "output": raw.get("output", {}),
    }


def save_models_config(data: dict[str, Any]) -> None:
    validated = ModelsConfig(**data)
    uid = _scope_user_id()
    models_data = {
        "provider": validated.provider,
        "default": validated.default_model,
        "quality": validated.quality_model,
        "ollama_host": validated.ollama_host,
    }
    if uid is not None:
        save_collection("models", models_data, user_id=uid)
        if validated.api_key.strip() and validated.provider in ("groq", "gemini"):
            save_llm_credential(uid, validated.provider, validated.api_key.strip())
        matching = get_collection("matching", user_id=uid)
        matching["embedding_model"] = validated.embedding_model
        save_collection("matching", matching, user_id=uid)
    else:
        save_collection("models", models_data, scope="platform", scope_id=0)
        if validated.api_key.strip() and validated.provider in ("groq", "gemini"):
            save_llm_credential(1, validated.provider, validated.api_key.strip())
        matching = get_collection("matching", scope="platform", scope_id=0)
        matching["embedding_model"] = validated.embedding_model
        save_collection("matching", matching, scope="platform", scope_id=0)
    reload_config()


def save_pipeline_config(data: dict[str, Any]) -> None:
    validated = PipelineConfig(**data)
    _save_section("pipeline", validated.model_dump())
    reload_config()


def save_auto_apply_config(data: dict[str, Any]) -> None:
    validated = AutoApplyConfig(**data)
    _save_section("auto_apply", validated.model_dump())
    reload_config()


def save_applicant_config(data: dict[str, Any]) -> None:
    validated = ApplicantConfig(**data)
    _save_section("applicant", validated.model_dump())


def save_keywords_config(data: dict[str, Any]) -> None:
    validated = KeywordsConfig(**data)
    _save_section("keywords", validated.model_dump())
    reload_config()


def save_cover_letter_config(data: dict[str, Any]) -> None:
    _save_section("cover_letter", data)


def save_hiring_agent_config(data: dict[str, Any]) -> None:
    validated = HiringAgentConfig(**data)
    _save_section("hiring_agent", validated.model_dump())


def save_integrations_config(data: dict[str, Any]) -> None:
    updates: dict[str, Any] = {}
    if "ever_jobs" in data:
        ej_raw = data["ever_jobs"]
        existing = _raw_config().get("ever_jobs", {})
        if isinstance(existing, dict):
            ej_raw = {**existing, **ej_raw}
        validated = EverJobsConfig(**ej_raw)
        updates["ever_jobs"] = validated.model_dump()
    if "scheduler" in data:
        updates["scheduler"] = data["scheduler"]
    if "source_discovery" in data:
        updates["source_discovery"] = data["source_discovery"]
    if updates:
        uid = _scope_user_id()
        if uid is not None:
            save_user_config_section(uid, "root", updates)
        else:
            for key, value in updates.items():
                save_collection(key, value, scope="platform", scope_id=0)
        reload_config()


def toggle_source(source_id: str, enabled: bool) -> None:
    uid = _scope_user_id()
    if uid is not None:
        sources = get_user_sources(uid)
        for s in sources:
            if s.get("id") == source_id:
                s["enabled"] = enabled
                break
        save_user_sources(uid, sources)
        reload_sources()
        return
    from src.config_store import list_sources, save_sources

    sources = list_sources()
    for s in sources:
        if s.get("id") == source_id:
            s["enabled"] = enabled
            break
    save_sources(sources)
    reload_sources()


def get_sources_list() -> list[dict[str, Any]]:
    uid = _scope_user_id()
    if uid is not None:
        return get_user_sources(uid)
    from src.config_store import list_sources

    return list_sources()
