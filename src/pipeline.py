"""Orchestrate platform crawl → enrich → embed → per-user sync → prefilter → match → generate."""

from __future__ import annotations

from src.config import get_config
from src.catalog_sync import sync_user_jobs
from src.cover_letter import generate_cover_letter
from src.crawler.export import export_excel
from src.crawler.orchestrator import crawl_all
from src.db import get_jobs_by_status, init_db
from src.enrich import enrich_jobs
from src.matcher import match_all
from src.tailor import TailorSkipped, tailor_cv


def run_platform_stages(
    crawl: bool = True,
    enrich: bool = True,
    embed: bool = True,
) -> None:
    cfg = get_config()

    if crawl:
        print("\n── Crawl (platform catalog) ──")
        crawl_all(persist=True)

    if enrich:
        print("\n── Enrich descriptions (catalog) ──")
        enrich_limit = cfg.get("pipeline", {}).get("enrich_limit", 200)
        enrich_jobs(limit=enrich_limit)

    if embed:
        print("\n── Embed catalog jobs ──")
        try:
            from src.embeddings import embed_catalog_jobs

            embed_limit = cfg.get("pipeline", {}).get("embed_limit", 200)
            embed_catalog_jobs(limit=embed_limit)
        except ImportError:
            print("  [embed] embeddings module not available, skipping")


def run_user_stages(
    sync: bool = True,
    prefilter: bool = True,
    match: bool = True,
    generate: bool = True,
    export: bool = True,
    user_id: int | None = None,
) -> None:
    cfg = get_config()

    if sync:
        print("\n── Sync catalog to user ──")
        sync_limit = cfg.get("pipeline", {}).get("sync_limit", 500)
        sync_user_jobs(user_id=user_id, limit=sync_limit)

    print("\n── Recheck location eligibility ──")
    from src.eligibility_backfill import recheck_eligibility

    recheck = recheck_eligibility(user_id=user_id)
    print(
        f"  Checked {recheck['checked']} jobs "
        f"({recheck['skipped']} skipped, {recheck['unchanged']} unchanged)"
    )

    if prefilter:
        print("\n── Vector prefilter ──")
        try:
            from src.prefilter import prefilter_all

            prefilter_all(user_id=user_id)
        except ImportError:
            print("  [prefilter] not available, skipping")

    if match:
        print("\n── Match jobs to profile ──")
        match_limit = cfg.get("pipeline", {}).get("match_limit", 100)
        result = match_all(limit=match_limit, user_id=user_id)
        print(
            f"  Batch complete: {result.get('scored', 0)} scored, "
            f"{result.get('queued', 0)} queued"
        )

    if generate:
        print("\n── Generate materials for queued jobs ──")
        from src.ats import parse_match_details, should_queue
        from src.cover_letter import CoverLetterSkipped, generate_cover_letter

        gen_limit = cfg.get("pipeline", {}).get("generate_limit", 50)
        pipeline = cfg.get("pipeline", {})
        threshold = cfg["match_threshold"]
        role_fit_min = int(pipeline.get("role_fit_min", 15))
        maybe_boost = int(pipeline.get("maybe_score_boost", 10))

        for job in get_jobs_by_status("queued", limit=gen_limit, user_id=user_id):
            m = parse_match_details(job)
            if m:
                ok, reason = should_queue(
                    m, threshold, role_fit_min=role_fit_min, maybe_score_boost=maybe_boost
                )
                if not ok:
                    print(f"  [generate] skip job {job['id']}: {reason}")
                    continue
            try:
                tailor_cv(job["id"], force=True, user_id=user_id)
                generate_cover_letter(job["id"], force=True, user_id=user_id)
                print(f"  Generated materials for job {job['id']}: {job.get('title', '')[:50]}")
            except (CoverLetterSkipped, TailorSkipped) as e:
                print(f"  [generate] skip job {job['id']}: {e}")
            except Exception as e:
                print(f"  [generate] job {job['id']}: {e}")

    if export:
        print("\n── Export Excel ──")
        export_excel(user_id=user_id)


def run_pipeline(
    crawl: bool = True,
    enrich: bool = True,
    match: bool = True,
    generate: bool = True,
    export: bool = True,
    embed: bool = True,
    sync: bool = True,
    prefilter: bool = True,
    user_id: int | None = None,
) -> None:
    init_db()
    run_platform_stages(crawl=crawl, enrich=enrich, embed=embed)
    run_user_stages(
        sync=sync,
        prefilter=prefilter,
        match=match,
        generate=generate,
        export=export,
        user_id=user_id,
    )
    print("\nPipeline complete.")
