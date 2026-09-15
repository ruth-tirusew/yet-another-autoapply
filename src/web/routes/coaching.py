"""Skills gap coaching page."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse

from src.coaching.cv_preview import (
    apply_cv_draft,
    build_cv_draft,
    cv_draft_status,
    get_cv_draft_html,
)
from src.coaching.profile_guide import build_profile_guide, guide_cache_status
from src.coaching.skills_gap import aggregate_gaps, build_gap_report
from src.db import get_profile
from src.web.auth.deps import require_user
from src.web.deps import render

router = APIRouter()


def _guide_context(user_id: int) -> dict:
    status = guide_cache_status(user_id)
    draft = cv_draft_status(user_id)
    return {
        "profile_loaded": status["profile_loaded"],
        "guide": status["guide"].model_dump() if status["guide"] else None,
        "guide_stale": status["guide_stale"],
        "guide_generated_at": status["generated_at"],
        "cv_draft_ready": draft["ready"],
        "cv_draft_stale": draft["stale"],
        "cv_draft_generated_at": draft["generated_at"],
        "cv_apply_success": False,
    }


@router.get("/coaching")
def coaching_page(request: Request, user: dict = Depends(require_user)):
    freq = aggregate_gaps(user["id"])
    report = build_gap_report(user["id"], use_llm=bool(freq))
    ctx = _guide_context(user["id"])
    return render(
        request,
        "coaching.html",
        {
            "report": report.model_dump(),
            "has_data": bool(freq),
            "gap_items": list(freq.most_common(12)),
            **ctx,
        },
    )


@router.get("/partials/coaching/profile-guide")
def profile_guide_partial(request: Request, user: dict = Depends(require_user)):
    ctx = _guide_context(user["id"])
    return render(request, "partials/profile_guide.html", ctx)


@router.post("/coaching/profile-guide/generate")
def profile_guide_generate(
    request: Request,
    user: dict = Depends(require_user),
    force: int = Query(0),
):
    if not get_profile(user["id"]):
        ctx = _guide_context(user["id"])
        return render(request, "partials/profile_guide.html", ctx)

    build_profile_guide(user["id"], force=bool(force))
    ctx = _guide_context(user["id"])
    return render(request, "partials/profile_guide.html", ctx)


@router.post("/coaching/cv-preview/generate")
def cv_preview_generate(
    request: Request,
    user: dict = Depends(require_user),
    force: int = Query(0),
):
    ctx = _guide_context(user["id"])
    try:
        build_cv_draft(user["id"], force=bool(force))
    except Exception as e:
        ctx["cv_preview_error"] = str(e)
    ctx.update(_guide_context(user["id"]))
    return render(request, "partials/cv_preview_panel.html", ctx)


@router.get("/coaching/cv-preview/html")
def cv_preview_html(user: dict = Depends(require_user)):
    return HTMLResponse(get_cv_draft_html(user["id"]))


@router.post("/coaching/cv-preview/apply")
def cv_preview_apply(request: Request, user: dict = Depends(require_user)):
    ctx = _guide_context(user["id"])
    try:
        apply_cv_draft(user["id"])
        ctx = _guide_context(user["id"])
        ctx["cv_apply_success"] = True
    except Exception as e:
        ctx["cv_apply_error"] = str(e)
    return render(request, "partials/cv_preview_panel.html", ctx)
