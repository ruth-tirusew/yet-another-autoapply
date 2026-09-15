"""Generate tailored CV for a job."""

from __future__ import annotations

import copy
import json
import shutil
from datetime import datetime
from pathlib import Path

from src.applicant import get_applicant, merge_applicant_into_resume
from src.ats import format_ats_context, parse_match_details
from src.config import get_config
from src.render_cv import render_resume_pdf
from src.cv_templates.resolver import resolve_template_for_job
from src.db import get_application, get_job, get_profile, log_application_event, save_application
from src.settings import applications_dir
from src.hiring_agent_bridge import format_evaluation_for_match
from src.llm import chat_json, render_template
from src.profile import load_resume, resolve_resume_for_job
from src.profile_format import normalize_skills


class TailorSkipped(Exception):
    """Raised when ATS analysis says not to tailor the CV."""


def _archive_existing(job_id: int) -> None:
    app = get_application(job_id)
    if not app:
        return
    out_dir = applications_dir() / str(job_id)
    if not out_dir.exists():
        return
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    archive = out_dir / "history" / ts
    archive.mkdir(parents=True, exist_ok=True)
    for name in ("cv_tailored.json", "cv_tailored.pdf", "cv_tailored.html", "cover_letter.md"):
        src = out_dir / name
        if src.exists():
            shutil.copy2(src, archive / name)
    log_application_event(job_id, "tailored", {"archived_to": str(archive), "action": "regenerate"})


def tailor_cv(job_id: int, *, force: bool = False, user_id: int | None = None) -> Path:
    from src.tenant import resolve_user_id

    uid = resolve_user_id(user_id)
    job = get_job(job_id, user_id=uid)
    if not job:
        raise ValueError(f"Job {job_id} not found")
    resume = resolve_resume_for_job(job, uid)
    if not resume:
        raise RuntimeError("No profile loaded")
    resume = merge_applicant_into_resume(resume, get_applicant())

    match = parse_match_details(job)
    if match and not force:
        if match.recommendation == "skip":
            raise TailorSkipped(
                f"ATS recommends skip for job {job_id} ({job.get('title', '')})"
            )
        pipeline = get_config().get("pipeline", {})
        role_fit_min = int(pipeline.get("role_fit_min", 15))
        if match.role_fit.score < role_fit_min:
            raise TailorSkipped(
                f"Role fit {match.role_fit.score}/{match.role_fit.max} too low for job {job_id}"
            )

    if force:
        _archive_existing(job_id)

    master = copy.deepcopy(resume)
    hints = match.tailoring_hints if match else []
    if not hints and job.get("match_details"):
        try:
            hints = json.loads(job["match_details"]).get("tailoring_hints", [])
        except json.JSONDecodeError:
            pass

    job_description = job.get("description_full") or job.get("description_short") or ""
    ats_context = (
        format_ats_context(
            match,
            job_title=job.get("title", ""),
            job_description=job_description,
            for_generation=True,
            user_id=uid,
        )
        if match
        else "No ATS match data — emphasize profile facts only."
    )
    profile_eval = format_evaluation_for_match(
        (get_profile(uid) or {}).get("evaluation_json"),
        job_title=job.get("title", ""),
        job_description=job_description,
        for_generation=True,
        user_id=uid,
    )

    cfg = get_config()
    system = render_template("tailor_system.jinja")
    user = render_template(
        "tailor_criteria.jinja",
        resume_json=json.dumps(master, indent=2),
        job_title=job.get("title", ""),
        job_company=job.get("company", ""),
        job_description=job.get("description_full") or job.get("description_short") or "",
        tailoring_hints=hints,
        ats_context=ats_context,
        profile_evaluation=profile_eval,
    )

    try:
        patch = chat_json(user, system=system, model=cfg["quality_model"])
        tailored = merge_tailored(master, patch if isinstance(patch, dict) else {})
    except Exception as e:
        print(f"  [tailor] job {job_id} LLM fallback: {e}")
        tailored = master

    tailored = merge_applicant_into_resume(tailored, get_applicant())

    out_dir = applications_dir() / str(job_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / "cv_tailored.json"
    json_path.write_text(json.dumps(tailored, indent=2, ensure_ascii=False), encoding="utf-8")

    pdf_path = out_dir / "cv_tailored.pdf"
    cv_template = resolve_template_for_job(job_id, uid)
    cv_out = render_resume_pdf(tailored, pdf_path, template=cv_template, user_id=uid)

    save_application(
        job_id,
        tailored_cv_path=str(cv_out),
        tailored_resume_json=tailored,
        cv_template_id=cv_template.id,
    )
    log_application_event(
        job_id,
        "tailored",
        {"path": str(cv_out), "score": job.get("match_score"), "force": force},
    )
    return cv_out


def merge_tailored(master: dict, patch: dict) -> dict:
    """Keep master resume facts; apply LLM edits to summary, skill order, and highlights."""
    merged = copy.deepcopy(master)
    if not isinstance(patch, dict):
        return merged

    patch_basics = patch.get("basics")
    if isinstance(patch_basics, dict):
        for key in ("summary", "label"):
            if patch_basics.get(key):
                merged.setdefault("basics", {})[key] = patch_basics[key]

    patch_work = patch.get("work")
    if isinstance(patch_work, list) and patch_work:
        merged["work"] = patch_work

    patch_skills = patch.get("skills")
    if isinstance(patch_skills, list) and patch_skills:
        merged["skills"] = normalize_skills(patch_skills)

    patch_projects = patch.get("projects")
    if isinstance(patch_projects, list) and patch_projects:
        merged["projects"] = patch_projects

    return merged


def rerender_cv(job_id: int, *, user_id: int | None = None) -> Path:
    """Re-render tailored CV PDF/HTML from stored JSON using the resolved template."""
    from src.tenant import resolve_user_id

    uid = resolve_user_id(user_id)
    app = get_application(job_id, uid)
    if not app or not app.get("tailored_resume_json"):
        raise RuntimeError("No tailored resume to re-render")

    tailored = app["tailored_resume_json"]
    tailored = merge_applicant_into_resume(tailored, get_applicant())

    out_dir = applications_dir() / str(job_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = out_dir / "cv_tailored.pdf"
    cv_template = resolve_template_for_job(job_id, uid)
    cv_out = render_resume_pdf(tailored, pdf_path, template=cv_template, user_id=uid)

    save_application(
        job_id,
        tailored_cv_path=str(cv_out),
        tailored_resume_json=tailored,
        cv_template_id=cv_template.id,
        user_id=uid,
    )
    log_application_event(
        job_id,
        "tailored",
        {"path": str(cv_out), "action": "rerender", "template_id": cv_template.id},
    )
    return cv_out
