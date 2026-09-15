"""Shared job catalog + per-user overlay (crawl once, match per user)."""

from __future__ import annotations

import json
import struct
from datetime import datetime, timedelta
from typing import Any

from src.db import connect, url_hash
from src.tenant import resolve_user_id

CATALOG_FIELDS = frozenset({
    "source", "source_id", "title", "company", "location", "salary", "tags",
    "date_posted", "url", "description_short", "description_full", "ats_type",
    "first_seen", "last_seen", "status", "url_hash", "embedding",
})

USER_JOB_FIELDS = frozenset({
    "status", "match_score", "match_summary", "match_details", "vector_score",
    "notes", "applied_at", "match_attempts",
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
  uj.status AS status,
  uj.match_score AS match_score,
  uj.match_summary AS match_summary,
  uj.match_details AS match_details,
  uj.vector_score AS vector_score,
  uj.notes AS notes,
  uj.applied_at AS applied_at,
  uj.match_attempts AS match_attempts
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
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, company_slug, ats_type)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_target_companies_user ON target_companies(user_id)")

    uj_cols = {r[1] for r in conn.execute("PRAGMA table_info(user_jobs)").fetchall()}
    if "match_attempts" not in uj_cols:
        conn.execute("ALTER TABLE user_jobs ADD COLUMN match_attempts INTEGER DEFAULT 0")

    # Stage-1 requirement extraction, cached on the shared catalog row so the
    # cost is paid once per posting rather than once per user.
    cat_cols = {r[1] for r in conn.execute("PRAGMA table_info(catalog_jobs)").fetchall()}
    for column, ddl in (
        ("requirements_json", "requirements_json TEXT"),
        ("requirements_hash", "requirements_hash TEXT"),
        ("requirements_model", "requirements_model TEXT"),
        ("requirements_at", "requirements_at TEXT"),
        # Hash of the text last used to warm this job's posting-chunk vectors
        # (src.embeddings.embed_catalog_jobs) — a mismatch means the
        # description changed (e.g. the enrich stage filled it in) and the
        # job needs re-embedding, not just jobs with no embedding at all.
        ("embed_content_hash", "embed_content_hash TEXT"),
    ):
        if column not in cat_cols:
            conn.execute(f"ALTER TABLE catalog_jobs ADD COLUMN {ddl}")

    # Model-tagged vector cache for resume chunks and posting requirements.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vector_store (
            owner_kind TEXT NOT NULL,
            owner_id   TEXT NOT NULL,
            model      TEXT NOT NULL,
            dim        INTEGER NOT NULL,
            text_hash  TEXT NOT NULL,
            vec        BLOB NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (owner_kind, owner_id, model)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_vector_store_kind ON vector_store(owner_kind, model)"
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


def upsert_catalog_job(job: dict[str, Any], source_id: str = "", ats_type: str = "") -> int:
    now = datetime.utcnow().isoformat()
    uh = url_hash(job.get("url", ""))
    if not uh or not job.get("url"):
        return -1

    with connect() as conn:
        row = conn.execute(
            "SELECT id FROM catalog_jobs WHERE url_hash = ?",
            (uh,),
        ).fetchone()
        if row:
            conn.execute(
                """
                UPDATE catalog_jobs SET source=?, source_id=?, title=?, company=?,
                    location=?, salary=?, tags=?, date_posted=?, url=?,
                    description_short=COALESCE(NULLIF(?, ''), description_short),
                    ats_type=COALESCE(NULLIF(?, ''), ats_type),
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
                    now,
                    uh,
                ),
            )
            return row["id"]

        cur = conn.execute(
            """
            INSERT INTO catalog_jobs (
                url_hash, source, source_id, title, company, location, salary, tags,
                date_posted, url, description_short, ats_type, status, first_seen, last_seen
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
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
                now,
                now,
            ),
        )
        return cur.lastrowid or -1


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
            conn.execute(
                f"""
                UPDATE user_jobs SET status='stale'
                WHERE catalog_job_id IN ({placeholders})
                  AND status IN ('new', 'scored')
                """,
                stale_ids,
            )
        return cur.rowcount


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
                   careers_url, enabled, created_at, updated_at
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
) -> int:
    now = datetime.utcnow().isoformat()
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO target_companies (
                user_id, company_slug, ats_type, tier, display_name,
                careers_url, enabled, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(user_id, company_slug, ats_type) DO UPDATE SET
                tier=excluded.tier,
                display_name=excluded.display_name,
                careers_url=excluded.careers_url,
                enabled=1,
                updated_at=excluded.updated_at
            """,
            (
                user_id,
                company_slug,
                ats_type,
                tier,
                display_name or company_slug.replace("-", " ").title(),
                careers_url,
                now,
                now,
            ),
        )
        return cur.lastrowid or -1


def delete_target_company(company_id: int, user_id: int) -> bool:
    with connect() as conn:
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
