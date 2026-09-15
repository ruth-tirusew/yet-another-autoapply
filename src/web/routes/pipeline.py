"""Pipeline wizard routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from src.db import get_pipeline_readiness
from src.web.auth.deps import require_user
from src.web.deps import render
from src.web.services.pipeline_runner import is_busy, run_state_dict, start_run
from src.web.services.ui_helpers import pipeline_health, pipeline_stages

router = APIRouter()


@router.get("/pipeline")
def pipeline_page(request: Request):
    state = run_state_dict()
    readiness = get_pipeline_readiness()
    return render(
        request,
        "pipeline.html",
        {
            "readiness": readiness,
            "busy": is_busy(),
            "state": state,
            "stages": pipeline_stages(readiness),
            "health": pipeline_health(readiness, state),
        },
    )


@router.post("/pipeline/run")
def pipeline_run(
    request: Request,
    user: dict = Depends(require_user),
    stages: str = Form("crawl,enrich,embed,sync,prefilter,match,generate,export"),
):
    stage_list = [s.strip() for s in stages.split(",") if s.strip()]
    try:
        start_run(stage_list, user_id=user["id"])
    except RuntimeError as e:
        if request.headers.get("HX-Request"):
            return HTMLResponse(f'<p class="text-red-600 text-sm">{e}</p>', status_code=409)
        return JSONResponse({"error": str(e)}, status_code=409)
    state = run_state_dict()
    if request.headers.get("HX-Request"):
        return render(request, "partials/pipeline_status.html", {"state": state})
    readiness = get_pipeline_readiness()
    return render(
        request,
        "pipeline.html",
        {
            "readiness": readiness,
            "busy": True,
            "state": state,
            "stages": pipeline_stages(readiness),
            "health": pipeline_health(readiness, state),
        },
    )


@router.get("/partials/pipeline-status")
def pipeline_status_partial(request: Request):
    return render(request, "partials/pipeline_status.html", {"state": run_state_dict()})
