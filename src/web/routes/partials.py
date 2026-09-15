"""LLM health and profile partials."""

from __future__ import annotations

from fastapi import APIRouter, Request

from src.config import get_config
from src.web.deps import render
from src.web.services.llm_health import check_provider_health
from src.web.services.profile_context import get_profile_summary

router = APIRouter()


@router.get("/partials/profile-eval-status")
def profile_eval_status_partial(request: Request):
    return render(
        request,
        "partials/profile_eval_status.html",
        {"profile_summary": get_profile_summary()},
    )


@router.get("/partials/model-status")
def model_status_partial(request: Request):
    cfg = get_config()
    status = check_provider_health()
    configured = {
        "provider": cfg.get("llm_provider", "ollama"),
        "default_model": cfg["default_model"],
        "quality_model": cfg["quality_model"],
    }
    installed = set(status.get("models", []))
    configured["default_available"] = configured["default_model"] in installed or status.get("provider") != "ollama"
    configured["quality_available"] = configured["quality_model"] in installed or status.get("provider") != "ollama"
    return render(
        request,
        "partials/model_status.html",
        {"status": status, "configured": configured},
    )
