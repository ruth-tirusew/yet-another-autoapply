#!/usr/bin/env python3
"""Compare vector prefilter scores for generic vs niche-signaling catalog jobs."""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config_store import get_collection
from src.coaching.generalize_profile import load_niche_terms
from src.db import connect, get_user_by_id, init_db
from src.matching.prefilter import PrefilterBatch, posting_chunk_requirements
from src.niche_terms import posting_mentions_user_niche, text_mentions_niche_terms
from src.tenant import resolve_user_id, set_tenant_user_id


def _score_stats(scores: list[float]) -> dict[str, float | int]:
    if not scores:
        return {"count": 0}
    return {
        "count": len(scores),
        "mean": round(statistics.mean(scores), 4),
        "median": round(statistics.median(scores), 4),
        "min": round(min(scores), 4),
        "max": round(max(scores), 4),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose niche-domain prefilter bias")
    parser.add_argument("--user-id", type=int, default=None, help="User ID (default: tenant user)")
    parser.add_argument("--sample", type=int, default=30, help="Jobs per bucket")
    parser.add_argument("--keywords-audit", action="store_true", help="Print keyword config audit")
    args = parser.parse_args()

    init_db()

    uid = resolve_user_id(args.user_id)
    if get_user_by_id(1):
        set_tenant_user_id(uid)

    terms = load_niche_terms(uid)
    print(f"User {uid}: {len(terms)} niche terms loaded")
    if terms:
        print("  Sample terms:", ", ".join(terms[:8]))

    batch = PrefilterBatch.build(uid)
    general_retriever = batch.retriever_for("general")
    niche_retriever = batch.retriever_for("niche")
    if general_retriever is None:
        print("No profile loaded — run: python cli.py upload-cv <pdf>")
        sys.exit(1)

    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, title, company, description_full, description_short
            FROM catalog_jobs
            WHERE status = 'active'
              AND (
                (description_full IS NOT NULL AND description_full != '')
                OR (description_short IS NOT NULL AND description_short != '')
              )
            ORDER BY id DESC
            LIMIT 500
            """
        ).fetchall()

    niche_jobs: list[tuple[str, float, float]] = []
    generic_jobs: list[tuple[str, float, float]] = []

    for row in rows:
        job = dict(row)
        title = job.get("title") or ""
        desc = job.get("description_full") or job.get("description_short") or ""
        job_key, chunk_reqs = posting_chunk_requirements(job)
        if not chunk_reqs:
            continue

        g_retrieved = general_retriever.retrieve(chunk_reqs, job_key=job_key)
        g_score = general_retriever.coverage_hint(g_retrieved)
        if niche_retriever is not None:
            n_retrieved = niche_retriever.retrieve(chunk_reqs, job_key=job_key)
            n_score = niche_retriever.coverage_hint(n_retrieved)
        else:
            n_score = g_score

        label = f"{title[:40]} @ {job.get('company') or '?'}"
        mentions = posting_mentions_user_niche(title, desc, uid, niche_terms=terms)
        body_mentions = text_mentions_niche_terms(desc[:2500], terms)
        if mentions or body_mentions:
            niche_jobs.append((label, g_score, n_score))
        else:
            generic_jobs.append((label, g_score, n_score))

    niche_sample = niche_jobs[: args.sample]
    generic_sample = generic_jobs[: args.sample]

    print("\n=== Generic jobs (no profile niche terms in posting) ===")
    g_general = [s[1] for s in generic_sample]
    g_niche = [s[2] for s in generic_sample]
    print("  general retriever:", _score_stats(g_general))
    print("  niche retriever:  ", _score_stats(g_niche))

    print("\n=== Niche-signaling jobs (posting mentions profile niche terms) ===")
    n_general = [s[1] for s in niche_sample]
    n_niche = [s[2] for s in niche_sample]
    print("  general retriever:", _score_stats(n_general))
    print("  niche retriever:  ", _score_stats(n_niche))

    if g_general and g_niche:
        delta = statistics.mean(g_general) - statistics.mean(g_niche)
        print(f"\nGeneric bucket mean delta (general − niche): {delta:+.4f}")
        if delta < -0.02:
            print("  → Niche retriever scores generic jobs lower (expected bias before fix)")

    if args.keywords_audit:
        print("\n=== Keywords config audit ===")
        for scope_label, scope, scope_id in (("platform", "platform", 0), ("user", "user", uid)):
            kw = get_collection("keywords", scope=scope, scope_id=scope_id)
            include = (kw or {}).get("include") or {}
            offenders: list[str] = []
            for group, values in include.items():
                for val in values or []:
                    lower = str(val).lower()
                    if any(t in lower or lower in t for t in terms):
                        offenders.append(f"{group}: {val}")
            print(f"  {scope_label}: {len(offenders)} potential niche overlap entries")
            for item in offenders[:15]:
                print(f"    • {item}")


if __name__ == "__main__":
    main()
