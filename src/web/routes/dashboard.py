"""Dashboard routes."""

from __future__ import annotations

from fastapi import APIRouter, Request

from src.config import get_config
from src.db import count_applications_today, get_jobs_by_status, get_pipeline_readiness, list_applications
from src.web.auth.deps import get_current_user
from src.web.deps import render
from src.web.services.onboarding import get_onboarding_state
from src.web.services.pipeline_runner import run_state_dict
from src.web.services.ui_helpers import enrich_review_job, pipeline_health, recent_activity

router = APIRouter()


@router.get("/")
def dashboard(request: Request):
    cfg = get_config()
    threshold = cfg["match_threshold"]
    queued = get_jobs_by_status("queued", limit=100)
    scored = get_jobs_by_status("scored", limit=50)
    review_jobs = [
        enrich_review_job(j)
        for j in queued + [j for j in scored if (j.get("match_score") or 0) >= threshold]
    ]
    readiness = get_pipeline_readiness()
    state = run_state_dict()
    failed_jobs = list_applications(status="failed", tab="history", limit=5)
    user = get_current_user(request) or {}
    uid = user.get("id", 1)
    return render(
        request,
        "dashboard.html",
        {
            "readiness": readiness,
            "cfg": cfg,
            "applied_today": count_applications_today(),
            "review_jobs": review_jobs[:12],
            "failed_jobs": failed_jobs,
            "health": pipeline_health(readiness, state),
            "activity": recent_activity(readiness),
            "pipeline_busy": state.get("running", False),
            "state": state,
            "onboarding": get_onboarding_state(uid),
        },
    )
