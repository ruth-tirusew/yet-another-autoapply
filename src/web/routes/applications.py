"""Application tracker routes."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from src.apply.dispatcher import retry_application, retry_failed_applications
from src.db import count_jobs_by_status, list_applications, log_application_event, set_job_status
from src.web.deps import render

router = APIRouter()

ALL_STATUSES = [
    "new", "scored", "queued", "approved", "needs_verification", "applied", "failed", "rejected", "skipped", "stale",
]
HISTORY_STATUSES = frozenset({"applied", "rejected", "failed"})
ACTIVE_STATUSES = ["new", "scored", "queued", "approved", "needs_verification", "skipped"]


def resolve_filter_tab(tab: str | None, status: str | None) -> str:
    if tab in ("active", "history"):
        return tab
    if status in HISTORY_STATUSES:
        return "history"
    return "active"


def statuses_for_tab(tab: str) -> list[str]:
    if tab == "history":
        return [s for s in ALL_STATUSES if s in HISTORY_STATUSES]
    return ACTIVE_STATUSES


@router.get("/applications")
def applications_page(
    request: Request,
    status: str | None = None,
    min_score: str | None = None,
    tab: str | None = None,
):
    from src.config import get_config
    from src.matcher import count_matchable_new_jobs
    from src.web.services.pipeline_runner import is_busy

    min_score_val = None
    if min_score not in (None, ""):
        try:
            min_score_val = float(min_score)
        except ValueError:
            min_score_val = None
    filter_tab = resolve_filter_tab(tab, status or None)
    filter_status = status or ""
    if filter_status and filter_status not in statuses_for_tab(filter_tab):
        filter_status = ""
    jobs = list_applications(
        status=filter_status or None,
        min_score=min_score_val,
        tab=filter_tab,
        limit=200,
    )
    ctx = {
        "jobs": jobs,
        "all_statuses": statuses_for_tab(filter_tab),
        "filter_status": filter_status,
        "filter_min_score": min_score or "",
        "filter_tab": filter_tab,
        "failed_count": count_jobs_by_status().get("failed", 0),
        "retry_summary": request.query_params.get("retry_summary", ""),
        "matchable_new": count_matchable_new_jobs(),
        "match_limit": get_config().get("pipeline", {}).get("match_limit", 10),
        "pipeline_busy": is_busy(),
    }
    if request.headers.get("HX-Request"):
        return render(request, "partials/applications_panel.html", ctx)
    return render(request, "applications.html", ctx)


def _applications_redirect(
    *,
    tab: str,
    status: str = "",
    min_score: str = "",
    retry_summary: str = "",
) -> RedirectResponse:
    params = [f"tab={tab}"]
    if status:
        params.append(f"status={status}")
    if min_score:
        params.append(f"min_score={min_score}")
    if retry_summary:
        params.append(f"retry_summary={quote(retry_summary)}")
    return RedirectResponse(f"/applications?{'&'.join(params)}", status_code=303)


@router.post("/applications/retry-failed")
def retry_all_failed(
    tab: str = Form("history"),
    status: str = Form(""),
    min_score: str = Form(""),
):
    summary = retry_failed_applications(force=True)
    message = (
        f"Retried {summary['total']}: {summary['succeeded']} succeeded, "
        f"{summary['failed']} still failed"
    )
    return _applications_redirect(
        tab=tab or "history",
        status=status,
        min_score=min_score,
        retry_summary=message,
    )


@router.post("/applications/{job_id}/retry")
def retry_one_failed(
    job_id: int,
    tab: str = Form("history"),
    status: str = Form(""),
    min_score: str = Form(""),
):
    result = retry_application(job_id, force=True)
    if result.get("success"):
        message = f"Job {job_id} applied successfully"
    else:
        message = f"Job {job_id} retry failed: {result.get('message', 'unknown error')}"
    return _applications_redirect(
        tab=tab or "history",
        status=status,
        min_score=min_score,
        retry_summary=message,
    )


@router.post("/applications/{job_id}/mark-applied")
def mark_one_applied(
    job_id: int,
    tab: str = Form("active"),
    status: str = Form(""),
    min_score: str = Form(""),
):
    """Record a manual application from the tracker table, for someone who
    applied on their own rather than waiting on (or instead of) auto-apply."""
    set_job_status(job_id, "applied")
    log_application_event(job_id, "applied_manually", {})
    return _applications_redirect(
        tab="history",
        status=status if status in HISTORY_STATUSES else "",
        min_score=min_score,
        retry_summary=f"Job {job_id} marked as applied",
    )
