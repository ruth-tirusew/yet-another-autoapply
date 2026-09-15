"""Chunked vector prefilter — a cheap, LLM-free gate before full ATS matching.

Replaces the old whole-document cosine similarity (one vector for the whole
resume, one for the whole posting), which averaged away everything that
distinguished one job from another and made the `vector_min_score` /
`vector_llm_min_score` thresholds effectively arbitrary. Both sides are now
split into short spans — :func:`~src.matching.chunking.chunk_resume` for the
resume, :func:`~src.matching.chunking.chunk_posting` for the posting — and
the prefilter score is the mean best-chunk similarity across the posting's
spans (how well each part of the posting is covered by *some* part of the
resume), computed with the same model-tagged vector cache the grounded
matcher uses (:mod:`src.matching.vectors`), so a prefilter vector and a
grounded-match vector are never silently compared across embedding models.

This module must not depend on requirement extraction (:mod:`src.matching.
requirements`): the prefilter exists specifically to gate the LLM stages,
extraction included, so it reuses :class:`~src.matching.retrieval.Retriever`
against posting *chunks* (plain text spans) rather than posting
*requirements* (the LLM-extracted, classified objects used later in
matching). Posting-chunk vectors are cached under a catalog-scoped id (never
including the user id), so the embedding cost is paid once per posting and
shared by every user whose queue contains it — the same amortization the
requirement cache gives the grounded matcher.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from src.coaching.generalize_profile import load_general_resume
from src.db import connect, get_jobs_by_status, log_application_event, update_job
from src.matching.chunking import chunk_posting, chunk_resume
from src.matching.models import Requirement, ResumeChunk
from src.matching.retrieval import Retriever
from src.niche_terms import posting_mentions_user_niche
from src.profile import load_resume
from src.settings import get_config
from src.tenant import resolve_user_id

ProfileVariant = Literal["general", "niche"]


def _profile_variant_for_job(job_title: str, job_description: str, user_id: int) -> ProfileVariant:
    if posting_mentions_user_niche(job_title, job_description, user_id):
        return "niche"
    return "general"


def _load_resume_variant(user_id: int, variant: ProfileVariant) -> dict | None:
    if variant == "niche":
        return load_resume(user_id)
    general = load_general_resume(user_id)
    return general or load_resume(user_id)


def posting_chunk_requirements(job: dict[str, Any]) -> tuple[str, list[Requirement]]:
    """Shared job_key + pseudo-requirement list for posting-chunk retrieval.

    Both the pre-warming embed stage (:func:`src.embeddings.embed_catalog_jobs`)
    and the on-demand prefilter must derive identical ``(job_key, chunk id)``
    pairs, or a pre-warmed vector is never the one the prefilter looks up.
    This is the one place that defines that id scheme — every other caller
    goes through it rather than reconstructing it. The ``pf:`` prefix keeps
    this cache namespace disjoint from the grounded matcher's requirement
    vectors, which key off the bare catalog job id.
    """
    catalog_job_id = job.get("catalog_job_id") or job.get("id")
    job_key = f"pf:{catalog_job_id}"
    description = job.get("description_full") or job.get("description_short") or ""
    chunks = chunk_posting(description)
    reqs = [Requirement(id=f"pc{i}", text=text) for i, text in enumerate(chunks)]
    return job_key, reqs


@dataclass
class PrefilterBatch:
    """Per-run state: resume chunks + retrievers for both variants, built once.

    A batch is reused across every job in a ``prefilter_all`` run so the
    resume is only chunked once and its chunk vectors are only fetched from
    the cache (or embedded) once per variant, not once per job.
    """

    user_id: int
    warnings: list[str] = field(default_factory=list)
    _retrievers: dict[ProfileVariant, Retriever | None] = field(default_factory=dict)

    @classmethod
    def build(cls, user_id: int) -> "PrefilterBatch":
        return cls(user_id=user_id)

    def retriever_for(self, variant: ProfileVariant) -> Retriever | None:
        if variant not in self._retrievers:
            resume = _load_resume_variant(self.user_id, variant)
            chunks: list[ResumeChunk] = chunk_resume(resume) if resume else []
            retriever = (
                Retriever(chunks, user_id=self.user_id, owner_prefix=f"u{self.user_id}:{variant}:")
                if chunks
                else None
            )
            if retriever and retriever.warning and retriever.warning not in self.warnings:
                self.warnings.append(retriever.warning)
            self._retrievers[variant] = retriever
        return self._retrievers[variant]


def _matching_cfg() -> dict:
    return get_config().get("matching", {})


def prefilter_job(
    user_job_id: int,
    user_id: int | None = None,
    *,
    context: PrefilterBatch | None = None,
) -> float | None:
    uid = resolve_user_id(user_id)
    cfg = _matching_cfg()
    if not cfg.get("vector_prefilter_enabled", True):
        return None

    with connect() as conn:
        row = conn.execute(
            """
            SELECT uj.id, uj.catalog_job_id, cj.title, cj.description_full, cj.description_short
            FROM user_jobs uj
            JOIN catalog_jobs cj ON cj.id = uj.catalog_job_id
            WHERE uj.id = ? AND uj.user_id = ?
            """,
            (user_job_id, uid),
        ).fetchone()
    if not row:
        return None

    job = dict(row)
    job_title = job.get("title") or ""
    job_description = job.get("description_full") or job.get("description_short") or ""
    if not job_description.strip():
        return None

    variant = _profile_variant_for_job(job_title, job_description, uid)

    batch = context or PrefilterBatch.build(uid)
    retriever = batch.retriever_for(variant)
    if retriever is None:
        return None  # no resume loaded — nothing to compare the posting against

    job_key, chunk_reqs = posting_chunk_requirements(job)
    if not chunk_reqs:
        return None  # posting has no extractable content to prefilter against

    retrieved = retriever.retrieve(chunk_reqs, job_key=job_key)
    score = retriever.coverage_hint(retrieved)

    vector_min = float(cfg.get("vector_min_score", 0.35))
    vector_llm_min = float(cfg.get("vector_llm_min_score", 0.50))
    event_meta = {"vector_score": score, "vector_variant": variant, "retrieval": retriever.backend}

    if score < vector_min:
        update_job(
            user_job_id,
            user_id=uid,
            vector_score=score,
            status="skipped",
            match_summary=f"SKIP — vector score {score:.2f} below {vector_min}",
        )
        log_application_event(
            user_job_id,
            "prefilter_skipped",
            {**event_meta, "reason": "below_vector_min"},
            user_id=uid,
        )
    else:
        update_job(user_job_id, user_id=uid, vector_score=score)
        if score < vector_llm_min:
            log_application_event(
                user_job_id,
                "prefilter_borderline",
                {**event_meta, "llm_threshold": vector_llm_min},
                user_id=uid,
            )

    return score


def prefilter_all(limit: int = 500, user_id: int | None = None) -> dict[str, int]:
    uid = resolve_user_id(user_id)
    cfg = _matching_cfg()
    if not cfg.get("vector_prefilter_enabled", True):
        return {"scored": 0, "skipped": 0}

    jobs = get_jobs_by_status("new", limit=limit, user_id=uid)
    batch = PrefilterBatch.build(uid)
    scored = 0
    skipped = 0
    for job in jobs:
        if job.get("vector_score") is not None:
            continue
        result = prefilter_job(job["id"], user_id=uid, context=batch)
        if result is None:
            continue
        scored += 1
        if result < float(cfg.get("vector_min_score", 0.35)):
            skipped += 1

    for warning in batch.warnings:
        print(f"  [prefilter] {warning}")
    print(f"  Prefiltered {scored} jobs ({skipped} skipped below threshold)")
    return {"scored": scored, "skipped": skipped}
