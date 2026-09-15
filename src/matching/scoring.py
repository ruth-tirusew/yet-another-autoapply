"""Stage 2 aggregation — turn requirement verdicts into scores, deterministically.

Nothing here asks a model for a number. The LLM judges individual
requirement/evidence pairs; every score below is recomputed from those
verdicts, so the same verdicts always produce the same result and a score can
always be traced back to the requirements that produced it.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Sequence

from src.ats import CategoryScore, JobMatchResult
from src.matching.chunking import _NICE_MARKER
from src.matching.models import Requirement, RequirementSet, RequirementVerdict
from src.rule_engine import _parse_ym

SKILL_MAX = 40
EXPERIENCE_MAX = 35
ROLE_FIT_MAX = 25

DEFAULT_SENIORITY_LEVELS = [
    "intern", "junior", "mid", "senior", "staff", "principal",
    "lead", "manager", "director", "vp",
]

# A years-of-experience figure only counts when it is stated about the
# applicant. Postings routinely mention years about the company or product.
_YEARS_RE = re.compile(
    r"(\d{1,2})\s*\+?\s*(?:-|to|–)?\s*(?:\d{1,2})?\s*\+?\s*(?:years?|yrs?)",
    re.IGNORECASE,
)
_APPLICANT_CONTEXT = re.compile(
    r"\b(?:experience|background|working|worked|developing|building|"
    r"professional|hands[-\s]?on|industry|track record)\b",
    re.IGNORECASE,
)
_COMPANY_CONTEXT = re.compile(
    r"\b(?:we(?:'ve| have)?\s+(?:been|spent)|our\s+(?:company|team|history|product)|"
    r"founded|since\s+\d{4}|in\s+business|over\s+the\s+(?:past|last)\s+\d+\s+years\s+"
    r"(?:we|our))\b",
    re.IGNORECASE,
)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?;:])\s+|[\n\r]+|(?:^|\s)[-*•·]\s+")


def extract_required_years(text: str) -> int | None:
    """Years of experience the posting requires *of the applicant*, if stated.

    Judged per sentence: a figure only counts when its own sentence talks
    about the candidate's experience, is not describing the company, and is
    not framed as a nice-to-have. The previous implementation took the first
    "N years" match anywhere in the posting, which routinely picked up
    company history or benefits copy.

    Returns the smallest qualifying figure, so "3-5 years" and "5+ years
    (8+ for senior)" both read as the entry bar rather than the ceiling.
    """
    if not text:
        return None
    found: list[int] = []
    for sentence in _SENTENCE_SPLIT.split(text):
        if not sentence or not _YEARS_RE.search(sentence):
            continue
        if _COMPANY_CONTEXT.search(sentence):
            continue
        if not _APPLICANT_CONTEXT.search(sentence):
            continue
        if _NICE_MARKER.search(sentence):
            continue  # "2 years of Go is a plus" is not the bar to clear
        for match in _YEARS_RE.finditer(sentence):
            try:
                found.append(int(match.group(1)))
            except ValueError:
                continue
    return min(found) if found else None


def years_of_experience(work: Sequence[dict] | None, *, today: date | None = None) -> float:
    """Total worked years, merging overlapping roles and excluding gaps.

    The rules engine measured earliest-start to latest-end, which counts
    career breaks as experience; this sums the union of employment intervals.
    """
    if not work:
        return 0.0
    today = today or date.today()
    now = (today.year, today.month)
    spans: list[tuple[int, int]] = []
    for entry in work:
        if not isinstance(entry, dict):
            continue
        start = _parse_ym(entry.get("startDate"))
        if start is None:
            continue
        end_raw = entry.get("endDate")
        end = _parse_ym(end_raw)
        if end is None:
            end = now  # blank / "Present" / "Current"
        start_m = start[0] * 12 + start[1]
        end_m = end[0] * 12 + end[1]
        if end_m > start_m:
            spans.append((start_m, end_m))
    if not spans:
        return 0.0
    spans.sort()
    merged: list[list[int]] = [list(spans[0])]
    for start_m, end_m in spans[1:]:
        if start_m <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end_m)
        else:
            merged.append([start_m, end_m])
    months = sum(end - start for start, end in merged)
    return round(months / 12.0, 2)


def coverage(verdicts: Sequence[RequirementVerdict]) -> float:
    """Weighted share of requirements the resume covers (0.0–1.0)."""
    total = sum(v.weight for v in verdicts)
    if total <= 0:
        return 0.0
    earned = sum(v.weight * v.credit for v in verdicts)
    return round(earned / total, 4)


def compute_skill_match(verdicts: Sequence[RequirementVerdict]) -> tuple[CategoryScore, float]:
    """Score requirement coverage — measured over *job requirements*, not resume terms."""
    skill_verdicts = [v for v in verdicts if v.category in ("skill", "experience", "responsibility", "education", "other")]
    if not skill_verdicts:
        return (
            CategoryScore(score=0, max=SKILL_MAX, evidence="No requirements extracted from posting."),
            0.0,
        )
    cov = coverage(skill_verdicts)
    met = [v for v in skill_verdicts if v.verdict == "met"]
    partial = [v for v in skill_verdicts if v.verdict == "partial"]
    missing_musts = [v for v in skill_verdicts if v.verdict == "missing" and v.kind == "must"]
    evidence = (
        f"{len(met)} met, {len(partial)} partial, {len(missing_musts)} required gaps "
        f"across {len(skill_verdicts)} requirements ({cov * 100:.0f}% weighted coverage)"
    )
    if missing_musts:
        evidence += " — missing: " + "; ".join(v.text[:60] for v in missing_musts[:4])
    return CategoryScore(score=round(cov * SKILL_MAX), max=SKILL_MAX, evidence=evidence), cov


def compute_experience_match(
    *,
    years_required: int | None,
    candidate_years: float,
) -> CategoryScore:
    if years_required is None:
        return CategoryScore(
            score=round(EXPERIENCE_MAX * 0.85),
            max=EXPERIENCE_MAX,
            evidence=(
                f"Posting states no years requirement; candidate has ~{candidate_years:.1f} years."
            ),
        )
    gap = years_required - candidate_years
    if gap <= 0:
        ratio = 1.0
    elif gap <= 1:
        ratio = 0.75
    elif gap <= 2:
        ratio = 0.45
    else:
        ratio = 0.15
    return CategoryScore(
        score=round(EXPERIENCE_MAX * ratio),
        max=EXPERIENCE_MAX,
        evidence=(
            f"Posting requires ~{years_required} years; candidate has ~{candidate_years:.1f} years."
        ),
    )


def compute_role_fit(
    *,
    posting_seniority: str,
    candidate_seniority: str,
    levels: Sequence[str] | None = None,
    job_title: str = "",
) -> CategoryScore:
    levels = list(levels or DEFAULT_SENIORITY_LEVELS)
    posting = (posting_seniority or "").strip().lower()
    candidate = (candidate_seniority or "").strip().lower()
    if posting not in levels or candidate not in levels:
        return CategoryScore(
            score=round(ROLE_FIT_MAX * 0.6),
            max=ROLE_FIT_MAX,
            evidence=(
                f"Seniority not determinable from '{job_title or posting_seniority}' — treated as neutral."
            ),
        )
    distance = abs(levels.index(posting) - levels.index(candidate))
    ratio = {0: 1.0, 1: 0.75, 2: 0.35}.get(distance, 0.1)
    return CategoryScore(
        score=round(ROLE_FIT_MAX * ratio),
        max=ROLE_FIT_MAX,
        evidence=f"Posting targets '{posting}', candidate is '{candidate}' (distance {distance}).",
    )


def derive_recommendation(
    *,
    overall: int,
    verdicts: Sequence[RequirementVerdict],
    role_fit: CategoryScore,
    threshold: int,
    role_fit_min: int,
    must_coverage_min: float = 0.5,
) -> tuple[str, str]:
    """Deterministic apply/maybe/skip, with the reason that decided it."""
    musts = [v for v in verdicts if v.kind == "must"]
    must_cov = coverage(musts) if musts else 1.0
    if role_fit.score < role_fit_min:
        return "skip", f"Role fit {role_fit.score}/{role_fit.max} below minimum {role_fit_min}"
    if musts and must_cov < must_coverage_min:
        return "skip", f"Only {must_cov * 100:.0f}% of required items covered"
    if overall >= threshold:
        return "apply", f"Score {overall} at or above threshold {threshold}"
    if overall >= threshold - 15:
        return "maybe", f"Score {overall} within 15 of threshold {threshold}"
    return "skip", f"Score {overall} well below threshold {threshold}"


def build_result(
    *,
    verdicts: Sequence[RequirementVerdict],
    req_set: RequirementSet,
    candidate_years: float,
    candidate_seniority: str,
    job_title: str,
    threshold: int,
    role_fit_min: int,
    seniority_levels: Sequence[str] | None = None,
    retrieval: str = "",
    model: str = "",
    prompt_version: str = "",
    warnings: Sequence[str] = (),
) -> JobMatchResult:
    """Assemble the final result. ``overall_score`` is the sum of its parts, always."""
    skill, cov = compute_skill_match(verdicts)
    experience = compute_experience_match(
        years_required=req_set.years_required, candidate_years=candidate_years
    )
    role_fit = compute_role_fit(
        posting_seniority=req_set.seniority,
        candidate_seniority=candidate_seniority,
        levels=seniority_levels,
        job_title=job_title,
    )
    overall = min(100, max(0, skill.score + experience.score + role_fit.score))
    recommendation, reason = derive_recommendation(
        overall=overall,
        verdicts=verdicts,
        role_fit=role_fit,
        threshold=threshold,
        role_fit_min=role_fit_min,
    )

    strengths = [
        f"{v.text} — {v.evidence[0].ref}" if v.evidence and v.evidence[0].ref else v.text
        for v in verdicts
        if v.verdict == "met"
    ][:8]
    gaps = [v.text for v in verdicts if v.verdict == "missing" and v.kind == "must"][:8]
    hints = [
        f"Make '{v.text}' explicit — closest evidence: {v.evidence[0].ref or v.evidence[0].chunk_id}"
        if v.evidence
        else f"Address '{v.text}' if you have relevant experience"
        for v in verdicts
        if v.verdict == "partial"
    ][:8]

    return JobMatchResult(
        overall_score=overall,
        skill_match=skill,
        experience_match=experience,
        role_fit=role_fit,
        gaps=gaps,
        strengths=strengths,
        recommendation=recommendation,  # type: ignore[arg-type]
        tailoring_hints=hints,
        engine="grounded",
        requirements=list(verdicts),
        coverage=cov,
        retrieval=retrieval or "none",  # type: ignore[arg-type]
        model=model,
        prompt_version=prompt_version,
        warnings=[w for w in warnings if w] + ([reason] if recommendation == "skip" else []),
    )
