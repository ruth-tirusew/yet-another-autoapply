"""Deterministic, config-driven job-match scoring — an alternative to the LLM matcher.

Pure functions only: no src.db / src.llm imports, so this module is trivially
unit-testable and safe to call from any scoring mode (rules or hybrid).
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from src.ats import CategoryScore, JobMatchResult
from src.profile_format import normalize_skills

SKILL_MAX = 40
EXPERIENCE_MAX = 35
ROLE_FIT_MAX = 25

_YM_RE = re.compile(r"^(\d{4})(?:-(\d{1,2}))?$")
_ONGOING_MARKERS = {"", "present", "current", "now"}
_YEARS_REQ_RE = re.compile(r"(\d{1,2})\+?(?:\s*(?:-|to)\s*\d{1,2}\+?)?\s*years?", re.IGNORECASE)

UNCLASSIFIED_MARKER = "[unclassified]"


def _parse_ym(value: Any) -> tuple[int, int] | None:
    """Parse a resume date string into (year, month). None means 'ongoing/now'."""
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in _ONGOING_MARKERS:
        return None
    match = _YM_RE.match(text)
    if not match:
        return None
    year = int(match.group(1))
    month = int(match.group(2)) if match.group(2) else 1
    return (year, month)


def _years_of_experience(work: list[dict] | None, *, today: date | None = None) -> float:
    """Career length: earliest startDate in work[] through the latest endDate (or now)."""
    if not work:
        return 0.0
    today = today or date.today()
    starts: list[tuple[int, int]] = []
    has_ongoing = False
    latest_end: tuple[int, int] | None = None
    for entry in work:
        if not isinstance(entry, dict):
            continue
        start = _parse_ym(entry.get("startDate"))
        if start is not None:
            starts.append(start)
        end_raw = entry.get("endDate")
        end = _parse_ym(end_raw)
        if end is None:
            # Blank/"Present"/None endDate means ongoing, but only when there's
            # something to anchor it (a start date, or an explicit marker).
            if start is not None or (end_raw is not None and str(end_raw).strip()):
                has_ongoing = True
        elif latest_end is None or end > latest_end:
            latest_end = end
    if not starts:
        return 0.0
    earliest = min(starts)
    end_point = (today.year, today.month) if has_ongoing else (latest_end or earliest)
    months = (end_point[0] - earliest[0]) * 12 + (end_point[1] - earliest[1])
    return max(0.0, months / 12.0)


def _extract_required_years(text: str) -> int | None:
    if not text:
        return None
    match = _YEARS_REQ_RE.search(text)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _classify_seniority(title: str, levels: list[str]) -> int | None:
    if not title or not levels:
        return None
    lowered = title.lower()
    for idx, level in sorted(enumerate(levels), key=lambda p: -len(p[1])):
        if level and level.lower() in lowered:
            return idx
    return None


def _flatten_resume_terms(resume: dict, synonyms: dict[str, str]) -> set[str]:
    skills = normalize_skills((resume or {}).get("skills"))
    terms: set[str] = set()
    for skill in skills:
        name = str(skill.get("name") or "").strip().lower()
        if name:
            terms.add(synonyms.get(name, name))
        for kw in skill.get("keywords") or []:
            kw_l = str(kw).strip().lower()
            if kw_l:
                terms.add(synonyms.get(kw_l, kw_l))
    return terms


def compute_skill_match(resume: dict, job_text: str, rules_cfg: dict) -> CategoryScore:
    synonyms = {str(k).lower(): str(v).lower() for k, v in (rules_cfg.get("synonyms") or {}).items()}
    terms = _flatten_resume_terms(resume, synonyms)
    text = (job_text or "").lower()
    if not terms:
        return CategoryScore(score=0, max=SKILL_MAX, evidence="No resume skills to match against.")

    matched = sorted(t for t in terms if t and t in text)
    missing = sorted(t for t in terms if t not in matched)[:8]
    coverage = len(matched) / len(terms)
    score = round(coverage * SKILL_MAX)

    parts = []
    if matched:
        parts.append(f"matched: {', '.join(matched[:10])}")
    if missing:
        parts.append(f"not mentioned in posting: {', '.join(missing)}")
    evidence = "; ".join(parts) or "No overlap found."
    return CategoryScore(score=score, max=SKILL_MAX, evidence=evidence)


def compute_experience_match(resume: dict, job_text: str, rules_cfg: dict) -> CategoryScore:
    years = _years_of_experience((resume or {}).get("work"))
    required = _extract_required_years(job_text or "")

    if required is None:
        return CategoryScore(
            score=round(EXPERIENCE_MAX * 0.85),
            max=EXPERIENCE_MAX,
            evidence=f"Posting states no explicit years requirement; candidate has ~{years:.1f} years.",
        )

    gap = required - years
    if gap <= 0:
        ratio = 1.0
    elif gap <= 1:
        ratio = 0.7
    elif gap <= 2:
        ratio = 0.4
    else:
        ratio = 0.15
    score = round(EXPERIENCE_MAX * ratio)
    evidence = f"Posting requires ~{required} years; candidate has ~{years:.1f} years."
    return CategoryScore(score=score, max=EXPERIENCE_MAX, evidence=evidence)


def compute_role_fit(resume: dict, job_title: str, rules_cfg: dict) -> CategoryScore:
    levels = list(rules_cfg.get("seniority_levels") or [])
    candidate_level_name = str(rules_cfg.get("candidate_seniority") or "").lower()
    candidate_idx = levels.index(candidate_level_name) if candidate_level_name in levels else None
    job_idx = _classify_seniority(job_title or "", levels)

    if job_idx is None or candidate_idx is None:
        return CategoryScore(
            score=round(ROLE_FIT_MAX * 0.6),
            max=ROLE_FIT_MAX,
            evidence=f"Could not classify seniority from title — treated as neutral. {UNCLASSIFIED_MARKER}",
        )

    distance = abs(job_idx - candidate_idx)
    if distance == 0:
        ratio = 1.0
    elif distance == 1:
        ratio = 0.75
    elif distance == 2:
        ratio = 0.35
    else:
        ratio = 0.1
    score = round(ROLE_FIT_MAX * ratio)
    evidence = (
        f"Job level '{levels[job_idx]}' vs candidate level '{levels[candidate_idx]}' "
        f"(distance {distance})."
    )
    return CategoryScore(score=score, max=ROLE_FIT_MAX, evidence=evidence)


def score_job_rules(
    resume: dict,
    job: dict,
    rules_cfg: dict,
    *,
    threshold: int = 70,
    role_fit_min: int = 15,
) -> JobMatchResult:
    job_title = job.get("title", "") or ""
    job_description = job.get("description_full") or job.get("description_short") or ""
    job_text = f"{job_title}\n{job_description}"

    skill = compute_skill_match(resume, job_text, rules_cfg)
    experience = compute_experience_match(resume, job_text, rules_cfg)
    role_fit = compute_role_fit(resume, job_title, rules_cfg)
    overall = skill.score + experience.score + role_fit.score

    strengths: list[str] = []
    gaps: list[str] = []
    if "matched: " in skill.evidence:
        matched_part = skill.evidence.split("matched: ", 1)[1].split(";")[0]
        strengths.extend(t.strip() for t in matched_part.split(",") if t.strip())
    if "not mentioned in posting: " in skill.evidence:
        missing_part = skill.evidence.split("not mentioned in posting: ", 1)[1]
        gaps.extend(t.strip() for t in missing_part.split(",") if t.strip())

    if role_fit.score < role_fit_min:
        recommendation = "skip"
    elif overall >= threshold:
        recommendation = "apply"
    elif overall >= threshold - 15:
        recommendation = "maybe"
    else:
        recommendation = "skip"

    return JobMatchResult(
        overall_score=min(100, max(0, overall)),
        skill_match=skill,
        experience_match=experience,
        role_fit=role_fit,
        gaps=gaps,
        strengths=strengths,
        recommendation=recommendation,
        tailoring_hints=[],
        engine="rules",
    )
