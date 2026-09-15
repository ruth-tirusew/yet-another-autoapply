"""Shared template helpers."""

from __future__ import annotations

from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from src.db import count_jobs_by_status
from src.web.services.profile_context import get_profile_summary
from src.web.services.ui_helpers import (
    applications_url,
    enrich_review_job,
    parse_apply_result,
    pipeline_health,
    pipeline_stages,
    recent_activity,
    score_tier,
    stage_label,
    status_badge_classes,
    status_count_classes,
)

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["applications_url"] = applications_url
templates.env.globals["get_profile_summary"] = get_profile_summary
templates.env.globals["nav_failed_count"] = lambda: count_jobs_by_status().get("failed", 0)
templates.env.globals["score_tier"] = score_tier
templates.env.globals["stage_label"] = stage_label
templates.env.globals["status_badge_classes"] = status_badge_classes
templates.env.globals["status_count_classes"] = status_count_classes
templates.env.globals["pipeline_stages"] = pipeline_stages
templates.env.globals["pipeline_health"] = pipeline_health
templates.env.globals["recent_activity"] = recent_activity
templates.env.globals["parse_apply_result"] = parse_apply_result

SETTINGS_TABS = [
    "account",
    "models",
    "pipeline",
    "hiring_agent",
    "keywords",
    "auto_apply",
    "applicant",
    "cover_letter",
    "integrations",
]


def render(
    request: Request,
    name: str,
    context: dict | None = None,
    *,
    guest: bool = False,
    status_code: int = 200,
):
    from src.web.auth.deps import get_current_user
    from src.web.auth.sessions import get_csrf_token

    ctx = dict(context or {})
    if "profile_summary" not in ctx and not guest:
        ctx["profile_summary"] = get_profile_summary()
    if "failed_count" not in ctx and not guest:
        ctx["failed_count"] = count_jobs_by_status().get("failed", 0)
    if "current_user" not in ctx:
        ctx["current_user"] = get_current_user(request)
    if "csrf_token" not in ctx:
        ctx["csrf_token"] = get_csrf_token(request)
    template_name = name if guest else name
    if guest and not name.startswith("auth/"):
        template_name = name
    response = templates.TemplateResponse(
        request,
        template_name,
        ctx,
        status_code=status_code,
    )
    return response
