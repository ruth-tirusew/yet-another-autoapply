"""Re-check location eligibility for jobs already in the queue."""

from __future__ import annotations

import json

from src.crawler.filters import is_job_eligible
from src.db import connect, get_jobs_by_statuses, update_job
from src.profile import load_resume
from src.tenant import resolve_user_id

_RECHECK_STATUSES = ("new", "scored", "queued")


def recheck_eligibility(
    user_id: int | None = None,
    *,
    limit: int = 2000,
) -> dict[str, int]:
    uid = resolve_user_id(user_id)
    resume = load_resume(user_id=uid)
    stats = {"checked": 0, "skipped": 0, "unchanged": 0}

    jobs = get_jobs_by_statuses(list(_RECHECK_STATUSES), limit=limit, user_id=uid)
    for job in jobs:
        stats["checked"] += 1
        eligible, reason = is_job_eligible(job, resume)
        if eligible:
            stats["unchanged"] += 1
            continue

        stats["skipped"] += 1
        update_job(
            job["id"],
            user_id=uid,
            match_score=0,
            match_summary=f"SKIP — {reason}",
            match_details=json.dumps({"recommendation": "skip", "reason": reason}),
            status="skipped",
        )

    return stats


def recheck_eligibility_all_users(*, limit_per_user: int = 2000) -> dict[str, int]:
    with connect() as conn:
        rows = conn.execute("SELECT id FROM users ORDER BY id").fetchall()
    totals = {"users": 0, "checked": 0, "skipped": 0, "unchanged": 0}
    for row in rows:
        totals["users"] += 1
        batch = recheck_eligibility(row["id"], limit=limit_per_user)
        for key in ("checked", "skipped", "unchanged"):
            totals[key] += batch[key]
    return totals
