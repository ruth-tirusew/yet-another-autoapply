"""Coaching-driven CV draft preview and apply to profile."""

from __future__ import annotations

import copy
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from src.applicant import get_applicant, merge_applicant_into_resume
from src.coaching.profile_guide import aggregate_gaps, cache_key, load_cached_guide
from src.config import get_config
from src.db import get_profile, save_profile
from src.llm import chat_json, render_template
from src.render_cv import render_resume_html, render_resume_pdf
from src.cv_templates.resolver import resolve_template_for_user
from src.settings import user_profile_dir
from src.tenant import resolve_user_id
from src.tailor import merge_tailored


class CvDraftCache(BaseModel):
    guide_cache_key: str
    generated_at: str
    resume: dict = Field(default_factory=dict)


def _draft_path(user_id: int) -> Path:
    return user_profile_dir(user_id) / "cv_draft.json"


def _guide_cache_key(user_id: int) -> str | None:
    profile = get_profile(user_id)
    if not profile:
        return None
    freq = aggregate_gaps(user_id)
    return cache_key(profile, freq)


def load_cv_draft(user_id: int | None = None) -> CvDraftCache | None:
    uid = resolve_user_id(user_id)
    path = _draft_path(uid)
    if not path.exists():
        return None
    try:
        return CvDraftCache(**json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError, ValueError):
        return None


def save_cv_draft(user_id: int, guide_key: str, resume: dict) -> CvDraftCache:
    cached = CvDraftCache(
        guide_cache_key=guide_key,
        generated_at=datetime.now(timezone.utc).isoformat(),
        resume=resume,
    )
    path = _draft_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(cached.model_dump_json(indent=2), encoding="utf-8")
    return cached


def cv_draft_status(user_id: int | None = None) -> dict[str, Any]:
    uid = resolve_user_id(user_id)
    guide_key = _guide_cache_key(uid)
    draft = load_cv_draft(uid)
    valid = (
        draft is not None
        and guide_key is not None
        and draft.guide_cache_key == guide_key
    )
    return {
        "ready": valid,
        "stale": draft is not None and guide_key is not None and draft.guide_cache_key != guide_key,
        "generated_at": draft.generated_at if draft else None,
        "resume": draft.resume if valid else None,
    }


def build_cv_draft(
    user_id: int | None = None,
    cv_tips: list[dict] | None = None,
    *,
    force: bool = False,
    use_llm: bool = True,
) -> dict:
    uid = resolve_user_id(user_id)
    profile = get_profile(uid)
    if not profile:
        raise RuntimeError("No profile loaded")

    guide_key = _guide_cache_key(uid)
    if not guide_key:
        raise RuntimeError("Could not compute guide cache key")

    if not force:
        draft = load_cv_draft(uid)
        if draft and draft.guide_cache_key == guide_key:
            return draft.resume

    if cv_tips is None:
        cached_guide = load_cached_guide(uid)
        if not cached_guide or cached_guide.cache_key != guide_key:
            raise RuntimeError("Generate a profile guide first")
        cv_tips = [t.model_dump() for t in cached_guide.report.cv_tips]

    resume = copy.deepcopy(profile.get("resume_json") or {})
    resume = merge_applicant_into_resume(resume, get_applicant())

    if not use_llm:
        save_cv_draft(uid, guide_key, resume)
        return resume

    cfg = get_config()
    system = render_template("cv_preview_system.jinja")
    user_prompt = render_template(
        "cv_preview_criteria.jinja",
        resume_json=json.dumps(resume, indent=2),
        cv_tips=cv_tips,
    )
    try:
        patch = chat_json(user_prompt, system=system, model=cfg["quality_model"])
        improved = merge_tailored(resume, patch if isinstance(patch, dict) else {})
    except Exception as e:
        raise RuntimeError(f"CV draft generation failed: {e}") from e

    improved = merge_applicant_into_resume(improved, get_applicant())
    save_cv_draft(uid, guide_key, improved)
    return improved


def apply_cv_draft(user_id: int | None = None) -> dict[str, Any]:
    uid = resolve_user_id(user_id)
    status = cv_draft_status(uid)
    if not status["ready"] or not status["resume"]:
        raise RuntimeError("No valid CV draft to apply. Generate a preview first.")

    profile = get_profile(uid)
    if not profile:
        raise RuntimeError("No profile loaded")

    profile_dir = user_profile_dir(uid)
    profile_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    archive = profile_dir / "history" / ts
    archive.mkdir(parents=True, exist_ok=True)
    resume_path = profile_dir / "resume.json"
    if resume_path.exists():
        shutil.copy2(resume_path, archive / "resume.json")
    source_pdf = profile.get("source_pdf_path") or str(profile_dir / "source_cv.pdf")
    if source_pdf and Path(source_pdf).exists():
        shutil.copy2(source_pdf, archive / "source_cv.pdf")

    draft_resume = status["resume"]
    resume_path.write_text(
        json.dumps(draft_resume, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    pdf_path = profile_dir / "source_cv.pdf"
    cv_template = resolve_template_for_user(uid)
    out = render_resume_pdf(draft_resume, pdf_path, template=cv_template, user_id=uid)
    source_pdf_str = str(out)

    save_profile(
        draft_resume,
        source_pdf_str,
        profile.get("github_json"),
        evaluation_json=None,
        user_id=uid,
    )

    guide_path = profile_dir / "guide.json"
    if guide_path.exists():
        guide_path.unlink()

    return {
        "applied": True,
        "archive": str(archive),
        "pdf_path": source_pdf_str,
    }


def get_cv_draft_html(user_id: int | None = None) -> str:
    status = cv_draft_status(user_id)
    if not status["ready"] or not status["resume"]:
        return "<p>No CV draft available. Generate a preview from Coaching first.</p>"
    return render_resume_html(status["resume"], user_id=user_id)
