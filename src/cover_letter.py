"""Cover letter generation."""

from __future__ import annotations

from pathlib import Path

from src.applicant import get_applicant, merge_applicant_into_resume
from src.ats import format_ats_context, parse_match_details
from src.config import get_config
from src.settings import applications_dir
from src.db import get_job, get_profile, log_application_event, save_application
from src.hiring_agent_bridge import format_evaluation_for_match
from src.llm import chat, render_template
from src.profile import resolve_resume_for_job
from src.tenant import resolve_user_id
from src.profile_format import format_experience_text, summary_for_job


class CoverLetterSkipped(Exception):
    """Raised when ATS analysis says not to generate a cover letter."""


def generate_cover_letter(job_id: int, *, force: bool = False, user_id: int | None = None) -> Path:
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
            raise CoverLetterSkipped(
                f"ATS recommends skip for job {job_id} ({job.get('title', '')})"
            )
        pipeline = get_config().get("pipeline", {})
        role_fit_min = int(pipeline.get("role_fit_min", 15))
        if match.role_fit.score < role_fit_min:
            raise CoverLetterSkipped(
                f"Role fit {match.role_fit.score}/{match.role_fit.max} too low for job {job_id}"
            )

    basics = resume.get("basics") or {}
    name = basics.get("name", "Candidate")
    job_title = job.get("title", "")
    job_company = job.get("company", "")
    job_description = job.get("description_full") or job.get("description_short") or ""
    summary = summary_for_job(
        basics.get("summary", "") or "",
        job_title=job_title,
        job_description=job_description,
        user_id=uid,
    )

    cfg = get_config()
    cl_cfg = cfg.get("cover_letter", {})
    ats_context = (
        format_ats_context(
            match,
            job_title=job_title,
            job_description=job_description,
            for_generation=True,
            user_id=uid,
        )
        if match
        else "No ATS match data — use profile facts only."
    )
    profile_eval = format_evaluation_for_match(
        (get_profile(uid) or {}).get("evaluation_json"),
        job_title=job_title,
        job_description=job_description,
        for_generation=True,
        user_id=uid,
    )

    system = render_template("cover_letter_system.jinja")
    user = render_template(
        "cover_letter_criteria.jinja",
        name=name,
        summary=summary or "",
        experience_text=format_experience_text(
            resume,
            job_title=job_title,
            job_description=job_description,
            user_id=uid,
        ),
        job_title=job_title,
        job_company=job_company,
        job_description=job_description,
        max_words=cl_cfg.get("max_words", 350),
        tone=cl_cfg.get("tone", "concise"),
        ats_context=ats_context,
        profile_evaluation=profile_eval,
        match_score=job.get("match_score"),
    )

    text = chat(user, system=system, model=cfg["quality_model"]).strip()

    out_dir = applications_dir() / str(job_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / "cover_letter.md"
    md_path.write_text(text, encoding="utf-8")

    save_application(job_id, cover_letter_path=str(md_path), cover_letter_text=text)
    log_application_event(
        job_id,
        "cover_generated",
        {"path": str(md_path), "score": job.get("match_score"), "force": force},
    )
    return md_path
