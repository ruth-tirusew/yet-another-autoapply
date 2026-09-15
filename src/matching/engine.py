"""Wire the two stages together into a single grounded match.

``MatchContext`` holds everything that is constant across a batch — resume
chunks, their vectors, candidate seniority and years — so a 50-job run builds
it once instead of re-deriving it per job as the previous matcher did.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Sequence

from src.ats import JobMatchResult
from src.matching import judge as judge_mod
from src.matching import requirements as req_mod
from src.matching.chunking import chunk_resume
from src.matching.models import RequirementSet, ResumeChunk
from src.matching.retrieval import Retriever
from src.matching.scoring import (
    DEFAULT_SENIORITY_LEVELS,
    build_result,
    years_of_experience,
)

logger = logging.getLogger(__name__)

PROMPT_VERSION = f"{req_mod.PROMPT_VERSION}+{judge_mod.PROMPT_VERSION}"


@dataclass
class MatchContext:
    """Per-batch state: built once, reused for every job in the run."""

    user_id: int
    resume: dict[str, Any]
    chunks: list[ResumeChunk]
    retriever: Retriever
    candidate_years: float
    candidate_seniority: str
    candidate_summary: str
    threshold: int
    role_fit_min: int
    seniority_levels: list[str]
    model: str
    allow_llm: bool = True
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def build(
        cls,
        resume: dict[str, Any],
        *,
        user_id: int,
        cfg: dict[str, Any],
        allow_llm: bool = True,
        allow_vectors: bool = True,
    ) -> "MatchContext":
        matching = cfg.get("matching", {}) or {}
        rules = matching.get("rules", {}) or {}
        pipeline = cfg.get("pipeline", {}) or {}
        chunks = chunk_resume(resume)
        retriever = Retriever(
            chunks,
            user_id=user_id,
            owner_prefix=f"u{user_id}:",
            allow_vectors=allow_vectors,
        )
        warnings = [retriever.warning] if retriever.warning else []
        return cls(
            user_id=user_id,
            resume=resume,
            chunks=chunks,
            retriever=retriever,
            candidate_years=years_of_experience(resume.get("work")),
            candidate_seniority=str(rules.get("candidate_seniority") or "").lower(),
            candidate_summary=str((resume.get("basics") or {}).get("summary") or ""),
            threshold=int(cfg.get("match_threshold", 70)),
            role_fit_min=int(pipeline.get("role_fit_min", 15)),
            seniority_levels=list(rules.get("seniority_levels") or DEFAULT_SENIORITY_LEVELS),
            model=str(cfg.get("quality_model") or ""),
            allow_llm=allow_llm,
            warnings=warnings,
        )


def grounded_match(
    job: dict[str, Any],
    resume: dict[str, Any],
    *,
    user_id: int,
    cfg: dict[str, Any],
    context: MatchContext | None = None,
    allow_llm: bool = True,
    req_set: RequirementSet | None = None,
) -> JobMatchResult:
    """Score one job: extract requirements, retrieve evidence, judge, aggregate."""
    ctx = context or MatchContext.build(resume, user_id=user_id, cfg=cfg, allow_llm=allow_llm)

    if req_set is None:
        req_set = req_mod.extract_requirements(job, allow_llm=ctx.allow_llm)

    warnings = list(ctx.warnings)
    if req_set.engine == "heuristic":
        warnings.append("Requirements extracted without a model (bullet heuristics).")
    if not req_set.requirements:
        warnings.append("No requirements could be extracted from this posting.")

    job_key = str(job.get("catalog_job_id") or job.get("id") or "")
    retrieved = ctx.retriever.retrieve(req_set.requirements, job_key=job_key)
    if ctx.retriever.warning and ctx.retriever.warning not in warnings:
        warnings.append(ctx.retriever.warning)

    verdicts: Sequence = []
    if req_set.requirements:
        if ctx.allow_llm:
            try:
                verdicts = judge_mod.judge_requirements(
                    req_set.requirements,
                    retrieved,
                    candidate_summary=ctx.candidate_summary,
                    model=ctx.model or None,
                )
            except Exception as e:
                logger.warning("[match] judge failed for job %s: %s", job.get("id"), e)
                warnings.append(f"Requirement judging fell back to retrieval only ({e}).")
                verdicts = judge_mod.lexical_verdicts(req_set.requirements, retrieved)
        else:
            verdicts = judge_mod.lexical_verdicts(req_set.requirements, retrieved)

    return build_result(
        verdicts=verdicts,
        req_set=req_set,
        candidate_years=ctx.candidate_years,
        candidate_seniority=ctx.candidate_seniority,
        job_title=job.get("title", "") or "",
        threshold=ctx.threshold,
        role_fit_min=ctx.role_fit_min,
        seniority_levels=ctx.seniority_levels,
        retrieval=ctx.retriever.backend,
        model=ctx.model if ctx.allow_llm else "",
        prompt_version=PROMPT_VERSION,
        warnings=warnings,
    )
