#!/usr/bin/env python3
"""Repair catalog data left corrupted by the pre-fix status-leak bug.

Before the fix in src/catalog_db.py (CATALOG_FIELDS no longer includes
"status"), update_job(status=...) — called by every match/prefilter/
eligibility decision — also overwrote the *shared* catalog_jobs.status
column, since "status" used to be listed in both CATALOG_FIELDS and
USER_JOB_FIELDS. catalog_jobs.status should only ever be "active" or
"stale" (the crawl's own lifecycle); any other value sitting there today
is leftover corruption from that bug, and it silently excludes the row
from sync_catalog_to_user, embed_catalog_jobs and
get_catalog_jobs_needing_enrichment — all of which filter on
status = 'active' — for every user, not just whichever one caused the
corruption.

This script, in order:

  1. Recomputes catalog_jobs.url_hash for every row from its stored url,
     using the *current* url_hash()/normalize_url() logic. Phase 2 changed
     what gets normalized before hashing (dropped a trailing slash,
     lowercased scheme/host, stripped a narrow set of tracking params) —
     a row hashed before that change won't match what a fresh crawl
     computes for the exact same URL, so upsert_catalog_job would create a
     brand new duplicate row (and a brand new LLM match) for a posting
     already in the catalog, the next time it's crawled. Two raw URLs that
     now normalize to the same target are merged: the row with a
     description survives (falling back to earliest first_seen), its
     sibling's user_jobs rows are reassigned to it (or dropped, per user,
     if that user already has a row for the surviving catalog job — never
     both, UNIQUE(user_id, catalog_job_id) forbids it), and the sibling
     catalog row is deleted.
  2. Backfills catalog_jobs.fingerprint for every row that predates it
     (fingerprint is only set by upsert_catalog_job going forward — see
     Phase 2), so possible_duplicates and step 5 below have real data to
     work with immediately instead of waiting for the next crawl to
     re-touch every row.
  3. Resets every catalog_jobs row whose status isn't 'active', 'stale' or
     'expired' back to 'active' or 'stale' (never 'expired' — that's only
     ever set by a confirmed native re-crawl finding a job gone, which
     this repair has no way to confirm), based on how recently it was
     last_seen (the same rule mark_catalog_stale already uses going
     forward).
  4. Tags any user_jobs row that already has a resolved status but no
     classified_by (i.e. it predates that column) as "legacy_unknown", so
     it reads honestly as "decided before this was tracked" instead of
     looking unset forever.
  5. Syncs each user's queue, so catalog rows the repair just reactivated
     (previously invisible to sync) reach their per-user queue as 'new'.
  6. For any resulting 'new' job whose (title, company, location)
     fingerprint matches a posting that user has already REJECTED or
     SKIPPED under a different URL, inherits that decision instead of
     leaving it 'new' — sparing a repeat LLM match on something already
     decided. Deliberately does NOT do this for 'applied' siblings: silently
     marking a distinct, still-open requisition "applied" risks hiding a
     real opportunity the person never actually applied to, which is a
     worse outcome than an occasional redundant match. Those are left
     'new' and simply carry the existing "+N similar" duplicate badge.

Dry-run by default — prints exactly what steps 1/2/3/4/6 would change and
does not write anything. Pass --apply to commit. Step 5 (sync) only runs
under --apply, since it's not meaningfully "undoable" to preview (its
effect is just: some catalog rows would gain a 'new' row in the queue).

Safe to re-run: every step is a no-op on data it's already fixed.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.db import connect, init_db, list_user_ids
from src.settings import get_config
from src.tenant import set_tenant_user_id

VALID_CATALOG_STATUSES = {"active", "stale", "expired"}
INHERITABLE_STATUSES = {"rejected", "skipped"}  # never "applied" — see module docstring


def _stale_cutoff(days: int) -> str:
    return (datetime.utcnow() - timedelta(days=days)).isoformat()


def _reassign_or_drop_user_jobs(conn, *, loser_catalog_id: int, winner_catalog_id: int) -> None:
    """Point a merged-away catalog row's user_jobs rows at the surviving
    row — or drop them if that user already has one for the survivor,
    since UNIQUE(user_id, catalog_job_id) forbids two. applications and
    application_events key off user_jobs.id, not catalog_job_id, so a
    reassignment (the common case) needs no further changes to them; a
    drop takes them down with the user_jobs row rather than leaving them
    orphaned.
    """
    loser_rows = conn.execute(
        "SELECT id, user_id FROM user_jobs WHERE catalog_job_id = ?", (loser_catalog_id,)
    ).fetchall()
    for row in loser_rows:
        already_has_winner = conn.execute(
            "SELECT 1 FROM user_jobs WHERE user_id = ? AND catalog_job_id = ?",
            (row["user_id"], winner_catalog_id),
        ).fetchone()
        if already_has_winner:
            conn.execute("DELETE FROM application_events WHERE job_id = ?", (row["id"],))
            conn.execute("DELETE FROM applications WHERE job_id = ?", (row["id"],))
            conn.execute("DELETE FROM user_jobs WHERE id = ?", (row["id"],))
        else:
            conn.execute(
                "UPDATE user_jobs SET catalog_job_id = ? WHERE id = ?",
                (winner_catalog_id, row["id"]),
            )


def rehash_catalog_urls(*, apply: bool) -> dict[str, int]:
    """See module docstring step 1."""
    from src.db import url_hash as compute_url_hash

    renamed = 0
    merged = 0
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, url, url_hash, first_seen, description_full FROM catalog_jobs"
        ).fetchall()
        by_new_hash: dict[str, list] = {}
        for r in rows:
            by_new_hash.setdefault(compute_url_hash(r["url"]), []).append(r)

        for new_hash, group in by_new_hash.items():
            stale = [r for r in group if r["url_hash"] != new_hash]
            if not stale:
                continue

            if len(group) == 1:
                r = group[0]
                print(f"[rehash] catalog #{r['id']}: {r['url_hash'][:12]}… -> {new_hash[:12]}…")
                if apply:
                    conn.execute("UPDATE catalog_jobs SET url_hash = ? WHERE id = ?", (new_hash, r["id"]))
                renamed += 1
                continue

            # >1 raw URL now normalizes to the same hash — a real merge,
            # not just a rename. Prefer the row with a description (richer
            # data), tie-break on earliest first_seen.
            group_sorted = sorted(
                group,
                key=lambda r: (0 if r["description_full"] else 1, r["first_seen"] or ""),
            )
            winner, *losers = group_sorted
            print(
                f"[rehash] merging catalog {[l['id'] for l in losers]} into #{winner['id']} "
                f"(new hash {new_hash[:12]}…)"
            )
            if apply:
                conn.execute("UPDATE catalog_jobs SET url_hash = ? WHERE id = ?", (new_hash, winner["id"]))
                for loser in losers:
                    _reassign_or_drop_user_jobs(conn, loser_catalog_id=loser["id"], winner_catalog_id=winner["id"])
                    conn.execute("DELETE FROM catalog_jobs WHERE id = ?", (loser["id"],))
            renamed += 1
            merged += len(losers)

    print(f"[rehash] {renamed} row(s) need a new url_hash, {merged} of those are merges")
    return {"renamed": renamed, "merged": merged}


def backfill_fingerprints(*, apply: bool) -> int:
    """Compute fingerprint for every catalog row that predates it.

    fingerprint is only ever set by upsert_catalog_job going forward
    (Phase 2), so a row nobody has re-crawled since that landed still has
    fingerprint = NULL and won't be flagged as a possible duplicate or
    picked up by inherit_duplicate_decisions until this runs.
    """
    from src.catalog_db import compute_fingerprint

    with connect() as conn:
        rows = conn.execute(
            "SELECT id, title, company, location FROM catalog_jobs "
            "WHERE fingerprint IS NULL OR fingerprint = ''"
        ).fetchall()
        print(f"[fingerprint backfill] {len(rows)} catalog rows missing a fingerprint")
        if apply:
            updates = [
                (compute_fingerprint(r["title"], r["company"], r["location"]), r["id"])
                for r in rows
            ]
            conn.executemany("UPDATE catalog_jobs SET fingerprint = ? WHERE id = ?", updates)
            print(f"[fingerprint backfill] applied to {len(updates)} rows")
    return len(rows)


def repair_catalog_status(*, apply: bool, stale_days: int) -> dict[str, int]:
    cutoff = _stale_cutoff(stale_days)
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, status, last_seen FROM catalog_jobs "
            f"WHERE status NOT IN ({','.join('?' * len(VALID_CATALOG_STATUSES))})",
            tuple(VALID_CATALOG_STATUSES),
        ).fetchall()

        by_transition: Counter[tuple[str, str]] = Counter()
        active_ids: list[int] = []
        stale_ids: list[int] = []
        for row in rows:
            target = "active" if (row["last_seen"] or "") >= cutoff else "stale"
            by_transition[(row["status"], target)] += 1
            (active_ids if target == "active" else stale_ids).append(row["id"])

        print(f"[catalog status] {len(rows)} corrupted rows found (status not in {VALID_CATALOG_STATUSES}):")
        for (old, new), count in sorted(by_transition.items()):
            print(f"    {old!r:>12} -> {new!r:<8}  x{count}")

        if apply:
            for status, ids in (("active", active_ids), ("stale", stale_ids)):
                for i in range(0, len(ids), 400):
                    chunk = ids[i : i + 400]
                    placeholders = ",".join("?" * len(chunk))
                    conn.execute(
                        f"UPDATE catalog_jobs SET status = ? WHERE id IN ({placeholders})",
                        (status, *chunk),
                    )
            print(f"[catalog status] applied: {len(active_ids)} -> active, {len(stale_ids)} -> stale")

    return {"active": len(active_ids), "stale": len(stale_ids)}


def tag_legacy_classifications(*, apply: bool) -> int:
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) c FROM user_jobs "
            "WHERE classified_by IS NULL AND status NOT IN ('new')"
        ).fetchone()
        count = row["c"]
        print(f"[classified_by] {count} resolved user_jobs rows predate classification tracking")
        if apply and count:
            conn.execute(
                "UPDATE user_jobs SET classified_by = 'legacy_unknown' "
                "WHERE classified_by IS NULL AND status NOT IN ('new')"
            )
            print(f"[classified_by] tagged {count} rows as 'legacy_unknown'")
    return count


def _resolved_fingerprints_for_user(conn, uid: int) -> dict[str, dict]:
    """fingerprint -> {status, catalog_job_id} for this user's rejected/skipped jobs.

    One row per fingerprint (lowest user_jobs.id wins) — any consistent
    choice is fine here, this is an informational inheritance, not an
    authoritative merge.
    """
    rows = conn.execute(
        """
        SELECT uj.id, uj.status, uj.catalog_job_id, cj.fingerprint
        FROM user_jobs uj
        JOIN catalog_jobs cj ON cj.id = uj.catalog_job_id
        WHERE uj.user_id = ?
          AND uj.status IN ({placeholders})
          AND cj.fingerprint IS NOT NULL AND cj.fingerprint != ''
        ORDER BY uj.id ASC
        """.format(placeholders=",".join("?" * len(INHERITABLE_STATUSES))),
        (uid, *INHERITABLE_STATUSES),
    ).fetchall()
    out: dict[str, dict] = {}
    for row in rows:
        out.setdefault(row["fingerprint"], {"status": row["status"], "catalog_job_id": row["catalog_job_id"]})
    return out


def inherit_duplicate_decisions(*, apply: bool) -> int:
    total = 0
    with connect() as conn:
        for uid in list_user_ids():
            resolved = _resolved_fingerprints_for_user(conn, uid)
            if not resolved:
                continue
            fps = list(resolved.keys())
            placeholders = ",".join("?" * len(fps))
            candidates = conn.execute(
                f"""
                SELECT uj.id, cj.fingerprint, cj.title, cj.company
                FROM user_jobs uj
                JOIN catalog_jobs cj ON cj.id = uj.catalog_job_id
                WHERE uj.user_id = ? AND uj.status = 'new' AND uj.classified_by IS NULL
                  AND cj.fingerprint IN ({placeholders})
                """,
                (uid, *fps),
            ).fetchall()
            for c in candidates:
                target = resolved[c["fingerprint"]]
                total += 1
                print(
                    f"[duplicate inherit] user {uid}: job #{c['id']} ({c['title']!r} @ {c['company']!r}) "
                    f"-> {target['status']} (duplicate of catalog #{target['catalog_job_id']})"
                )
                if apply:
                    conn.execute(
                        "UPDATE user_jobs SET status = ?, classified_by = 'duplicate_of_resolved', "
                        "match_summary = ? WHERE id = ?",
                        (
                            target["status"],
                            f"AUTO-{target['status'].upper()} — duplicate of a posting "
                            f"(catalog #{target['catalog_job_id']}) already {target['status']}",
                            c["id"],
                        ),
                    )
    if apply:
        print(f"[duplicate inherit] applied to {total} jobs")
    return total


def sync_all_users() -> int:
    from src.catalog_db import sync_catalog_to_user

    total = 0
    for uid in list_user_ids():
        set_tenant_user_id(uid)
        total += sync_catalog_to_user(user_id=uid, limit=10000)
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write changes (default: dry run)")
    parser.add_argument("--stale-days", type=int, default=None, help="Override pipeline.stale_days")
    args = parser.parse_args()

    init_db()
    cfg = get_config()
    stale_days = args.stale_days or int(cfg.get("stale_job_days", 30))

    print(f"{'APPLY' if args.apply else 'DRY RUN'} — stale cutoff: {stale_days} days\n")

    rehash_catalog_urls(apply=args.apply)
    print()
    backfill_fingerprints(apply=args.apply)
    print()
    repair_catalog_status(apply=args.apply, stale_days=stale_days)
    print()
    tag_legacy_classifications(apply=args.apply)
    print()

    if args.apply:
        synced = sync_all_users()
        print(f"[sync] {synced} catalog jobs synced across all users\n")
    else:
        print("[sync] skipped in dry run (run with --apply to sync reactivated jobs)\n")

    inherit_duplicate_decisions(apply=args.apply)

    if not args.apply:
        print("\nDry run only — no changes written. Re-run with --apply to commit.")


if __name__ == "__main__":
    main()
