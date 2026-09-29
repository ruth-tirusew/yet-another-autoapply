"""Shared job catalog + per-user overlay (crawl once, match per user)."""

from __future__ import annotations

import hashlib
import json
import re
import struct
from datetime import datetime, timedelta
from typing import Any

from src.db import connect, url_hash
from src.tenant import resolve_user_id

# Stay comfortably under SQLite's default 999-variable-per-statement limit
# when batching an IN (...) clause over an arbitrary number of hashes.
_SQLITE_IN_BATCH = 400

# user_jobs statuses a posting closing (mark_catalog_stale / expire_missing_
# catalog_jobs) is allowed to demote to 'stale'. Deliberately excludes
# 'needs_verification' (an apply attempt is actively in flight — yanking the
# job out from under it would strand that flow) and every terminal status
# ('applied', 'rejected', 'skipped', 'failed', already-'stale') — a real
# outcome must never be overwritten just because the posting later closed.
_STALE_DEMOTABLE_STATUSES = ("new", "scored", "approved", "queued")

CATALOG_FIELDS = frozenset({
    "source", "source_id", "title", "company", "location", "salary", "tags",
    "date_posted", "url", "description_short", "description_full", "ats_type",
    "first_seen", "last_seen", "url_hash", "embedding",
})

USER_JOB_FIELDS = frozenset({
    "status", "match_score", "match_summary", "match_details", "vector_score",
    "notes", "applied_at", "match_attempts", "classified_by",
})

JOB_JOIN_FROM = """
FROM user_jobs uj
JOIN catalog_jobs cj ON cj.id = uj.catalog_job_id
"""

JOB_JOIN_SELECT = """
SELECT
  uj.id AS id,
  uj.user_id AS user_id,
  uj.catalog_job_id AS catalog_job_id,
  cj.url_hash AS url_hash,
  cj.source AS source,
  cj.source_id AS source_id,
  cj.title AS title,
  cj.company AS company,
  cj.location AS location,
  cj.salary AS salary,
  cj.tags AS tags,
  cj.date_posted AS date_posted,
  cj.url AS url,
  cj.description_short AS description_short,
  cj.description_full AS description_full,
  cj.ats_type AS ats_type,
  cj.first_seen AS first_seen,
  cj.last_seen AS last_seen,
  cj.status AS catalog_status,
  CASE WHEN cj.fingerprint IS NULL OR cj.fingerprint = '' THEN 0
       ELSE (SELECT COUNT(*) - 1 FROM catalog_jobs cj2 WHERE cj2.fingerprint = cj.fingerprint)
  END AS possible_duplicates,
  uj.status AS status,
  uj.match_score AS match_score,
  uj.match_summary AS match_summary,
  uj.match_details AS match_details,
  uj.vector_score AS vector_score,
  uj.notes AS notes,
  uj.applied_at AS applied_at,
  uj.match_attempts AS match_attempts,
  uj.classified_by AS classified_by
"""


def _row_to_job(row: Any) -> dict[str, Any]:
    d = dict(row)
    d.pop("catalog_status", None)
    return d


def embedding_to_blob(vec: list[float] | None) -> bytes | None:
    if not vec:
        return None
    return struct.pack(f"{len(vec)}f", *vec)


def blob_to_embedding(blob: bytes | None) -> list[float] | None:
    if not blob:
        return None
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def migrate_catalog_schema(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS catalog_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url_hash TEXT UNIQUE NOT NULL,
            source TEXT,
            source_id TEXT,
            title TEXT,
            company TEXT,
            location TEXT,
            salary TEXT,
            tags TEXT,
            date_posted TEXT,
            url TEXT,
            description_short TEXT,
            description_full TEXT,
            ats_type TEXT,
            embedding BLOB,
            status TEXT DEFAULT 'active',
            first_seen TEXT,
            last_seen TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_catalog_jobs_status ON catalog_jobs(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_catalog_jobs_last_seen ON catalog_jobs(last_seen)")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            catalog_job_id INTEGER NOT NULL REFERENCES catalog_jobs(id),
            status TEXT DEFAULT 'new',
            match_score REAL,
            match_summary TEXT,
            match_details TEXT,
            vector_score REAL,
            notes TEXT,
            applied_at TEXT,
            synced_at TEXT,
            UNIQUE(user_id, catalog_job_id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_user_jobs_user ON user_jobs(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_user_jobs_status ON user_jobs(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_user_jobs_catalog ON user_jobs(catalog_job_id)")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS target_companies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            company_slug TEXT NOT NULL,
            ats_type TEXT NOT NULL,
            tier INTEGER DEFAULT 2,
            display_name TEXT,
            careers_url TEXT,
            enabled INTEGER DEFAULT 1,
            origin TEXT DEFAULT 'manual',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, company_slug, ats_type)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_target_companies_user ON target_companies(user_id)")
    tc_cols = {r[1] for r in conn.execute("PRAGMA table_info(target_companies)").fetchall()}
    if "origin" not in tc_cols:
        # "manual" (a person added it) or "auto" (the hybrid watchlist added
        # it because its jobs kept scoring well) — see
        # catalog_db.sync_target_company_watchlist. Existing rows predate
        # auto-add entirely, so they're all genuinely manual.
        conn.execute("ALTER TABLE target_companies ADD COLUMN origin TEXT DEFAULT 'manual'")

    # A company the hybrid watchlist auto-added and a person then removed
    # via the UI — auto-add must never re-add it. Not used for a person's
    # own manual removals, since nothing auto-adds those back anyway.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS target_company_blocklist (
            user_id INTEGER NOT NULL,
            company_slug TEXT NOT NULL,
            ats_type TEXT NOT NULL,
            blocked_at TEXT NOT NULL,
            PRIMARY KEY (user_id, company_slug, ats_type)
        )
        """
    )

    uj_cols = {r[1] for r in conn.execute("PRAGMA table_info(user_jobs)").fetchall()}
    if "match_attempts" not in uj_cols:
        conn.execute("ALTER TABLE user_jobs ADD COLUMN match_attempts INTEGER DEFAULT 0")
    if "classified_by" not in uj_cols:
        # What actually set the current status, independent of the
        # free-text match_summary a person has to read to find out: "user"
        # (a manual approve/reject/skip/applied action), "llm"/"rules"/
        # "grounded" (JobMatchResult.engine, whichever scored it),
        # "vector_prefilter" (skipped below the vector gate before any LLM
        # call), "eligibility" (location-restricted), "apply_dispatcher"
        # (an automated application attempt's outcome), "match_error"
        # (retired after repeated scoring failures), "duplicate_of_resolved"
        # (inherited a decision already made on a same-fingerprint posting —
        # see scripts/repair_catalog_data.py), "closed" (demoted to 'stale'
        # because a confirmed native re-crawl found the posting gone — see
        # expire_missing_catalog_jobs), "stale_timeout" (demoted to 'stale'
        # by last_seen aging out with no such confirmation — see
        # mark_catalog_stale), or NULL for a row that predates this column
        # (status was set, but by what is unrecorded).
        conn.execute("ALTER TABLE user_jobs ADD COLUMN classified_by TEXT")

    # Stage-1 requirement extraction, cached on the shared catalog row so the
    # cost is paid once per posting rather than once per user.
    cat_cols = {r[1] for r in conn.execute("PRAGMA table_info(catalog_jobs)").fetchall()}
    for column, ddl in (
        ("requirements_json", "requirements_json TEXT"),
        ("requirements_hash", "requirements_hash TEXT"),
        ("requirements_model", "requirements_model TEXT"),
        ("requirements_at", "requirements_at TEXT"),
        ("embed_content_hash", "embed_content_hash TEXT"),
        # Exact-match (title, company, location) identity, independent of
        # url_hash. The same posting routinely gets crawled under several
        # distinct URLs (a different requisition ID per re-post, a
        # tracking-param variant not caught by url normalization), which
        # url_hash's uniqueness can't catch since those URLs are genuinely
        # different. This is deliberately *not* used to merge or suppress
        # rows — same title+company+location can also mean a company has
        # multiple real open reqs for the same role — only to flag likely
        # duplicates for a person to judge (see get_catalog_url_hashes and
        # JOB_JOIN_SELECT's possible_duplicates column).
        ("fingerprint", "fingerprint TEXT"),
        # "native" (fetched directly from the company's own ATS API —
        # Greenhouse/Lever/Ashby/Workable/SmartRecruiters) or "aggregator"
        # (relayed through a third-party board). A native fetch is
        # authoritative for a posting: see upsert_catalog_job for why an
        # aggregator re-crawl of an already-native row only touches
        # last_seen rather than overwriting its (more trustworthy) date,
        # description and location.
        ("source_tier", "source_tier TEXT DEFAULT 'aggregator'"),
        # The exact ATS slug a native fetch used (e.g. the Greenhouse board
        # token). Only ever set by a native adapter, which already knows it
        # precisely — used to scope "this company's listing no longer
        # includes this job" checks to that one company, rather than
        # matching on a free-text display name that can drift.
        ("company_slug", "company_slug TEXT"),
    ):
        if column not in cat_cols:
            conn.execute(f"ALTER TABLE catalog_jobs ADD COLUMN {ddl}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_catalog_jobs_fingerprint ON catalog_jobs(fingerprint)")

    # Model-tagged vector cache for resume chunks and posting requirements.
    # user_id is NULL for owner kinds shared across tenants (e.g. posting
    # requirements, cached once per catalog job) and set for tenant-owned
    # rows (e.g. resume chunks) — see TENANT_SCOPED_OWNER_KINDS in
    # src.matching.vectors.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vector_store (
            owner_kind TEXT NOT NULL,
            owner_id   TEXT NOT NULL,
            model      TEXT NOT NULL,
            dim        INTEGER NOT NULL,
            text_hash  TEXT NOT NULL,
            vec        BLOB NOT NULL,
            user_id    INTEGER,
            created_at TEXT NOT NULL,
            PRIMARY KEY (owner_kind, owner_id, model)
        )
        """
    )
    vs_cols = {r[1] for r in conn.execute("PRAGMA table_info(vector_store)").fetchall()}
    if "user_id" not in vs_cols:
        conn.execute("ALTER TABLE vector_store ADD COLUMN user_id INTEGER")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_vector_store_kind ON vector_store(owner_kind, model)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_vector_store_user ON vector_store(user_id)"
    )

    prof_cols = {r[1] for r in conn.execute("PRAGMA table_info(profile)").fetchall()}
    if "embedding" not in prof_cols:
        conn.execute("ALTER TABLE profile ADD COLUMN embedding BLOB")
    if "embedding_general" not in prof_cols:
        conn.execute("ALTER TABLE profile ADD COLUMN embedding_general BLOB")
    if "embedding_niche" not in prof_cols:
        conn.execute("ALTER TABLE profile ADD COLUMN embedding_niche BLOB")

    row = conn.execute("SELECT COUNT(*) FROM catalog_jobs").fetchone()
    if row and row[0] == 0:
        _migrate_legacy_jobs(conn)


def _migrate_legacy_jobs(conn) -> None:
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs'"
    ).fetchone():
        return
    legacy_count = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    if not legacy_count:
        return

    rows = conn.execute("SELECT * FROM jobs ORDER BY id").fetchall()
    catalog_by_hash: dict[str, int] = {}

    for row in rows:
        r = dict(row)
        uh = r.get("url_hash") or url_hash(r.get("url", ""))
        if not uh:
            continue

        uid = r.get("user_id") if "user_id" in r.keys() else 1

        if uh not in catalog_by_hash:
            cur = conn.execute(
                """
                INSERT INTO catalog_jobs (
                    url_hash, source, source_id, title, company, location, salary, tags,
                    date_posted, url, description_short, description_full, status,
                    first_seen, last_seen
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (
                    uh,
                    r.get("source", ""),
                    r.get("source_id", ""),
                    r.get("title", ""),
                    r.get("company", ""),
                    r.get("location", ""),
                    r.get("salary", ""),
                    r.get("tags", ""),
                    r.get("date_posted", ""),
                    r.get("url", ""),
                    r.get("description_short", ""),
                    r.get("description_full", ""),
                    r.get("first_seen") or datetime.utcnow().isoformat(),
                    r.get("last_seen") or datetime.utcnow().isoformat(),
                ),
            )
            catalog_by_hash[uh] = cur.lastrowid
        else:
            cid = catalog_by_hash[uh]
            if r.get("description_full"):
                conn.execute(
                    """
                    UPDATE catalog_jobs SET description_full=?, description_short=?,
                        title=?, company=?, location=?, last_seen=?
                    WHERE id=?
                    """,
                    (
                        r.get("description_full"),
                        r.get("description_short"),
                        r.get("title"),
                        r.get("company"),
                        r.get("location"),
                        r.get("last_seen"),
                        cid,
                    ),
                )

        cid = catalog_by_hash[uh]
        exists = conn.execute(
            "SELECT id FROM user_jobs WHERE user_id=? AND catalog_job_id=?",
            (uid, cid),
        ).fetchone()
        if exists:
            conn.execute(
                """
                UPDATE user_jobs SET status=?, match_score=?, match_summary=?,
                    match_details=?, notes=?, applied_at=?
                WHERE id=?
                """,
                (
                    r.get("status", "new"),
                    r.get("match_score"),
                    r.get("match_summary"),
                    r.get("match_details"),
                    r.get("notes"),
                    r.get("applied_at"),
                    exists["id"],
                ),
            )
            new_uj_id = exists["id"]
        else:
            cur = conn.execute(
                """
                INSERT INTO user_jobs (
                    user_id, catalog_job_id, status, match_score, match_summary,
                    match_details, notes, applied_at, synced_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uid,
                    cid,
                    r.get("status", "new"),
                    r.get("match_score"),
                    r.get("match_summary"),
                    r.get("match_details"),
                    r.get("notes"),
                    r.get("applied_at"),
                    datetime.utcnow().isoformat(),
                ),
            )
            new_uj_id = cur.lastrowid

        old_id = r["id"]
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='applications'"
        ).fetchone():
            conn.execute("UPDATE applications SET job_id=? WHERE job_id=?", (new_uj_id, old_id))
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='application_events'"
        ).fetchone():
            conn.execute("UPDATE application_events SET job_id=? WHERE job_id=?", (new_uj_id, old_id))


def _normalize_fingerprint_part(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def compute_fingerprint(title: str, company: str, location: str) -> str:
    """Exact-match (title, company, location) identity for duplicate flagging.

    See the ``fingerprint`` column comment in ``migrate_catalog_schema`` for
    why this exists alongside ``url_hash`` and why it only flags rather than
    merges. Empty when title or company is missing — nothing meaningful to
    group on.
    """
    title_n = _normalize_fingerprint_part(title)
    company_n = _normalize_fingerprint_part(company)
    if not title_n or not company_n:
        return ""
    location_n = _normalize_fingerprint_part(location)
    return hashlib.sha256(f"{title_n}|{company_n}|{location_n}".encode()).hexdigest()[:32]


def upsert_catalog_job(job: dict[str, Any], source_id: str = "", ats_type: str = "") -> int:
    now = datetime.utcnow().isoformat()
    uh = url_hash(job.get("url", ""))
    if not uh or not job.get("url"):
        return -1
    fingerprint = compute_fingerprint(job.get("title", ""), job.get("company", ""), job.get("location", ""))
    incoming_tier = job.get("source_tier") or "aggregator"

    with connect() as conn:
        row = conn.execute(
            "SELECT id, source_tier, status FROM catalog_jobs WHERE url_hash = ?",
            (uh,),
        ).fetchone()
        if row:
            if row["source_tier"] == "native" and incoming_tier != "native":
                # An aggregator re-crawl of a URL already known from a
                # native fetch must not clobber the more trustworthy native
                # data (date, description, location, status) with a
                # possibly-stale aggregator copy of the same posting — only
                # confirm it's still listed. Equivalent to
                # touch_catalog_last_seen for this one row.
                #
                # 'expired' is the one status this must never overwrite: it
                # means a native re-crawl of the company's own listing
                # confirmed the job is gone (see expire_missing_catalog_jobs).
                # An aggregator's copy of that same URL can easily still be
                # listed (its own crawl is on a different, often slower,
                # cycle) — reactivating on that sighting would silently
                # reopen a job the company itself closed.
                if row["status"] != "expired":
                    conn.execute(
                        "UPDATE catalog_jobs SET last_seen=?, status='active' WHERE url_hash=?",
                        (now, uh),
                    )
                else:
                    conn.execute("UPDATE catalog_jobs SET last_seen=? WHERE url_hash=?", (now, uh))
                return row["id"]

            conn.execute(
                """
                UPDATE catalog_jobs SET source=?, source_id=?, title=?, company=?,
                    location=?, salary=?, tags=?, date_posted=?, url=?,
                    description_short=COALESCE(NULLIF(?, ''), description_short),
                    ats_type=COALESCE(NULLIF(?, ''), ats_type),
                    fingerprint=?, source_tier=?, company_slug=?,
                    last_seen=?, status='active'
                WHERE url_hash=?
                """,
                (
                    job.get("source", ""),
                    source_id,
                    job.get("title", ""),
                    job.get("company", ""),
                    job.get("location", ""),
                    job.get("salary", ""),
                    job.get("tags", ""),
                    job.get("date_posted", ""),
                    job.get("url", ""),
                    job.get("description", "") or job.get("description_short", ""),
                    ats_type,
                    fingerprint,
                    incoming_tier,
                    job.get("company_slug", ""),
                    now,
                    uh,
                ),
            )
            return row["id"]

        cur = conn.execute(
            """
            INSERT INTO catalog_jobs (
                url_hash, source, source_id, title, company, location, salary, tags,
                date_posted, url, description_short, ats_type, fingerprint,
                source_tier, company_slug, status, first_seen, last_seen
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            (
                uh,
                job.get("source", ""),
                source_id,
                job.get("title", ""),
                job.get("company", ""),
                job.get("location", ""),
                job.get("salary", ""),
                job.get("tags", ""),
                job.get("date_posted", ""),
                job.get("url", ""),
                job.get("description", "") or job.get("description_short", ""),
                ats_type,
                fingerprint,
                incoming_tier,
                job.get("company_slug", ""),
                now,
                now,
            ),
        )
        return cur.lastrowid or -1


def touch_catalog_last_seen(url_hashes: list[str]) -> int:
    """Bump last_seen (and clear 'stale') for URLs the crawl still lists.

    A source's ``skip_existing`` filters an already-known URL out of the
    batch it hands to ``upsert_job``, so that job's ``last_seen`` would
    otherwise freeze at whenever it was first crawled — mark_catalog_stale
    would then mark it stale after stale_job_days purely because nothing
    ever touched it again, even though the posting is still listed on the
    board every single crawl. This does the same "still active" bump a full
    upsert does, without paying for a full row rewrite, so a source can
    filter its returned batch for budget/parsing reasons without silently
    making every job it already knows about go stale.

    Never reactivates an 'expired' row, though — expired is only ever set
    by expire_missing_catalog_jobs, a confirmed native re-crawl finding the
    job gone from the company's own listing. This function's only current
    caller is the aggregator adapter, whose crawl is neither native nor
    guaranteed to run on the same cycle, so it seeing a URL still listed is
    not good enough evidence to reopen something the company itself closed.
    """
    if not url_hashes:
        return 0
    now = datetime.utcnow().isoformat()
    updated = 0
    with connect() as conn:
        for i in range(0, len(url_hashes), _SQLITE_IN_BATCH):
            chunk = url_hashes[i : i + _SQLITE_IN_BATCH]
            placeholders = ",".join("?" * len(chunk))
            cur = conn.execute(
                f"UPDATE catalog_jobs SET last_seen=?, "
                f"status = CASE WHEN status = 'expired' THEN status ELSE 'active' END "
                f"WHERE url_hash IN ({placeholders})",
                (now, *chunk),
            )
            updated += cur.rowcount
    return updated


def mark_catalog_stale(days: int = 30) -> int:
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    with connect() as conn:
        cur = conn.execute(
            """
            UPDATE catalog_jobs SET status='stale'
            WHERE last_seen < ? AND status = 'active'
            """,
            (cutoff,),
        )
        stale_ids = [
            r["id"]
            for r in conn.execute(
                "SELECT id FROM catalog_jobs WHERE status='stale'"
            ).fetchall()
        ]
        if stale_ids:
            placeholders = ",".join("?" * len(stale_ids))
            status_placeholders = ",".join("?" * len(_STALE_DEMOTABLE_STATUSES))
            conn.execute(
                f"""
                UPDATE user_jobs SET status='stale', classified_by='stale_timeout'
                WHERE catalog_job_id IN ({placeholders})
                  AND status IN ({status_placeholders})
                """,
                (*stale_ids, *_STALE_DEMOTABLE_STATUSES),
            )
        return cur.rowcount


def expire_missing_catalog_jobs(ats_type: str, company_slug: str, current_urls: set[str]) -> int:
    """Mark this company's native catalog rows 'expired' if its own current
    listing no longer includes them — the strongest possible "this job
    closed" signal, since it came straight from the company's ATS, not a
    last_seen timeout.

    Callers MUST only call this after a confirmed *successful* fetch of
    that company's full current listing (see the native adapters'
    ``company_status`` — a non-200 response or an exception must never
    reach here). An empty ``current_urls`` from a failed fetch would
    otherwise look identical to "this company closed every one of its
    jobs", which is almost always wrong.

    ``current_urls`` is compared by ``url_hash``, not raw string equality —
    it should be every URL the company's own listing currently shows,
    filtered for relevance/location or not; a cosmetic difference (tracking
    param, trailing slash, scheme casing) between the URL the catalog
    stored and the one the fresh fetch returned must not read as "gone".
    """
    current_hashes = {url_hash(u) for u in current_urls}
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, url FROM catalog_jobs
            WHERE ats_type = ? AND company_slug = ? AND source_tier = 'native' AND status = 'active'
            """,
            (ats_type, company_slug),
        ).fetchall()
        missing_ids = [r["id"] for r in rows if url_hash(r["url"]) not in current_hashes]
        if not missing_ids:
            return 0
        placeholders = ",".join("?" * len(missing_ids))
        conn.execute(f"UPDATE catalog_jobs SET status='expired' WHERE id IN ({placeholders})", missing_ids)
        status_placeholders = ",".join("?" * len(_STALE_DEMOTABLE_STATUSES))
        conn.execute(
            f"""
            UPDATE user_jobs SET status='stale', classified_by='closed'
            WHERE catalog_job_id IN ({placeholders})
              AND status IN ({status_placeholders})
            """,
            (*missing_ids, *_STALE_DEMOTABLE_STATUSES),
        )
    return len(missing_ids)


def ensure_user_job_for_catalog(catalog_job_id: int, user_id: int) -> int:
    now = datetime.utcnow().isoformat()
    with connect() as conn:
        row = conn.execute(
            "SELECT id FROM user_jobs WHERE user_id = ? AND catalog_job_id = ?",
            (user_id, catalog_job_id),
        ).fetchone()
        if row:
            return row["id"]
        cur = conn.execute(
            """
            INSERT INTO user_jobs (user_id, catalog_job_id, status, synced_at)
            VALUES (?, ?, 'new', ?)
            """,
            (user_id, catalog_job_id, now),
        )
        return cur.lastrowid or -1


def sync_catalog_to_user(user_id: int | None = None, limit: int = 500) -> int:
    uid = resolve_user_id(user_id)
    now = datetime.utcnow().isoformat()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT cj.id FROM catalog_jobs cj
            WHERE cj.status = 'active'
              AND cj.id NOT IN (
                SELECT catalog_job_id FROM user_jobs WHERE user_id = ?
              )
            ORDER BY cj.last_seen DESC
            LIMIT ?
            """,
            (uid, limit),
        ).fetchall()
        count = 0
        for row in rows:
            conn.execute(
                """
                INSERT INTO user_jobs (user_id, catalog_job_id, status, synced_at)
                VALUES (?, ?, 'new', ?)
                """,
                (uid, row["id"], now),
            )
            count += 1
    if count:
        print(f"  Synced {count} catalog jobs to user {uid}")
    return count


def get_catalog_url_hashes() -> set[str]:
    """Every url_hash already in the shared catalog, regardless of status or user.

    Used by adapters' ``skip_existing`` to avoid re-fetching a posting the
    crawl already knows about. This is deliberately catalog-wide rather
    than scoped to one user's synced queue (``get_existing_url_hashes``) —
    a source-level "have we seen this URL" check has nothing to do with
    whether any particular user has synced it yet, and scoping it to a
    user (the previous behavior, via a default-tenant fallback) meant the
    crawl re-processed URLs that were already in the catalog just because
    they hadn't reached that one user's queue.
    """
    with connect() as conn:
        rows = conn.execute("SELECT url_hash FROM catalog_jobs").fetchall()
    return {row["url_hash"] for row in rows}


def get_catalog_jobs_needing_enrichment(limit: int = 100) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, url, title, company, description_short, source
            FROM catalog_jobs
            WHERE status = 'active'
              AND (description_full IS NULL OR description_full = '')
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def update_catalog_job(catalog_job_id: int, **fields: Any) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    with connect() as conn:
        conn.execute(
            f"UPDATE catalog_jobs SET {cols} WHERE id=?",
            (*fields.values(), catalog_job_id),
        )


def get_catalog_job(catalog_job_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM catalog_jobs WHERE id = ?",
            (catalog_job_id,),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["embedding_vec"] = blob_to_embedding(d.pop("embedding", None))
    return d


def list_catalog_jobs(limit: int = 200, status: str = "active") -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM catalog_jobs
            WHERE status = ?
            ORDER BY last_seen DESC
            LIMIT ?
            """,
            (status, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# --- Target companies ---


def list_target_companies(user_id: int) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, user_id, company_slug, ats_type, tier, display_name,
                   careers_url, enabled, origin, created_at, updated_at
            FROM target_companies
            WHERE user_id = ?
            ORDER BY tier ASC, display_name ASC
            """,
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def add_target_company(
    user_id: int,
    company_slug: str,
    ats_type: str,
    *,
    tier: int = 2,
    display_name: str = "",
    careers_url: str = "",
    origin: str = "manual",
) -> int:
    """Add or re-enable a target company.

    origin only ever moves "auto" -> "manual", never the reverse: a person
    adding a company by hand (or pinning an auto-added one) always makes it
    manual, but auto-add re-touching a row a person already promoted to
    manual must not silently demote it back to "auto" (which would make it
    eligible for auto-prune again against their wishes).
    """
    now = datetime.utcnow().isoformat()
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO target_companies (
                user_id, company_slug, ats_type, tier, display_name,
                careers_url, enabled, origin, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
            ON CONFLICT(user_id, company_slug, ats_type) DO UPDATE SET
                tier=excluded.tier,
                display_name=excluded.display_name,
                careers_url=excluded.careers_url,
                enabled=1,
                origin=CASE WHEN excluded.origin = 'manual' THEN 'manual' ELSE target_companies.origin END,
                updated_at=excluded.updated_at
            """,
            (
                user_id,
                company_slug,
                ats_type,
                tier,
                display_name or company_slug.replace("-", " ").title(),
                careers_url,
                origin,
                now,
                now,
            ),
        )
        return cur.lastrowid or -1


def pin_target_company(company_id: int, user_id: int) -> None:
    """Turn an auto-added company into a manual one — never auto-pruned again."""
    now = datetime.utcnow().isoformat()
    with connect() as conn:
        conn.execute(
            "UPDATE target_companies SET origin = 'manual', updated_at = ? WHERE id = ? AND user_id = ?",
            (now, company_id, user_id),
        )


def block_target_company(user_id: int, company_slug: str, ats_type: str) -> None:
    now = datetime.utcnow().isoformat()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO target_company_blocklist (user_id, company_slug, ats_type, blocked_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, company_slug, ats_type) DO UPDATE SET blocked_at = excluded.blocked_at
            """,
            (user_id, company_slug, ats_type, now),
        )


def delete_target_company(company_id: int, user_id: int, *, blocklist: bool = False) -> bool:
    """Remove a target company. blocklist=True also stops auto-add from
    ever re-adding it — pass this only for a person's own removal of an
    auto-added company, not for a routine auto-prune (which should stay
    re-addable if the company starts matching well again)."""
    with connect() as conn:
        if blocklist:
            row = conn.execute(
                "SELECT company_slug, ats_type FROM target_companies WHERE id = ? AND user_id = ?",
                (company_id, user_id),
            ).fetchone()
            if row:
                conn.execute(
                    """
                    INSERT INTO target_company_blocklist (user_id, company_slug, ats_type, blocked_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(user_id, company_slug, ats_type) DO UPDATE SET blocked_at = excluded.blocked_at
                    """,
                    (user_id, row["company_slug"], row["ats_type"], datetime.utcnow().isoformat()),
                )
        cur = conn.execute(
            "DELETE FROM target_companies WHERE id = ? AND user_id = ?",
            (company_id, user_id),
        )
        return cur.rowcount > 0


def toggle_target_company(company_id: int, user_id: int, enabled: bool) -> None:
    now = datetime.utcnow().isoformat()
    with connect() as conn:
        conn.execute(
            """
            UPDATE target_companies SET enabled = ?, updated_at = ?
            WHERE id = ? AND user_id = ?
            """,
            (1 if enabled else 0, now, company_id, user_id),
        )


def sync_target_company_watchlist(
    user_id: int,
    *,
    threshold: int,
    auto_max: int = 50,
    auto_prune_days: int = 60,
) -> dict[str, int]:
    """Hybrid watchlist: auto-add companies whose jobs keep scoring well,
    auto-prune ones that stopped, never touch anything a person added or
    pinned by hand.

    A company (company_slug, ats_type) is a candidate to auto-add once this
    user's queue has at least 2 of its jobs scored at or above
    ``threshold``, or at least 1 applied to — only among jobs whose
    catalog row is source_tier='native', since only a native ATS adapter
    can actually be re-crawled by company (an aggregator posting has no
    reliable company_slug). A company a person explicitly removed after it
    was auto-added (see delete_target_company's blocklist option) is never
    re-added. At most ``auto_max`` companies carry origin='auto' at once.

    An origin='auto' company is pruned once it's been on the list for more
    than ``auto_prune_days`` and it currently has neither (a) a job the
    user has ever applied to, nor (b) a still-*open* job (catalog
    status='active') scored at or above threshold — never a 'manual'-
    origin one. (b) has to be scoped to a currently-open job, not "any job
    that ever scored well": the auto-add condition above is exactly "has a
    job scored well", and match_score is never cleared off a user_jobs row
    once its underlying posting closes, so an unscoped check would find
    the very (now long-closed) job that got the company added in the
    first place and could never prune anything. Once that job is marked
    'stale' or 'expired' (by mark_catalog_stale or, more reliably,
    expire_missing_catalog_jobs — the next native re-crawl of this exact
    company), it naturally stops counting here. (a) has no such time or
    freshness bound: applying is a deliberate, lasting signal that a
    company is worth continuing to watch, not something that should decay.
    """
    added = 0
    pruned = 0
    now = datetime.utcnow()
    with connect() as conn:
        existing = conn.execute(
            "SELECT id, company_slug, ats_type, origin, created_at FROM target_companies WHERE user_id = ?",
            (user_id,),
        ).fetchall()
        existing_keys = {(r["company_slug"], r["ats_type"]) for r in existing}
        auto_count = sum(1 for r in existing if r["origin"] == "auto")

        blocked = {
            (r["company_slug"], r["ats_type"])
            for r in conn.execute(
                "SELECT company_slug, ats_type FROM target_company_blocklist WHERE user_id = ?",
                (user_id,),
            ).fetchall()
        }

        candidates = conn.execute(
            """
            SELECT cj.company_slug, cj.ats_type, cj.company AS display_name,
                   SUM(CASE WHEN uj.match_score >= ? THEN 1 ELSE 0 END) AS good_count,
                   SUM(CASE WHEN uj.status = 'applied' THEN 1 ELSE 0 END) AS applied_count
            FROM catalog_jobs cj
            JOIN user_jobs uj ON uj.catalog_job_id = cj.id AND uj.user_id = ?
            WHERE cj.source_tier = 'native' AND cj.company_slug IS NOT NULL AND cj.company_slug != ''
            GROUP BY cj.company_slug, cj.ats_type
            HAVING good_count >= 2 OR applied_count >= 1
            ORDER BY applied_count DESC, good_count DESC
            """,
            (threshold, user_id),
        ).fetchall()

        for c in candidates:
            if auto_count >= auto_max:
                break
            key = (c["company_slug"], c["ats_type"])
            if key in existing_keys or key in blocked:
                continue
            add_target_company(
                user_id,
                c["company_slug"],
                c["ats_type"],
                display_name=c["display_name"] or "",
                origin="auto",
            )
            existing_keys.add(key)
            auto_count += 1
            added += 1

        cutoff = now - timedelta(days=auto_prune_days)
        for r in existing:
            if r["origin"] != "auto":
                continue
            try:
                created = datetime.fromisoformat(r["created_at"])
            except (TypeError, ValueError):
                continue
            if created > cutoff:
                continue  # still within its grace period

            ever_applied = conn.execute(
                """
                SELECT COUNT(*) c FROM catalog_jobs cj
                JOIN user_jobs uj ON uj.catalog_job_id = cj.id AND uj.user_id = ?
                WHERE cj.company_slug = ? AND cj.ats_type = ? AND uj.status = 'applied'
                """,
                (user_id, r["company_slug"], r["ats_type"]),
            ).fetchone()["c"]
            currently_qualifying = conn.execute(
                """
                SELECT COUNT(*) c FROM catalog_jobs cj
                JOIN user_jobs uj ON uj.catalog_job_id = cj.id AND uj.user_id = ?
                WHERE cj.company_slug = ? AND cj.ats_type = ?
                  AND cj.status = 'active' AND uj.match_score >= ?
                """,
                (user_id, r["company_slug"], r["ats_type"], threshold),
            ).fetchone()["c"]
            if ever_applied == 0 and currently_qualifying == 0:
                conn.execute("DELETE FROM target_companies WHERE id = ?", (r["id"],))
                pruned += 1

    return {"added": added, "pruned": pruned}
