"""Generalized resume variant + profile-derived niche terms (cached on disk)."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from src.config import get_config
from src.db import get_profile
from src.llm import chat_json, render_template
from src.profile import load_resume
from src.settings import user_profile_dir
from src.tenant import resolve_user_id


class CachedGeneralResume(BaseModel):
    cache_key: str
    generated_at: str
    resume: dict[str, Any]


class CachedNicheTerms(BaseModel):
    cache_key: str
    generated_at: str
    terms: list[str] = Field(default_factory=list)


class GeneralizeResult(BaseModel):
    resume: dict[str, Any]
    niche_terms: list[str] = Field(default_factory=list)


def cache_key(profile: dict) -> str:
    updated = profile.get("updated_at") or ""
    return hashlib.sha256(updated.encode()).hexdigest()[:32]


def _general_path(user_id: int) -> Path:
    return user_profile_dir(user_id) / "resume_general.json"


def _terms_path(user_id: int) -> Path:
    return user_profile_dir(user_id) / "niche_terms.json"


def _load_cached_general(user_id: int) -> CachedGeneralResume | None:
    path = _general_path(user_id)
    if not path.exists():
        return None
    try:
        return CachedGeneralResume.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, ValueError, OSError):
        return None


def _load_cached_terms(user_id: int) -> CachedNicheTerms | None:
    path = _terms_path(user_id)
    if not path.exists():
        return None
    try:
        return CachedNicheTerms.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, ValueError, OSError):
        return None


def _save_cache(user_id: int, key: str, resume: dict, terms: list[str]) -> None:
    profile_dir = user_profile_dir(user_id)
    profile_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    _general_path(user_id).write_text(
        json.dumps(
            CachedGeneralResume(cache_key=key, generated_at=now, resume=resume).model_dump(),
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _terms_path(user_id).write_text(
        json.dumps(
            CachedNicheTerms(cache_key=key, generated_at=now, terms=terms).model_dump(),
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _fallback_niche_terms(resume: dict) -> list[str]:
    """Heuristic extraction when the LLM returns no niche_terms."""
    parts: list[str] = []
    for section in ("work", "projects", "skills"):
        for item in resume.get(section) or []:
            if isinstance(item, dict):
                for key in ("name", "position", "summary", "description"):
                    val = item.get(key)
                    if val:
                        parts.append(str(val))
                for kw in item.get("keywords") or item.get("technologies") or []:
                    parts.append(str(kw))
            elif isinstance(item, str):
                parts.append(item)
    basics = resume.get("basics") or {}
    if basics.get("summary"):
        parts.append(str(basics["summary"]))
    original = " ".join(parts)
    candidates: set[str] = set()
    for match in re.finditer(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3})\b", original):
        phrase = match.group(1).strip().lower()
        if len(phrase) >= 5:
            candidates.add(phrase)
    return sorted(candidates)[:20]


def _merge_extra_terms(user_id: int, terms: list[str]) -> list[str]:
    cfg = get_config()
    extra = cfg.get("matching", {}).get("niche_terms_extra") or []
    merged = {t.strip().lower() for t in terms if t and t.strip()}
    for item in extra:
        if isinstance(item, str) and item.strip():
            merged.add(item.strip().lower())
    return sorted(merged)


def _restore_work_dates(
    original_work: list[Any] | None, generalized_work: list[Any] | None
) -> list[Any]:
    """Copy startDate/endDate back from the source resume, by position.

    The generalize prompt asks the model to keep every fact, but nothing
    stops a small local model from dropping fields it doesn't think matter —
    dates included, which silently zeroes years-of-experience for any
    posting scored against this variant. Dates are objective facts the
    generalizer has no reason to rewrite, so they're restored deterministically
    here rather than left to prompt compliance.
    """
    if not isinstance(generalized_work, list):
        return generalized_work or []
    original = original_work if isinstance(original_work, list) else []
    restored = []
    for i, entry in enumerate(generalized_work):
        if not isinstance(entry, dict):
            restored.append(entry)
            continue
        entry = dict(entry)
        orig = original[i] if i < len(original) and isinstance(original[i], dict) else {}
        for field in ("startDate", "endDate"):
            if not entry.get(field) and orig.get(field):
                entry[field] = orig[field]
        restored.append(entry)
    return restored


def build_general_resume(user_id: int | None = None, *, force: bool = False) -> GeneralizeResult:
    uid = resolve_user_id(user_id)
    profile = get_profile(uid)
    if not profile:
        raise RuntimeError("No profile loaded. Run: python cli.py upload-cv <pdf>")
    resume = profile.get("resume_json") or load_resume(uid)
    if not resume:
        raise RuntimeError("No resume in profile")

    key = cache_key(profile)
    if not force:
        cached = _load_cached_general(uid)
        cached_terms = _load_cached_terms(uid)
        if (
            cached is not None
            and cached.cache_key == key
            and cached_terms is not None
            and cached_terms.cache_key == key
        ):
            return GeneralizeResult(resume=cached.resume, niche_terms=cached_terms.terms)

    cfg = get_config()
    system = render_template("generalize_resume_system.jinja")
    user = render_template(
        "generalize_resume.jinja",
        resume_json=json.dumps(resume, indent=2, ensure_ascii=False)[:14000],
    )
    data = chat_json(user, system=system, model=cfg["quality_model"])
    if not isinstance(data, dict):
        raise RuntimeError("Generalize profile returned invalid JSON")

    general = data.get("resume") if isinstance(data.get("resume"), dict) else data
    if general.get("skills") is not None:
        from src.profile_format import normalize_skills

        general = {**general, "skills": normalize_skills(general.get("skills"))}
    general = {**general, "work": _restore_work_dates(resume.get("work"), general.get("work"))}
    raw_terms = data.get("niche_terms") or []
    terms = [
        str(t).strip().lower()
        for t in raw_terms
        if isinstance(t, str) and str(t).strip()
    ]
    if not terms:
        terms = _fallback_niche_terms(resume)
    terms = _merge_extra_terms(uid, terms)
    _save_cache(uid, key, general, terms)
    return GeneralizeResult(resume=general, niche_terms=terms)


def load_general_resume(user_id: int | None = None) -> dict | None:
    uid = resolve_user_id(user_id)
    profile = get_profile(uid)
    if not profile:
        return None
    key = cache_key(profile)
    cached = _load_cached_general(uid)
    if cached is not None and cached.cache_key == key:
        return cached.resume
    return None


def load_niche_terms(user_id: int | None = None) -> list[str]:
    uid = resolve_user_id(user_id)
    profile = get_profile(uid)
    if not profile:
        return []
    key = cache_key(profile)
    cached = _load_cached_terms(uid)
    if cached is not None and cached.cache_key == key:
        return _merge_extra_terms(uid, cached.terms)
    return _merge_extra_terms(uid, [])


def generalize_status(user_id: int | None = None) -> dict[str, Any]:
    uid = resolve_user_id(user_id)
    profile = get_profile(uid)
    if not profile:
        return {"ready": False, "stale": False, "cache_key": None}
    key = cache_key(profile)
    cached = _load_cached_general(uid)
    valid = cached is not None and cached.cache_key == key
    return {
        "ready": valid,
        "stale": cached is not None and cached.cache_key != key,
        "cache_key": key,
        "generated_at": cached.generated_at if cached else None,
        "term_count": len(load_niche_terms(uid)) if valid else 0,
    }
