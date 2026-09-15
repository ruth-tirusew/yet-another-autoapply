"""ATS match result parsing and queue eligibility."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from src.matching.models import RequirementVerdict
from src.niche_terms import partition_strengths, posting_mentions_user_niche

# Niche-domain text matching (posting_mentions_niche_domain,
# text_mentions_niche_domain, get_user_niche_terms, etc.) moved to
# src.niche_terms — those functions don't touch JobMatchResult/CategoryScore
# and co-locating them here created a real import cycle (ats ->
# coaching.generalize_profile -> profile -> hiring_agent_bridge -> ats).
# Importers should use src.niche_terms directly.


class CategoryScore(BaseModel):
    score: int = 0
    max: int = 0
    evidence: str = ""


class JobMatchResult(BaseModel):
    overall_score: int = Field(ge=0, le=100)
    skill_match: CategoryScore
    experience_match: CategoryScore
    role_fit: CategoryScore
    gaps: list[str] = []
    strengths: list[str] = []
    recommendation: Literal["apply", "maybe", "skip"] = "maybe"
    tailoring_hints: list[str] = []
    engine: Literal["llm", "rules", "grounded"] = "llm"

    # Retrieval-grounded fields. Empty for results produced by the legacy
    # single-shot LLM and rules engines, so previously stored match_details
    # still parse.
    requirements: list[RequirementVerdict] = []
    coverage: float | None = None
    retrieval: Literal["vector", "lexical", "none", ""] = ""
    model: str = ""
    prompt_version: str = ""
    warnings: list[str] = []


class LegacyMatchResponse(BaseModel):
    """Response schema for the single-shot ``llm`` scoring mode.

    JobMatchResult carries retrieval-grounded fields that the one-shot prompt
    neither produces nor should be asked for; this keeps the structured-output
    schema sent to the provider limited to what that prompt actually returns.
    """

    overall_score: int = Field(ge=0, le=100)
    skill_match: CategoryScore
    experience_match: CategoryScore
    role_fit: CategoryScore
    gaps: list[str] = []
    strengths: list[str] = []
    recommendation: Literal["apply", "maybe", "skip"] = "maybe"
    tailoring_hints: list[str] = []


def parse_match_details(job: dict[str, Any]) -> JobMatchResult | None:
    raw = job.get("match_details")
    if not raw:
        return None
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        if "overall_score" not in data:
            return None
        return JobMatchResult(**data)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def format_ats_context(
    details: JobMatchResult,
    *,
    job_title: str = "",
    job_description: str = "",
    for_generation: bool = False,
    user_id: int | None = None,
    niche_terms: list[str] | None = None,
) -> str:
    lines = [
        f"Overall: {details.overall_score}/100 ({details.recommendation})",
        f"Skill match: {details.skill_match.score}/{details.skill_match.max} — {details.skill_match.evidence}",
        f"Experience match: {details.experience_match.score}/{details.experience_match.max} — {details.experience_match.evidence}",
        f"Role fit: {details.role_fit.score}/{details.role_fit.max} — {details.role_fit.evidence}",
    ]
    if details.strengths:
        if for_generation and (job_title or job_description):
            primary, background = partition_strengths(
                details.strengths,
                job_title=job_title,
                job_description=job_description,
                user_id=user_id,
                niche_terms=niche_terms,
            )
            if primary:
                lines.append("Strengths to emphasize for THIS job (lead with these):")
                lines.extend(f"- {s}" for s in primary)
            if background:
                lines.append(
                    "Background only — do NOT lead with these unless the posting mentions that domain:"
                )
                lines.extend(f"- {s}" for s in background)
        else:
            lines.append("Strengths to emphasize:")
            lines.extend(f"- {s}" for s in details.strengths)
    if details.gaps:
        lines.append("Gaps — do NOT claim experience in these areas:")
        lines.extend(f"- {g}" for g in details.gaps)
    if details.tailoring_hints:
        lines.append("Tailoring hints (prioritize over generic strengths):")
        lines.extend(f"- {h}" for h in details.tailoring_hints)
    if for_generation:
        if posting_mentions_user_niche(
            job_title, job_description, user_id, niche_terms=niche_terms
        ):
            niche_note = "This posting mentions the candidate's niche domain — niche experience may be relevant."
        else:
            niche_note = (
                "Lead with transferable engineering skills for this posting. "
                "Do not foreground niche-domain terminology unless it appears in the job description."
            )
        lines.append(
            f"Write for the job title and posting above. Do not reuse a generic summary. {niche_note}"
        )
    return "\n".join(lines)


def should_queue(
    result: JobMatchResult,
    threshold: int,
    *,
    role_fit_min: int = 15,
    maybe_score_boost: int = 10,
) -> tuple[bool, str]:
    if result.recommendation == "skip":
        return False, "ATS recommendation: skip"
    if result.overall_score < threshold:
        return False, f"Score {result.overall_score} below threshold {threshold}"
    if result.role_fit.score < role_fit_min:
        return False, f"Role fit {result.role_fit.score}/{result.role_fit.max} below minimum {role_fit_min}"
    if result.recommendation == "maybe" and result.overall_score < threshold + maybe_score_boost:
        return False, f"Maybe fit needs score ≥ {threshold + maybe_score_boost}"
    return True, "queued"
