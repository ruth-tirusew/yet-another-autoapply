"""PDF upload → JSONResume profile store."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from src.db import get_profile, save_profile
from src.hiring_agent_bridge import (
    HiringAgentError,
    evaluate_profile,
    extract_resume_from_pdf,
    fetch_github_for_resume,
)
from src.llm import chat_json
from src.niche_terms import posting_mentions_user_niche
from src.settings import get_config, user_profile_dir
from src.tenant import resolve_user_id


def _profile_dir(user_id: int | None = None) -> Path:
    uid = resolve_user_id(user_id)
    return user_profile_dir(uid)


def extract_with_ollama(pdf_path: str, user_id: int | None = None) -> dict:
    import pymupdf

    cfg = get_config()
    doc = pymupdf.open(pdf_path)
    text = "\n".join(page.get_text() for page in doc)
    doc.close()

    system = "Extract resume data into JSON Resume format. Only include facts from the text. Return valid JSON only."
    user = f"Keys: basics, work, education, skills, projects, awards.\n\nResume text:\n{text[:12000]}"
    return chat_json(user, system=system, model=cfg["quality_model"])


def upload_cv(
    pdf_path: str,
    use_github: bool = True,
    evaluate: bool = True,
    user_id: int | None = None,
) -> dict:
    uid = resolve_user_id(user_id)
    src = Path(pdf_path).resolve()
    if not src.exists():
        raise FileNotFoundError(pdf_path)

    profile_dir = _profile_dir(uid)
    profile_dir.mkdir(parents=True, exist_ok=True)
    dest = profile_dir / "source_cv.pdf"
    shutil.copy2(src, dest)

    warnings: list[str] = []

    print("[profile] Extracting CV with hiring_agent…")
    try:
        resume = extract_resume_from_pdf(str(dest))
    except HiringAgentError as e:
        warnings.append(
            f"hiring_agent PDF extraction failed ({e}); used a simpler fallback extractor instead."
        )
        resume = extract_with_ollama(str(dest), uid)
    (profile_dir / "resume.json").write_text(
        json.dumps(resume, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    github_data = fetch_github_for_resume(resume) if use_github else {}

    evaluation = None
    ha_cfg = get_config().get("hiring_agent", {})
    if evaluate and ha_cfg.get("evaluate_on_upload", True):
        print("[profile] Running hiring_agent profile evaluation…")
        try:
            evaluation = evaluate_profile(resume, github_data)
        except HiringAgentError as e:
            warnings.append(f"Profile evaluation failed ({e}); CV was saved without a score.")

    save_profile(resume, str(dest), github_data, evaluation, user_id=uid)
    name = (resume.get("basics") or {}).get("name", "candidate")
    work_n = len(resume.get("work") or [])
    proj_n = len(resume.get("projects") or [])
    print(f"Profile saved for {name} ({work_n} work, {proj_n} projects)")
    if evaluation:
        from src.hiring_agent_bridge import evaluation_total_score

        print(f"  Profile evaluation score: {evaluation_total_score(evaluation)}/100")

    try:
        from src.coaching.generalize_profile import build_general_resume

        print("[profile] Building generalized resume variant…")
        result = build_general_resume(uid)
        print(f"  Generalized profile ready ({len(result.niche_terms)} niche terms)")
    except Exception as e:
        print(f"  [profile] Generalize skipped: {e}")

    return {**(get_profile(uid) or {}), "warnings": warnings}


def evaluate_stored_profile(user_id: int | None = None) -> dict | None:
    """Re-run hiring_agent evaluation on the current profile."""
    uid = resolve_user_id(user_id)
    prof = get_profile(uid)
    if not prof:
        raise RuntimeError("No profile loaded. Run: python cli.py upload-cv <pdf>")
    evaluation = evaluate_profile(prof["resume_json"], prof.get("github_json"))
    if evaluation:
        save_profile(
            prof["resume_json"],
            prof["source_pdf_path"],
            prof.get("github_json"),
            evaluation,
            user_id=uid,
        )
    return evaluation


def load_resume(user_id: int | None = None) -> dict | None:
    prof = get_profile(user_id)
    return prof["resume_json"] if prof else None


def resolve_resume_variant_for_job(
    job: dict[str, Any], user_id: int | None = None
) -> tuple[dict, str]:
    """Return ``(resume, variant)`` — the niche resume only when the posting asks for it.

    Callers that score a batch use the variant name as a cache key, so the
    per-batch match context is built once per variant instead of once per job.
    """
    uid = resolve_user_id(user_id)
    niche = load_resume(uid)
    if not niche:
        raise RuntimeError("No profile loaded. Run: python cli.py upload-cv <pdf>")

    # generalize_profile imports src.profile at its own top level (it needs
    # load_resume), so this one stays deferred to call time — a genuine
    # direct two-node cycle, not a style choice.
    from src.coaching.generalize_profile import load_general_resume

    title = job.get("title", "") or ""
    description = job.get("description_full") or job.get("description_short") or ""
    if posting_mentions_user_niche(title, description, uid):
        return niche, "niche"

    general = load_general_resume(uid)
    return (general, "general") if general else (niche, "niche")


def resolve_resume_for_job(job: dict[str, Any], user_id: int | None = None) -> dict:
    """Return general resume unless the posting mentions the user's niche terms."""
    return resolve_resume_variant_for_job(job, user_id)[0]
