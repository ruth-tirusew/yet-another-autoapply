"""Provider-aware LLM health checks."""

from __future__ import annotations

from typing import Any

from src.config_store import get_merged_config
from src.llm.catalog import PROVIDER_MODELS
from src.llm.factory import get_provider, list_available_models
from src.tenant import get_tenant_user_id


def _user_id() -> int | None:
    return get_tenant_user_id()


def check_provider_health(user_id: int | None = None) -> dict[str, Any]:
    uid = user_id if user_id is not None else _user_id()
    cfg = get_merged_config(uid)
    provider = cfg.get("llm_provider", "ollama")
    try:
        client = get_provider(uid)
        if provider == "ollama":
            models = client.list_models()
            return {
                "reachable": bool(models) or True,
                "provider": provider,
                "host": cfg.get("ollama_host"),
                "models": models,
                "error": None,
            }
        models = client.list_models()
        test = client.health_check(cfg.get("default_model") or models[0])
        return {
            "reachable": test.get("ok", False),
            "provider": provider,
            "host": None,
            "models": models,
            "error": test.get("error"),
        }
    except Exception as e:
        return {
            "reachable": False,
            "provider": provider,
            "host": cfg.get("ollama_host") if provider == "ollama" else None,
            "models": PROVIDER_MODELS.get(provider, []),
            "error": str(e),
        }


def check_ollama(host: str | None = None) -> dict[str, Any]:
    """Backward-compatible alias."""
    return check_provider_health()


def test_model(model: str | None = None, host: str | None = None, user_id: int | None = None) -> dict[str, Any]:
    uid = user_id if user_id is not None else _user_id()
    cfg = get_merged_config(uid)
    model = model or cfg["default_model"]
    try:
        client = get_provider(uid)
        return client.health_check(model)
    except Exception as e:
        return {"ok": False, "model": model, "latency_ms": 0, "error": str(e)}


def list_models(host: str | None = None, user_id: int | None = None) -> list[str]:
    return list_available_models(user_id if user_id is not None else _user_id())
