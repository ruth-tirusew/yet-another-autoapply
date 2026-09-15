"""Resolve LLM provider for the current tenant user."""

from __future__ import annotations

from src.config_store import get_merged_config
from src.db import get_llm_api_key
from src.llm.providers.gemini import GeminiProvider
from src.llm.providers.groq import GroqProvider
from src.llm.providers.ollama import OllamaProvider
from src.tenant import get_tenant_user_id, resolve_user_id


def _resolve_user_id(user_id: int | None = None) -> int:
    if user_id is not None:
        return user_id
    uid = get_tenant_user_id()
    if uid is not None:
        return uid
    return 1


def get_provider(user_id: int | None = None):
    uid = _resolve_user_id(user_id)
    cfg = get_merged_config(uid)
    provider_name = cfg.get("llm_provider", "ollama")

    if provider_name == "groq":
        key = get_llm_api_key(uid, "groq")
        if not key:
            raise RuntimeError("Groq API key not configured. Add it in Settings → Models.")
        return GroqProvider(key)

    if provider_name == "gemini":
        key = get_llm_api_key(uid, "gemini")
        if not key:
            raise RuntimeError("Gemini API key not configured. Add it in Settings → Models.")
        return GeminiProvider(key)

    return OllamaProvider(host=cfg.get("ollama_host", "http://localhost:11434"))


def list_available_models(user_id: int | None = None) -> list[str]:
    from src.llm.catalog import PROVIDER_MODELS

    uid = _resolve_user_id(user_id)
    cfg = get_merged_config(uid)
    provider_name = cfg.get("llm_provider", "ollama")
    if provider_name == "ollama":
        try:
            return get_provider(uid).list_models()
        except Exception:
            return []
    return list(PROVIDER_MODELS.get(provider_name, []))
