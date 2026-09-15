"""Job detail and review actions."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

from src.apply.base import detect_adapter
from src.apply.dispatcher import ADAPTERS, apply_to_job, complete_verification, try_auto_verify
from src.config import get_config
from src.settings import applications_dir
from src.db import (
    clear_user_jobs,
    get_application,
    get_application_events,
    get_job,
    log_application_event,
    save_application,
    set_application_template,
    set_job_status,
)
from src.cv_templates.store import ensure_user_templates, get_template, list_templates
from src.cv_templates.resolver import resolve_template_for_job
from src.profile import load_resume
from src.web.auth.deps import require_user
from src.web.deps import render
from src.web.services.job_generator import (
    consume_generate_result,
    get_job_generate_state,
    is_job_generating,
    start_job_generate,
)
from src.web.services.job_scorer import get_job_score_state, is_job_scoring, start_job_score
from src.web.services.pipeline_runner import is_busy, start_run
from src.web.services.ui_helpers import parse_apply_result

router = APIRouter()


@router.post("/jobs/score")
def score_jobs_batch(
    request: Request,
    user: dict = Depends(require_user),
):
    """Score the next batch of new jobs (background pipeline match stage)."""
    stage_list = ["match"]
    try:
        start_run(stage_list, user_id=user["id"])
    except RuntimeError as e:
        if request.headers.get("HX-Request"):
            return HTMLResponse(f'<p class="text-red-600 text-sm">{e}</p>', status_code=409)
        return RedirectResponse(f"/jobs?score_error={e}", status_code=303)
    if request.headers.get("HX-Request"):
        from src.web.services.pipeline_runner import run_state_dict

        return render(
            request,
            "partials/score_batch_status.html",
            {"state": run_state_dict(user["id"]), "redirect": "/jobs"},
        )
    return RedirectResponse("/jobs?scoring=1", status_code=303)


@router.get("/partials/job/{job_id}/generate-status")
def job_generate_status_partial(request: Request, job_id: int, user: dict = Depends(require_user)):
    job = get_job(job_id, user_id=user["id"])
    state = get_job_generate_state(job_id)
    running = is_job_generating(job_id)
    error = state.get("error") if state else None
    skipped = state.get("skipped") if state else None
    stage = state.get("stage") if state else None
    if not running:
        finished = consume_generate_result(job_id)
        if finished:
            error = error or finished.get("error")
            skipped = skipped or finished.get("skipped")
        if error or skipped:
            return render(
                request,
                "partials/job_generate_status.html",
                {
                    "job_id": job_id,
                    "running": False,
                    "error": error,
                    "skipped": skipped,
                    "stage": stage,
                },
            )
        app_rec = get_application(job_id)
        has_cv = bool(
            app_rec and app_rec.get("tailored_cv_path") and Path(app_rec["tailored_cv_path"]).exists()
        )
        if has_cv:
            return Response(status_code=200, headers={"HX-Redirect": f"/job/{job_id}"})
    return render(
        request,
        "partials/job_generate_status.html",
        {
            "job_id": job_id,
            "running": running,
            "error": error,
            "skipped": skipped,
            "stage": stage,
        },
    )


@router.get("/partials/job/{job_id}/score-status")
def job_score_status_partial(request: Request, job_id: int, user: dict = Depends(require_user)):
    job = get_job(job_id, user_id=user["id"])
    state = get_job_score_state(job_id)
    running = is_job_scoring(job_id)
    error = state.get("error") if state else None
    if not running and not error and job and job.get("status") != "new":
        return Response(status_code=200, headers={"HX-Redirect": f"/job/{job_id}"})
    return render(
        request,
        "partials/job_score_status.html",
        {"job_id": job_id, "running": running, "error": error},
    )


@router.post("/jobs/clear")
def clear_jobs(user: dict = Depends(require_user)):
    result = clear_user_jobs(user["id"])
    return RedirectResponse(
        f"/jobs?cleared={result['jobs']}",
        status_code=303,
    )


@router.get("/jobs")
def jobs_list(request: Request, status: str = "", min_score: str = ""):
    from src.config import get_config
    from src.db import count_jobs_by_status, get_jobs
    from src.matcher import count_matchable_new_jobs
    from src.web.services.pipeline_runner import is_busy

    jobs = get_jobs(limit=500)
    if status:
        jobs = [j for j in jobs if j.get("status") == status]
    if min_score:
        try:
            ms = float(min_score)
            jobs = [j for j in jobs if (j.get("match_score") or 0) >= ms]
        except ValueError:
            pass
    cfg = get_config()
    counts = count_jobs_by_status()
    matchable_new = count_matchable_new_jobs()
    return render(
        request,
        "jobs.html",
        {
            "jobs": jobs,
            "filter_status": status,
            "filter_min_score": min_score,
            "jobs_new": counts.get("new", 0),
            "jobs_scored": counts.get("scored", 0),
            "matchable_new": matchable_new,
            "match_limit": cfg.get("pipeline", {}).get("match_limit", 10),
            "pipeline_busy": is_busy(),
        },
    )


def _ats_badge(url: str, job: dict | None = None) -> str | None:
    url_lower = (url or "").lower()
    if "greenhouse.io" in url_lower:
        return "Greenhouse"
    if "jobs.lever.co" in url_lower or "lever.co" in url_lower:
        return "Lever"
    if "ashbyhq.com" in url_lower:
        return "Ashby"
    if "workable.com" in url_lower:
        return "Workable"
    if "smartrecruiters.com" in url_lower:
        return "SmartRecruiters"
    return job.get("ats_type") or None


def _apply_context(job: dict, has_cv: bool) -> dict:
    url = job.get("url", "")
    method = detect_adapter(url)
    status = job.get("status", "")

    if status == "applied":
        return {
            "apply_method": method,
            "can_auto_apply": False,
            "can_retry_apply": False,
            "can_verify_apply": False,
            "apply_label": "Applied",
            "apply_blocked": "Already applied",
            "is_manual_apply": method == "generic",
        }

    if status == "needs_verification":
        return {
            "apply_method": method,
            "can_auto_apply": False,
            "can_retry_apply": False,
            "can_verify_apply": method == "greenhouse",
            "apply_label": "Awaiting verification",
            "apply_blocked": None,
            "is_manual_apply": False,
        }

    if not has_cv:
        return {
            "apply_method": method,
            "can_auto_apply": False,
            "can_retry_apply": False,
            "can_verify_apply": False,
            "apply_label": "Apply now",
            "apply_blocked": "Generate materials first",
            "is_manual_apply": method == "generic",
        }

    if method == "generic":
        return {
            "apply_method": method,
            "can_auto_apply": False,
            "can_retry_apply": False,
            "can_verify_apply": False,
            "apply_label": "Apply on site",
            "apply_blocked": None,
            "is_manual_apply": True,
        }

    cfg = get_config()
    allowlist = cfg.get("auto_apply_sources", ["greenhouse", "lever"])
    adapter = ADAPTERS.get(method)
    if method not in allowlist and method != "email":
        return {
            "apply_method": method,
            "can_auto_apply": False,
            "can_retry_apply": False,
            "can_verify_apply": False,
            "apply_label": "Apply now",
            "apply_blocked": f"{method} auto-apply is disabled in settings",
            "is_manual_apply": False,
        }
    if not adapter or not adapter.can_apply(url):
        return {
            "apply_method": method,
            "can_auto_apply": False,
            "can_retry_apply": False,
            "can_verify_apply": False,
            "apply_label": "Apply now",
            "apply_blocked": f"Cannot auto-apply to this {method} posting",
            "is_manual_apply": False,
        }

    return {
        "apply_method": method,
        "can_auto_apply": True,
        "can_retry_apply": status == "failed",
        "can_verify_apply": False,
        "apply_label": "Retry apply" if status == "failed" else "Apply now",
        "apply_blocked": None,
        "is_manual_apply": False,
    }


def _resume_preview(resume: dict | None) -> dict:
    if not resume:
        return {}
    basics = resume.get("basics") or {}
    from src.profile_format import normalize_skills

    skills = normalize_skills(resume.get("skills"))
    work = resume.get("work") or []
    top_skills: list[str] = []
    for group in skills[:3]:
        if group.get("name"):
            top_skills.append(group["name"])
        top_skills.extend((group.get("keywords") or [])[:4])
    latest_role = work[0] if work else {}
    return {
        "summary": basics.get("summary") or "",
        "label": basics.get("label") or "",
        "top_skills": top_skills[:8],
        "latest_role": {
            "position": latest_role.get("position") or "",
            "company": latest_role.get("name") or "",
            "highlights": (latest_role.get("highlights") or [])[:4],
        },
    }


@router.get("/job/{job_id}")
def job_detail(request: Request, job_id: int):
    job = get_job(job_id)
    if not job:
        return HTMLResponse("Job not found", status_code=404)
    scoring = request.query_params.get("scoring") == "1" or is_job_scoring(job_id)
    generating = request.query_params.get("generating") == "1" or is_job_generating(job_id)
    app_rec = get_application(job_id)
    match = None
    if job.get("match_details"):
        try:
            match = json.loads(job["match_details"])
        except json.JSONDecodeError:
            pass
    cover_text = ""
    if app_rec and app_rec.get("cover_letter_path"):
        p = Path(app_rec["cover_letter_path"])
        if p.exists():
            cover_text = p.read_text(encoding="utf-8")
    screenshot_url = None
    shot = applications_dir() / str(job_id) / "apply_screenshot.png"
    if shot.exists():
        screenshot_url = f"/applications/{job_id}/screenshot"
    has_cv = bool(app_rec and app_rec.get("tailored_cv_path") and Path(app_rec["tailored_cv_path"]).exists())
    master = load_resume()
    tailored = (app_rec or {}).get("tailored_resume_json") or {}
    description = job.get("description_full") or job.get("description_short") or ""
    profile_loaded = master is not None
    can_score = job.get("status") == "new" and bool(description) and profile_loaded
    score_blocked = None
    if job.get("status") == "new":
        if not profile_loaded:
            score_blocked = "Upload a CV on Profile before scoring."
        elif not description:
            score_blocked = "No job description yet — run Enrich on the Pipeline page first."

    cv_templates = ensure_user_templates()
    cv_template = None
    if app_rec and app_rec.get("cv_template_id"):
        cv_template = get_template(app_rec["cv_template_id"])
    if not cv_template:
        cv_template = resolve_template_for_job(job_id)

    events = get_application_events(job_id)
    approve_error = None
    if request.query_params.get("approve_error") == "1":
        approve_error = next(
            (e["detail"].get("message") for e in events if e["event_type"] == "generate_failed"),
            "Approval was recorded, but generating the CV/cover letter failed.",
        )

    return render(
        request,
        "job_detail.html",
        {
            "job": job,
            "app_rec": app_rec,
            "match": match,
            "cover_text": cover_text,
            "description": description,
            "can_score": can_score,
            "score_blocked": score_blocked,
            "scoring": scoring,
            "generating": generating,
            "approve_error": approve_error,
            "ats_badge": _ats_badge(job.get("url", ""), job),
            "screenshot_url": screenshot_url,
            "has_cv": has_cv,
            "master_preview": _resume_preview(master),
            "tailored_preview": _resume_preview(tailored) if tailored else None,
            "events": events,
            "apply_result_message": parse_apply_result((app_rec or {}).get("apply_result")),
            "otp_auto_fetch": bool(get_config().get("otp_auto_fetch")),
            "cv_templates": cv_templates,
            "cv_template": cv_template,
            **_apply_context(job, has_cv),
        },
    )


@router.post("/job/{job_id}/match")
def rematch_job(request: Request, job_id: int, user: dict = Depends(require_user)):
    job = get_job(job_id, user_id=user["id"])
    if not job:
        return HTMLResponse("Job not found", status_code=404)
    description = job.get("description_full") or job.get("description_short") or ""
    if not load_resume(user["id"]):
        msg = "Upload a CV on Profile before scoring."
        if request.headers.get("HX-Request"):
            return HTMLResponse(f'<p class="text-red-600 text-sm">{msg}</p>', status_code=400)
        return RedirectResponse(f"/job/{job_id}?score_error=profile", status_code=303)
    if not description:
        msg = "No job description yet — run Enrich first."
        if request.headers.get("HX-Request"):
            return HTMLResponse(f'<p class="text-red-600 text-sm">{msg}</p>', status_code=400)
        return RedirectResponse(f"/job/{job_id}?score_error=description", status_code=303)
    if not start_job_score(job_id, user["id"]):
        msg = "This job is already being scored."
        if request.headers.get("HX-Request"):
            return render(
                request,
                "partials/job_score_status.html",
                {"job_id": job_id, "running": True, "error": None},
            )
        return RedirectResponse(f"/job/{job_id}?scoring=1", status_code=303)
    if request.headers.get("HX-Request"):
        return render(
            request,
            "partials/job_score_status.html",
            {"job_id": job_id, "running": True, "error": None},
        )
    return RedirectResponse(f"/job/{job_id}?scoring=1", status_code=303)


@router.post("/job/{job_id}/tailor")
def tailor_job(request: Request, job_id: int, user: dict = Depends(require_user)):
    job = get_job(job_id, user_id=user["id"])
    if not job:
        return HTMLResponse("Job not found", status_code=404)
    if not load_resume(user["id"]):
        msg = "Upload a CV on Profile before tailoring."
        if request.headers.get("HX-Request"):
            return HTMLResponse(f'<p class="text-red-600 text-sm">{msg}</p>', status_code=400)
        return RedirectResponse(f"/job/{job_id}", status_code=303)
    if not start_job_generate(job_id, user["id"], mode="tailor"):
        if request.headers.get("HX-Request"):
            return render(
                request,
                "partials/job_generate_status.html",
                {"job_id": job_id, "running": True, "error": None, "skipped": None, "stage": "tailoring"},
            )
        return RedirectResponse(f"/job/{job_id}?generating=1", status_code=303)
    if request.headers.get("HX-Request"):
        return render(
            request,
            "partials/job_generate_status.html",
            {"job_id": job_id, "running": True, "error": None, "skipped": None, "stage": "tailoring"},
        )
    return RedirectResponse(f"/job/{job_id}?generating=1", status_code=303)


@router.post("/job/{job_id}/generate")
def generate_materials(request: Request, job_id: int, user: dict = Depends(require_user)):
    job = get_job(job_id, user_id=user["id"])
    if not job:
        return HTMLResponse("Job not found", status_code=404)
    if not load_resume(user["id"]):
        msg = "Upload a CV on Profile before generating materials."
        if request.headers.get("HX-Request"):
            return HTMLResponse(f'<p class="text-red-600 text-sm">{msg}</p>', status_code=400)
        return RedirectResponse(f"/job/{job_id}", status_code=303)
    if not start_job_generate(job_id, user["id"], mode="full"):
        if request.headers.get("HX-Request"):
            return render(
                request,
                "partials/job_generate_status.html",
                {"job_id": job_id, "running": True, "error": None, "skipped": None, "stage": "starting"},
            )
        return RedirectResponse(f"/job/{job_id}?generating=1", status_code=303)
    if request.headers.get("HX-Request"):
        return render(
            request,
            "partials/job_generate_status.html",
            {"job_id": job_id, "running": True, "error": None, "skipped": None, "stage": "starting"},
        )
    return RedirectResponse(f"/job/{job_id}?generating=1", status_code=303)


@router.post("/job/{job_id}/apply")
def apply_job(job_id: int):
    job = get_job(job_id)
    if not job:
        return HTMLResponse("Job not found", status_code=404)

    app_rec = get_application(job_id)
    has_cv = bool(app_rec and app_rec.get("tailored_cv_path") and Path(app_rec["tailored_cv_path"]).exists())
    if not _apply_context(job, has_cv)["can_auto_apply"]:
        return RedirectResponse(f"/job/{job_id}", status_code=303)

    result = apply_to_job(job_id, force=True)
    print(f"[review] apply job {job_id}: {result}")
    return RedirectResponse(f"/job/{job_id}", status_code=303)


@router.post("/job/{job_id}/fetch-otp")
def fetch_otp_job(job_id: int):
    job = get_job(job_id)
    if not job:
        return HTMLResponse("Job not found", status_code=404)
    if job.get("status") != "needs_verification":
        return RedirectResponse(f"/job/{job_id}", status_code=303)

    result = try_auto_verify(job_id)
    print(f"[review] fetch otp job {job_id}: {result}")
    return RedirectResponse(f"/job/{job_id}", status_code=303)


@router.post("/job/{job_id}/verify")
def verify_job(job_id: int, otp: str = Form(...)):
    job = get_job(job_id)
    if not job:
        return HTMLResponse("Job not found", status_code=404)
    if job.get("status") != "needs_verification":
        return RedirectResponse(f"/job/{job_id}", status_code=303)

    result = complete_verification(job_id, otp)
    print(f"[review] verify job {job_id}: {result}")
    return RedirectResponse(f"/job/{job_id}", status_code=303)


@router.post("/job/{job_id}/retry")
def retry_job(job_id: int):
    job = get_job(job_id)
    if not job:
        return HTMLResponse("Job not found", status_code=404)
    if job.get("status") != "failed":
        return RedirectResponse(f"/job/{job_id}", status_code=303)

    result = apply_to_job(job_id, force=True)
    print(f"[review] retry apply job {job_id}: {result}")
    return RedirectResponse(f"/job/{job_id}", status_code=303)


@router.post("/job/{job_id}/approve")
def approve_job(job_id: int):
    from src.cover_letter import generate_cover_letter
    from src.tailor import tailor_cv

    app_rec = get_application(job_id)
    generate_failed = False
    if not app_rec or not app_rec.get("tailored_cv_path"):
        try:
            tailor_cv(job_id, force=True)
            generate_cover_letter(job_id, force=True)
        except Exception as e:
            generate_failed = True
            log_application_event(job_id, "generate_failed", {"message": str(e)})
    set_job_status(job_id, "approved")
    log_application_event(job_id, "approved", {})
    if generate_failed:
        # Materials are missing, so there's nothing to auto-apply with yet —
        # surface the failure instead of silently approving as if it worked.
        return RedirectResponse(f"/job/{job_id}?approve_error=1", status_code=303)
    if get_config()["auto_apply"]:
        result = apply_to_job(job_id, force=True)
        print(f"[review] apply job {job_id}: {result}")
    return RedirectResponse(f"/job/{job_id}", status_code=303)


@router.post("/job/{job_id}/reject")
def reject_job(job_id: int):
    set_job_status(job_id, "rejected")
    return RedirectResponse("/applications", status_code=303)


@router.post("/job/{job_id}/skip")
def skip_job(job_id: int):
    set_job_status(job_id, "skipped")
    return RedirectResponse("/applications", status_code=303)


@router.post("/job/{job_id}/template")
def set_job_template(
    job_id: int,
    template_id: str = Form(""),
    user: dict = Depends(require_user),
):
    job = get_job(job_id, user_id=user["id"])
    if not job:
        return HTMLResponse("Job not found", status_code=404)
    tid = int(template_id) if template_id else None
    if tid:
        tpl = get_template(tid, user["id"])
        if not tpl:
            return HTMLResponse("Template not found", status_code=404)
    set_application_template(job_id, tid, user_id=user["id"])
    return RedirectResponse(f"/job/{job_id}", status_code=303)


@router.post("/job/{job_id}/rerender-cv")
def rerender_job_cv(job_id: int, user: dict = Depends(require_user)):
    from src.tailor import rerender_cv

    job = get_job(job_id, user_id=user["id"])
    if not job:
        return HTMLResponse("Job not found", status_code=404)
    try:
        rerender_cv(job_id, user_id=user["id"])
    except RuntimeError as e:
        return HTMLResponse(str(e), status_code=400)
    return RedirectResponse(f"/job/{job_id}", status_code=303)


@router.post("/job/{job_id}/cover")
def save_cover(job_id: int, cover: str = Form(...)):
    out_dir = applications_dir() / str(job_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "cover_letter.md"
    path.write_text(cover, encoding="utf-8")
    save_application(job_id, cover_letter_path=str(path), cover_letter_text=cover)
    return RedirectResponse(f"/job/{job_id}", status_code=303)


@router.get("/download/{job_id}/cv")
def download_cv(job_id: int):
    app_rec = get_application(job_id)
    if not app_rec or not app_rec.get("tailored_cv_path"):
        return HTMLResponse("No CV", status_code=404)
    p = Path(app_rec["tailored_cv_path"])
    if not p.exists():
        p = p.with_suffix(".html")
    return FileResponse(p, filename=p.name)


@router.get("/applications/{job_id}/screenshot")
def apply_screenshot(job_id: int):
    shot = applications_dir() / str(job_id) / "apply_screenshot.png"
    if not shot.exists():
        return HTMLResponse("No screenshot", status_code=404)
    return FileResponse(shot, media_type="image/png")
