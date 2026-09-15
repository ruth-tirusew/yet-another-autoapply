#!/usr/bin/env python3
"""Delete all jobs and re-run enrich + match for users 1 and 2."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.db import connect, init_db
from src.embeddings import embed_catalog_jobs, save_profile_embedding
from src.enrich import enrich_jobs
from src.matcher import match_new_jobs
from src.pipeline import run_platform_stages, run_user_stages
from src.tenant import set_tenant_user_id

TARGET_MATCHED = 150
USER_IDS = [1, 2]


def delete_all_jobs() -> None:
    with connect() as conn:
        conn.execute("DELETE FROM application_events")
        conn.execute("DELETE FROM applications")
        conn.execute("DELETE FROM user_jobs")
        conn.execute("DELETE FROM catalog_jobs")
    print("Deleted all jobs (application_events, applications, user_jobs, catalog_jobs).")


def count_matched(user_id: int) -> int:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS c FROM user_jobs
            WHERE user_id = ? AND status IN ('scored', 'queued', 'approved')
            """,
            (user_id,),
        ).fetchone()
    return int(row["c"]) if row else 0


def status_breakdown(user_id: int) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS c FROM user_jobs WHERE user_id = ? GROUP BY status",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def main() -> None:
    init_db()
    delete_all_jobs()

    set_tenant_user_id(1)
    print("\n── Platform: crawl ──")
    run_platform_stages(crawl=True, enrich=False, embed=False)

    print("\n── Platform: enrich (300) ──")
    enrich_jobs(limit=300)

    print("\n── Platform: embed catalog (300) ──")
    n = embed_catalog_jobs(limit=300)
    print(f"  Embedded {n} catalog jobs")

    for uid in USER_IDS:
        set_tenant_user_id(uid)
        print(f"\n{'=' * 50}\nUser {uid}\n{'=' * 50}")
        print("── Profile embedding ──")
        save_profile_embedding(uid)
        run_user_stages(
            sync=True,
            prefilter=True,
            match=False,
            generate=False,
            export=False,
            user_id=uid,
        )

        total_processed = 0
        batch = 0
        while count_matched(uid) < TARGET_MATCHED:
            batch += 1
            result = match_new_jobs(limit=50, user_id=uid)
            total_processed += result["processed"]
            matched = count_matched(uid)
            print(
                f"  Match batch {batch}: +{result['processed']} processed "
                f"({result['scored']} scored, {result['queued']} queued) — "
                f"matched total: {matched}/{TARGET_MATCHED}"
            )
            if result["processed"] == 0:
                print(f"  No more matchable jobs (stopped at {matched}).")
                break

        print(f"User {uid} final breakdown: {status_breakdown(uid)}")

    print("\nDone.")


if __name__ == "__main__":
    main()
