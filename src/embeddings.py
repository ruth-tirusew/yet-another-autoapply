"""Backward-compatible embedding entry points, backed by chunked retrieval.

The old design embedded one vector for an entire resume and one for an
entire job posting, stored as untagged BLOB columns on ``profile`` and
``catalog_jobs``. That has been replaced by chunked, model-tagged vectors in
``vector_store`` (see :mod:`src.matching.vectors` and :mod:`src.matching.
prefilter`). This module keeps the small set of names other code still
imports by name — ``save_profile_embeddings``, ``reset_embeddings``,
``cosine_similarity``, ``embed_catalog_jobs`` — working against the new
storage, so ``cli.py`` and the pipeline stages need no changes.
"""

from __future__ import annotations

from src.catalog_db import update_catalog_job
from src.db import connect
from src.matching.chunking import text_hash
from src.matching.prefilter import PrefilterBatch, posting_chunk_requirements
from src.matching.requirements import source_hash
from src.matching.vectors import OWNER_REQUIREMENT, EmbeddingUnavailable, embed_with_cache, purge
from src.matching.vectors import cosine as cosine_similarity  # noqa: F401  (re-exported for callers)
from src.tenant import resolve_user_id

__all__ = [
    "cosine_similarity",
    "save_profile_embeddings",
    "save_profile_embedding",
    "reset_embeddings",
    "embed_catalog_jobs",
]


def save_profile_embeddings(user_id: int | None = None) -> bool:
    """Pre-warm resume-chunk vectors for both profile variants (general + niche).

    Returns True only when both variants actually produced real vectors
    (``Retriever.backend == "vector"``) — a variant that falls back to
    lexical retrieval means the embedding backend was unreachable, which is
    what callers use this return value to report (e.g. the
    ``profile generalize`` CLI command).
    """
    uid = resolve_user_id(user_id)
    batch = PrefilterBatch.build(uid)
    general = batch.retriever_for("general")
    niche = batch.retriever_for("niche")
    return bool(general and general.backend == "vector") and bool(niche and niche.backend == "vector")


def save_profile_embedding(user_id: int | None = None) -> bool:
    """Alias kept for callers using the old singular name."""
    return save_profile_embeddings(user_id)


def embed_catalog_jobs(limit: int = 200, *, scan_limit: int | None = None) -> int:
    """Pre-warm posting-chunk vectors for active catalog jobs.

    Re-embeds a job whenever its requirements-section text has changed since
    it was last embedded (tracked via ``catalog_jobs.embed_content_hash``),
    not just when it has never been embedded — the old ``embedding IS NULL``
    check meant a job first embedded from a 350-char crawl snippet was never
    re-embedded once the ``enrich`` stage filled in its full description.
    Scans the ``scan_limit`` most recently seen active jobs (default
    ``limit * 5``) to find up to ``limit`` that actually need work, so an
    unchanged catalog costs only cheap hash comparisons, not embedding calls.
    """
    scan_limit = scan_limit or max(limit * 5, limit)
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, title, company, description_short, description_full, embed_content_hash
            FROM catalog_jobs
            WHERE status = 'active'
            ORDER BY last_seen DESC
            LIMIT ?
            """,
            (scan_limit,),
        ).fetchall()

    embedded = 0
    for row in rows:
        if embedded >= limit:
            break
        job = dict(row)
        current_hash = source_hash(job)
        if job.get("embed_content_hash") == current_hash:
            continue  # unchanged since last embed — no network call needed

        job_key, chunk_reqs = posting_chunk_requirements(job)
        if not chunk_reqs:
            update_catalog_job(job["id"], embed_content_hash=current_hash)
            continue

        items = [(f"{job_key}:{r.id}", text_hash(r.text), r.text) for r in chunk_reqs]
        try:
            embed_with_cache(OWNER_REQUIREMENT, items)
        except EmbeddingUnavailable as e:
            print(f"  [embed] job {job['id']}: {e}")
            continue
        update_catalog_job(job["id"], embed_content_hash=current_hash)
        embedded += 1

    print(f"  Embedded {embedded} catalog jobs (chunked)")
    return embedded


def reset_embeddings() -> dict[str, int]:
    """Clear stored embeddings and vector scores (required after switching embedding models).

    Clears the new model-tagged vector cache (resume chunks, posting chunks,
    requirement vectors) as well as the legacy BLOB columns and cached
    per-job content hash, so every job is treated as needing fresh work.
    """
    with connect() as conn:
        profile = conn.execute(
            """
            UPDATE profile
            SET embedding = NULL, embedding_general = NULL, embedding_niche = NULL
            WHERE embedding IS NOT NULL
               OR embedding_general IS NOT NULL
               OR embedding_niche IS NOT NULL
            """
        ).rowcount
        catalog = conn.execute(
            """
            UPDATE catalog_jobs
            SET embedding = NULL, embed_content_hash = NULL
            WHERE embedding IS NOT NULL OR embed_content_hash IS NOT NULL
            """
        ).rowcount
        scores = conn.execute(
            "UPDATE user_jobs SET vector_score = NULL WHERE vector_score IS NOT NULL"
        ).rowcount
    vectors_purged = purge()
    return {
        "profiles": profile,
        "catalog_jobs": catalog,
        "vector_scores": scores,
        "vectors_purged": vectors_purged,
    }
