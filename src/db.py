"""SQLite persistence for jobs, applications, profile, users, and source registry."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from src import settings
from src.settings import PROFILE_DIR, ensure_dirs, user_profile_dir
from src.tenant import resolve_user_id
from src.config_store import COLLECTION_NAMES, DEFAULT_COLLECTIONS, PLATFORM_SCOPE_ID, PLATFORM_SOURCES_USER_ID, SCOPE_PLATFORM, SCOPE_USER


def url_hash(url: str) -> str:
    return hashlib.sha256(url.strip().lower().encode()).hexdigest()


def _uid(user_id: int | None = None) -> int:
    return resolve_user_id(user_id)


@contextmanager
def connect() -> Iterator[Any]:
    ensure_dirs()
    conn = sqlite3.connect(settings.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _table_columns(conn, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


AUTH_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT,
    display_name TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS oauth_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    provider_user_id TEXT NOT NULL,
    UNIQUE(provider, provider_user_id)
);

CREATE TABLE IF NOT EXISTS user_settings (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    config_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _table_exists(conn, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _add_column_if_missing(conn, table: str, column: str, ddl: str) -> None:
    if column not in _table_columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def _migrate_multi_user(conn) -> None:
    conn.executescript(AUTH_SCHEMA)

    if not _table_exists(conn, "jobs"):
        conn.execute(
            """
            CREATE TABLE jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL DEFAULT 1,
                url_hash TEXT NOT NULL,
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
                status TEXT DEFAULT 'new',
                match_score REAL,
                match_summary TEXT,
                match_details TEXT,
                notes TEXT,
                first_seen TEXT,
                last_seen TEXT,
                applied_at TEXT
            )
            """
        )
    else:
        for col, ddl in [
            ("user_id", "user_id INTEGER NOT NULL DEFAULT 1"),
            ("source", "source TEXT"),
            ("source_id", "source_id TEXT"),
            ("title", "title TEXT"),
            ("company", "company TEXT"),
            ("location", "location TEXT"),
            ("salary", "salary TEXT"),
            ("tags", "tags TEXT"),
            ("date_posted", "date_posted TEXT"),
            ("url", "url TEXT"),
            ("description_short", "description_short TEXT"),
            ("description_full", "description_full TEXT"),
            ("status", "status TEXT DEFAULT 'new'"),
            ("match_score", "match_score REAL"),
            ("match_summary", "match_summary TEXT"),
            ("match_details", "match_details TEXT"),
            ("notes", "notes TEXT"),
            ("first_seen", "first_seen TEXT"),
            ("last_seen", "last_seen TEXT"),
            ("applied_at", "applied_at TEXT"),
        ]:
            _add_column_if_missing(conn, "jobs", col, ddl)

    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_match_score ON jobs(match_score)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_user ON jobs(user_id)")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_user_url ON jobs(user_id, url_hash)"
    )

    if not _table_exists(conn, "applications"):
        conn.execute(
            """
            CREATE TABLE applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER UNIQUE NOT NULL,
                tailored_cv_path TEXT,
                cover_letter_path TEXT,
                cover_letter_text TEXT,
                tailored_resume_json TEXT,
                apply_method TEXT,
                apply_result TEXT,
                cv_template_id INTEGER,
                created_at TEXT,
                updated_at TEXT,
                version INTEGER DEFAULT 1,
                FOREIGN KEY (job_id) REFERENCES jobs(id)
            )
            """
        )
    else:
        _add_column_if_missing(conn, "applications", "cover_letter_text", "cover_letter_text TEXT")
        _add_column_if_missing(conn, "applications", "version", "version INTEGER DEFAULT 1")
        _add_column_if_missing(conn, "applications", "cv_template_id", "cv_template_id INTEGER")

    if not _table_exists(conn, "cv_templates"):
        conn.execute(
            """
            CREATE TABLE cv_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                slug TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                kind TEXT NOT NULL DEFAULT 'preset',
                preset_json TEXT NOT NULL,
                html_path TEXT,
                is_default INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(user_id, slug)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_cv_templates_user ON cv_templates(user_id)"
        )

    if not _table_exists(conn, "profile"):
        conn.execute(
            """
            CREATE TABLE profile (
                user_id INTEGER PRIMARY KEY,
                resume_json TEXT,
                source_pdf_path TEXT,
                github_json TEXT,
                evaluation_json TEXT,
                updated_at TEXT
            )
            """
        )
    else:
        prof_cols = _table_columns(conn, "profile")
        if "id" in prof_cols and "user_id" not in prof_cols:
            conn.execute(
                """
                CREATE TABLE profile_new (
                    user_id INTEGER PRIMARY KEY,
                    resume_json TEXT,
                    source_pdf_path TEXT,
                    github_json TEXT,
                    evaluation_json TEXT,
                    updated_at TEXT
                )
                """
            )
            conn.execute(
                """
                INSERT INTO profile_new (user_id, resume_json, source_pdf_path, github_json, evaluation_json, updated_at)
                SELECT 1, resume_json, source_pdf_path, github_json, evaluation_json, updated_at
                FROM profile WHERE id = 1
                """
            )
            conn.execute("DROP TABLE profile")
            conn.execute("ALTER TABLE profile_new RENAME TO profile")
        _add_column_if_missing(conn, "profile", "evaluation_json", "evaluation_json TEXT")

    if not _table_exists(conn, "source_registry"):
        conn.execute(
            """
            CREATE TABLE source_registry (
                user_id INTEGER NOT NULL DEFAULT 1,
                id TEXT NOT NULL,
                name TEXT,
                adapter TEXT,
                config_json TEXT,
                enabled INTEGER DEFAULT 1,
                discovered_at TEXT,
                last_run_at TEXT,
                last_status TEXT,
                last_error TEXT,
                PRIMARY KEY (user_id, id)
            )
            """
        )
    elif "user_id" not in _table_columns(conn, "source_registry"):
        conn.execute(
            """
            CREATE TABLE source_registry_new (
                user_id INTEGER NOT NULL DEFAULT 1,
                id TEXT NOT NULL,
                name TEXT,
                adapter TEXT,
                config_json TEXT,
                enabled INTEGER DEFAULT 1,
                discovered_at TEXT,
                last_run_at TEXT,
                last_status TEXT,
                last_error TEXT,
                PRIMARY KEY (user_id, id)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO source_registry_new
            SELECT 1, id, name, adapter, config_json, enabled, discovered_at, last_run_at, last_status, last_error
            FROM source_registry
            """
        )
        conn.execute("DROP TABLE source_registry")
        conn.execute("ALTER TABLE source_registry_new RENAME TO source_registry")

    if not _table_exists(conn, "application_events"):
        conn.execute(
            """
            CREATE TABLE application_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                detail_json TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (job_id) REFERENCES jobs(id)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_app_events_job ON application_events(job_id)")

    if not _table_exists(conn, "pipeline_runs"):
        conn.execute(
            """
            CREATE TABLE pipeline_runs (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL DEFAULT 1,
                stages_json TEXT,
                summary_json TEXT,
                log_tail TEXT,
                error TEXT,
                started_at TEXT,
                finished_at TEXT
            )
            """
        )
    else:
        _add_column_if_missing(conn, "pipeline_runs", "user_id", "user_id INTEGER NOT NULL DEFAULT 1")

    if not _table_exists(conn, "password_reset_tokens"):
        conn.execute(
            """
            CREATE TABLE password_reset_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                token_hash TEXT UNIQUE NOT NULL,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                used_at TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_reset_token_hash ON password_reset_tokens(token_hash)"
        )

    _migrate_collections(conn)
    from src.catalog_db import migrate_catalog_schema

    migrate_catalog_schema(conn)
    _bootstrap_legacy_data(conn)


COLLECTIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS config_collections (
    scope TEXT NOT NULL,
    scope_id INTEGER NOT NULL,
    collection TEXT NOT NULL,
    data_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (scope, scope_id, collection)
);

CREATE TABLE IF NOT EXISTS user_sources (
    user_id INTEGER NOT NULL,
    source_id TEXT NOT NULL,
    name TEXT,
    adapter TEXT NOT NULL,
    enabled INTEGER DEFAULT 1,
    config_json TEXT NOT NULL,
    discovered INTEGER DEFAULT 0,
    last_run_at TEXT,
    last_status TEXT,
    last_error TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, source_id)
);

CREATE TABLE IF NOT EXISTS user_llm_credentials (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    api_key_ciphertext TEXT NOT NULL,
    key_hint TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, provider)
);

CREATE TABLE IF NOT EXISTS user_imap_credentials (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    password_ciphertext TEXT NOT NULL,
    key_hint TEXT,
    updated_at TEXT NOT NULL
);
"""


def _migrate_collections(conn) -> None:
    conn.executescript(COLLECTIONS_SCHEMA)
    _migrate_user_settings_to_collections(conn)


def _migrate_user_settings_to_collections(conn) -> None:
    if not _table_exists(conn, "user_settings"):
        return
    rows = conn.execute("SELECT user_id, config_json FROM user_settings").fetchall()
    now = datetime.utcnow().isoformat()
    for row in rows:
        user_id = row["user_id"]
        try:
            blob = json.loads(row["config_json"] or "{}")
        except json.JSONDecodeError:
            continue
        if not isinstance(blob, dict):
            continue
        for name in COLLECTION_NAMES:
            if name not in blob:
                continue
            existing = conn.execute(
                "SELECT 1 FROM config_collections WHERE scope=? AND scope_id=? AND collection=?",
                (SCOPE_USER, user_id, name),
            ).fetchone()
            if existing:
                continue
            conn.execute(
                """
                INSERT INTO config_collections (scope, scope_id, collection, data_json, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (SCOPE_USER, user_id, name, json.dumps(blob[name], ensure_ascii=False), now),
            )
        sources = blob.get("sources")
        if isinstance(sources, list) and sources:
            for src in sources:
                sid = src.get("id")
                if not sid:
                    continue
                exists = conn.execute(
                    "SELECT 1 FROM user_sources WHERE user_id=? AND source_id=?",
                    (user_id, sid),
                ).fetchone()
                if exists:
                    continue
                cfg = {k: v for k, v in src.items() if k not in ("id", "name", "adapter", "enabled", "discovered")}
                conn.execute(
                    """
                    INSERT INTO user_sources (user_id, source_id, name, adapter, enabled, config_json, discovered, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        sid,
                        src.get("name", ""),
                        src.get("adapter", "json_api"),
                        1 if src.get("enabled", True) else 0,
                        json.dumps(cfg, ensure_ascii=False),
                        1 if src.get("discovered") else 0,
                        now,
                    ),
                )


def _bootstrap_legacy_data(conn) -> None:
    user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if user_count > 0:
        return

    has_jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    has_profile = conn.execute("SELECT COUNT(*) FROM profile").fetchone()[0]
    if not (has_jobs or has_profile):
        return

    # Import any legacy config.yaml into platform config_collections *before*
    # reading the bootstrap user's applicant info below, so a configured
    # applicant.email is picked up rather than the "migration@local" default.
    # Commit first — auto_import_if_empty() writes via its own connection and
    # would otherwise self-deadlock against this connection's open transaction.
    conn.commit()
    from src.yaml_import import auto_import_if_empty

    auto_import_if_empty()

    from src.config_store import DEFAULT_COLLECTIONS

    applicant = DEFAULT_COLLECTIONS.get("applicant", {})
    if count_config_collections(SCOPE_PLATFORM, PLATFORM_SCOPE_ID) > 0:
        from src.config_store import get_collection

        applicant = get_collection("applicant", scope=SCOPE_PLATFORM, scope_id=PLATFORM_SCOPE_ID)
    email = (applicant.get("email") or "migration@local").strip().lower()
    name = applicant.get("name") or "Migration User"
    now = datetime.utcnow().isoformat()

    conn.execute(
        """
        INSERT INTO users (id, email, password_hash, display_name, created_at, updated_at)
        VALUES (1, ?, NULL, ?, ?, ?)
        """,
        (email, name, now, now),
    )
    # Release the write lock before calling into copy_platform_*_to_user below —
    # those helpers open their own connections via connect() to write, which
    # would otherwise self-deadlock against this connection's still-open
    # transaction until the busy timeout expires ("database is locked").
    conn.commit()

    copy_platform_config_to_user(1)
    copy_platform_sources_to_user(1)

    conn.execute("UPDATE jobs SET user_id = 1 WHERE user_id IS NULL OR user_id = 0")
    conn.execute("UPDATE pipeline_runs SET user_id = 1 WHERE user_id IS NULL OR user_id = 0")

    if PROFILE_DIR.exists():
        dest = user_profile_dir(1)
        dest.mkdir(parents=True, exist_ok=True)
        for item in PROFILE_DIR.iterdir():
            target = dest / item.name
            if item.is_file() and not target.exists():
                shutil.copy2(item, target)
        row = conn.execute("SELECT source_pdf_path FROM profile WHERE user_id = 1").fetchone()
        if row and row["source_pdf_path"]:
            old_path = Path(row["source_pdf_path"])
            if old_path.exists() and str(old_path).startswith(str(PROFILE_DIR)):
                new_path = dest / old_path.name
                if not new_path.exists():
                    shutil.copy2(old_path, new_path)
                conn.execute(
                    "UPDATE profile SET source_pdf_path = ? WHERE user_id = 1",
                    (str(new_path),),
                )


def init_db() -> None:
    with connect() as conn:
        _migrate_multi_user(conn)
    from src.yaml_import import auto_import_if_empty

    auto_import_if_empty()


# --- Users ---


def create_user(
    email: str,
    password_hash: str | None = None,
    display_name: str = "",
) -> dict[str, Any]:
    now = datetime.utcnow().isoformat()
    email_norm = email.strip().lower()
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO users (email, password_hash, display_name, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (email_norm, password_hash, display_name or None, now, now),
        )
        user_id = cur.lastrowid
    copy_platform_config_to_user(user_id)
    copy_platform_sources_to_user(user_id)
    return get_user_by_id(user_id) or {}


def get_user_by_id(user_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def get_user_by_email(email: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE email = ?",
            (email.strip().lower(),),
        ).fetchone()
    return dict(row) if row else None


def update_user(user_id: int, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = datetime.utcnow().isoformat()
    cols = ", ".join(f"{k}=?" for k in fields)
    with connect() as conn:
        conn.execute(f"UPDATE users SET {cols} WHERE id=?", (*fields.values(), user_id))


def _hash_reset_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_password_reset_token(user_id: int, *, hours_valid: int = 1) -> str:
    import secrets

    raw = secrets.token_urlsafe(32)
    now = datetime.utcnow()
    expires = (now + timedelta(hours=hours_valid)).isoformat()
    with connect() as conn:
        conn.execute(
            "UPDATE password_reset_tokens SET used_at = ? WHERE user_id = ? AND used_at IS NULL",
            (now.isoformat(), user_id),
        )
        conn.execute(
            """
            INSERT INTO password_reset_tokens (user_id, token_hash, expires_at, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, _hash_reset_token(raw), expires, now.isoformat()),
        )
    return raw


def get_password_reset_token(raw_token: str) -> dict[str, Any] | None:
    token_hash = _hash_reset_token(raw_token)
    now = datetime.utcnow().isoformat()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT t.*, u.email
            FROM password_reset_tokens t
            JOIN users u ON u.id = t.user_id
            WHERE t.token_hash = ? AND t.used_at IS NULL AND t.expires_at > ?
            """,
            (token_hash, now),
        ).fetchone()
    return dict(row) if row else None


def mark_password_reset_token_used(raw_token: str) -> None:
    token_hash = _hash_reset_token(raw_token)
    now = datetime.utcnow().isoformat()
    with connect() as conn:
        conn.execute(
            "UPDATE password_reset_tokens SET used_at = ? WHERE token_hash = ?",
            (now, token_hash),
        )


def get_user_settings(user_id: int) -> dict[str, Any] | None:
    if count_config_collections(SCOPE_USER, user_id) > 0:
        from src.config_store import get_all_collections

        raw = get_all_collections(user_id)
        raw["sources"] = list_user_sources(user_id)
        return raw
    with connect() as conn:
        row = conn.execute(
            "SELECT config_json FROM user_settings WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if not row:
        return None
    return json.loads(row["config_json"] or "{}")


def save_user_settings(user_id: int, config: dict[str, Any]) -> None:
    """Legacy blob save — also syncs into config_collections rows."""
    now = datetime.utcnow().isoformat()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO user_settings (user_id, config_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                config_json=excluded.config_json,
                updated_at=excluded.updated_at
            """,
            (user_id, json.dumps(config, ensure_ascii=False), now),
        )
    for name in COLLECTION_NAMES:
        if name in config:
            save_config_collection(SCOPE_USER, user_id, name, config[name])


# --- Config collections ---


def count_config_collections(scope: str, scope_id: int) -> int:
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM config_collections WHERE scope=? AND scope_id=?",
            (scope, scope_id),
        ).fetchone()
    return row["c"] if row else 0


def get_config_collection(scope: str, scope_id: int, collection: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT data_json FROM config_collections WHERE scope=? AND scope_id=? AND collection=?",
            (scope, scope_id, collection),
        ).fetchone()
    if not row:
        return None
    return json.loads(row["data_json"] or "{}")


def save_config_collection(scope: str, scope_id: int, collection: str, data: Any) -> None:
    now = datetime.utcnow().isoformat()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO config_collections (scope, scope_id, collection, data_json, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(scope, scope_id, collection) DO UPDATE SET
                data_json=excluded.data_json,
                updated_at=excluded.updated_at
            """,
            (scope, scope_id, collection, json.dumps(data, ensure_ascii=False), now),
        )


def list_config_collections(scope: str, scope_id: int) -> dict[str, Any]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT collection, data_json FROM config_collections WHERE scope=? AND scope_id=?",
            (scope, scope_id),
        ).fetchall()
    return {row["collection"]: json.loads(row["data_json"] or "{}") for row in rows}


def copy_platform_config_to_user(user_id: int) -> None:
    platform = list_config_collections(SCOPE_PLATFORM, PLATFORM_SCOPE_ID)
    if not platform:
        for name, data in DEFAULT_COLLECTIONS.items():
            save_config_collection(SCOPE_USER, user_id, name, data)
        return
    for name, data in platform.items():
        save_config_collection(SCOPE_USER, user_id, name, data)


def list_user_ids() -> list[int]:
    with connect() as conn:
        rows = conn.execute("SELECT id FROM users ORDER BY id").fetchall()
    return [row["id"] for row in rows]


# --- User sources ---


def _source_row_to_dict(row: Any) -> dict[str, Any]:
    d = dict(row)
    cfg = json.loads(d.pop("config_json") or "{}")
    result = {
        "id": d["source_id"],
        "name": d.get("name") or "",
        "adapter": d.get("adapter") or "json_api",
        "enabled": bool(d.get("enabled", 1)),
        "discovered": bool(d.get("discovered", 0)),
        **cfg,
    }
    return result


def count_user_sources(user_id: int) -> int:
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM user_sources WHERE user_id=?",
            (user_id,),
        ).fetchone()
    return row["c"] if row else 0


def list_user_sources(user_id: int) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM user_sources WHERE user_id=? ORDER BY name, source_id",
            (user_id,),
        ).fetchall()
    return [_source_row_to_dict(r) for r in rows]


def save_user_sources_bulk(user_id: int, sources: list[dict[str, Any]]) -> None:
    now = datetime.utcnow().isoformat()
    with connect() as conn:
        for src in sources:
            sid = src.get("id")
            if not sid:
                continue
            cfg = {k: v for k, v in src.items() if k not in ("id", "name", "adapter", "enabled", "discovered")}
            conn.execute(
                """
                INSERT INTO user_sources (user_id, source_id, name, adapter, enabled, config_json, discovered, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, source_id) DO UPDATE SET
                    name=excluded.name,
                    adapter=excluded.adapter,
                    enabled=excluded.enabled,
                    config_json=excluded.config_json,
                    discovered=excluded.discovered,
                    updated_at=excluded.updated_at
                """,
                (
                    user_id,
                    sid,
                    src.get("name", ""),
                    src.get("adapter", "json_api"),
                    1 if src.get("enabled", True) else 0,
                    json.dumps(cfg, ensure_ascii=False),
                    1 if src.get("discovered") else 0,
                    now,
                ),
            )


def copy_platform_sources_to_user(user_id: int) -> None:
    platform_sources = list_user_sources(PLATFORM_SOURCES_USER_ID)
    if platform_sources:
        save_user_sources_bulk(user_id, platform_sources)


def update_user_source_status(
    user_id: int,
    source_id: str,
    *,
    enabled: bool | None = None,
    last_status: str | None = None,
    last_error: str | None = None,
) -> None:
    now = datetime.utcnow().isoformat()
    fields: dict[str, Any] = {"updated_at": now, "last_run_at": now}
    if enabled is not None:
        fields["enabled"] = 1 if enabled else 0
    if last_status is not None:
        fields["last_status"] = last_status
    if last_error is not None:
        fields["last_error"] = last_error
    cols = ", ".join(f"{k}=?" for k in fields)
    with connect() as conn:
        conn.execute(
            f"UPDATE user_sources SET {cols} WHERE user_id=? AND source_id=?",
            (*fields.values(), user_id, source_id),
        )


# --- LLM credentials ---


def save_llm_credential(user_id: int, provider: str, api_key: str) -> None:
    from src.secrets import encrypt_secret, mask_secret

    now = datetime.utcnow().isoformat()
    ciphertext = encrypt_secret(api_key)
    hint = mask_secret(api_key)
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO user_llm_credentials (user_id, provider, api_key_ciphertext, key_hint, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, provider) DO UPDATE SET
                api_key_ciphertext=excluded.api_key_ciphertext,
                key_hint=excluded.key_hint,
                updated_at=excluded.updated_at
            """,
            (user_id, provider, ciphertext, hint, now),
        )


def get_llm_credential(user_id: int, provider: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM user_llm_credentials WHERE user_id=? AND provider=?",
            (user_id, provider),
        ).fetchone()
    return dict(row) if row else None


def get_llm_api_key(user_id: int, provider: str) -> str | None:
    from src.secrets import decrypt_secret

    row = get_llm_credential(user_id, provider)
    if not row:
        return None
    try:
        return decrypt_secret(row["api_key_ciphertext"])
    except Exception:
        return None


def delete_llm_credential(user_id: int, provider: str) -> None:
    with connect() as conn:
        conn.execute(
            "DELETE FROM user_llm_credentials WHERE user_id=? AND provider=?",
            (user_id, provider),
        )


# --- IMAP credentials ---


def save_imap_credential(user_id: int, password: str) -> None:
    from src.secrets import encrypt_secret, mask_secret

    now = datetime.utcnow().isoformat()
    ciphertext = encrypt_secret(password)
    hint = mask_secret(password)
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO user_imap_credentials (user_id, password_ciphertext, key_hint, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                password_ciphertext=excluded.password_ciphertext,
                key_hint=excluded.key_hint,
                updated_at=excluded.updated_at
            """,
            (user_id, ciphertext, hint, now),
        )


def get_imap_credential_meta(user_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT key_hint, updated_at FROM user_imap_credentials WHERE user_id=?",
            (user_id,),
        ).fetchone()
    return dict(row) if row else None


def get_imap_password(user_id: int | None = None) -> str | None:
    from src.secrets import decrypt_secret

    uid = _uid(user_id)
    with connect() as conn:
        row = conn.execute(
            "SELECT password_ciphertext FROM user_imap_credentials WHERE user_id=?",
            (uid,),
        ).fetchone()
    if not row:
        return None
    try:
        return decrypt_secret(row["password_ciphertext"])
    except Exception:
        return None


def delete_imap_credential(user_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM user_imap_credentials WHERE user_id=?", (user_id,))


def list_llm_credentials_meta(user_id: int) -> dict[str, dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT provider, key_hint, updated_at FROM user_llm_credentials WHERE user_id=?",
            (user_id,),
        ).fetchall()
    return {
        row["provider"]: {"key_hint": row["key_hint"], "has_key": True, "updated_at": row["updated_at"]}
        for row in rows
    }


def link_oauth_account(user_id: int, provider: str, provider_user_id: str) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO oauth_accounts (user_id, provider, provider_user_id)
            VALUES (?, ?, ?)
            ON CONFLICT(provider, provider_user_id) DO UPDATE SET user_id=excluded.user_id
            """,
            (user_id, provider, provider_user_id),
        )


def get_oauth_account(provider: str, provider_user_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT o.*, u.email, u.display_name
            FROM oauth_accounts o
            JOIN users u ON u.id = o.user_id
            WHERE o.provider = ? AND o.provider_user_id = ?
            """,
            (provider, provider_user_id),
        ).fetchone()
    return dict(row) if row else None


def list_oauth_accounts(user_id: int) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT provider, provider_user_id FROM oauth_accounts WHERE user_id = ?",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def unlink_oauth_account(user_id: int, provider: str) -> None:
    with connect() as conn:
        conn.execute(
            "DELETE FROM oauth_accounts WHERE user_id = ? AND provider = ?",
            (user_id, provider),
        )


def user_owns_job(job_id: int, user_id: int | None = None) -> bool:
    uid = _uid(user_id)
    with connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM user_jobs WHERE id = ? AND user_id = ?",
            (job_id, uid),
        ).fetchone()
    return row is not None


# --- Jobs (user_jobs + catalog_jobs join) ---


def _job_row_to_dict(row: Any) -> dict[str, Any]:
    from src.catalog_db import _row_to_job

    return _row_to_job(row)


def get_existing_url_hashes(user_id: int | None = None) -> set[str]:
    """Return catalog url_hashes already synced for this user."""
    uid = _uid(user_id)
    from src.catalog_db import JOB_JOIN_FROM

    with connect() as conn:
        rows = conn.execute(
            f"SELECT cj.url_hash {JOB_JOIN_FROM} WHERE uj.user_id = ?",
            (uid,),
        ).fetchall()
    return {row["url_hash"] for row in rows}


def upsert_job(job: dict[str, Any], source_id: str = "", user_id: int | None = None) -> int:
    """Platform crawl: upsert shared catalog. With user_id, also link user_jobs row."""
    from src.catalog_db import ensure_user_job_for_catalog, upsert_catalog_job
    from src.crawler.ats_detect import detect_ats_type

    ats = detect_ats_type(job.get("url", ""))
    catalog_id = upsert_catalog_job(job, source_id=source_id, ats_type=ats)
    if catalog_id <= 0:
        return catalog_id
    if user_id is not None:
        uid = _uid(user_id)
        return ensure_user_job_for_catalog(catalog_id, uid)
    return catalog_id


def mark_stale_jobs(days: int = 30, user_id: int | None = None) -> int:
    from src.catalog_db import mark_catalog_stale

    return mark_catalog_stale(days)


def mark_stale(days: int = 30, user_id: int | None = None) -> int:
    return mark_stale_jobs(days, user_id)


def get_jobs(
    status: str | None = None,
    limit: int = 200,
    order_by: str = "COALESCE(uj.match_score, -1) DESC, cj.last_seen DESC",
    user_id: int | None = None,
) -> list[dict]:
    from src.catalog_db import JOB_JOIN_FROM, JOB_JOIN_SELECT

    uid = _uid(user_id)
    with connect() as conn:
        if status:
            rows = conn.execute(
                f"{JOB_JOIN_SELECT} {JOB_JOIN_FROM} "
                f"WHERE uj.user_id = ? AND uj.status = ? ORDER BY {order_by} LIMIT ?",
                (uid, status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                f"{JOB_JOIN_SELECT} {JOB_JOIN_FROM} "
                f"WHERE uj.user_id = ? ORDER BY {order_by} LIMIT ?",
                (uid, limit),
            ).fetchall()
    return [_job_row_to_dict(r) for r in rows]


def get_jobs_by_status(
    status: str | list[str],
    limit: int = 500,
    user_id: int | None = None,
    *,
    vector_llm_min: float | None = None,
    prefilter_enabled: bool = False,
    require_description: bool = False,
) -> list[dict]:
    from src.catalog_db import JOB_JOIN_FROM, JOB_JOIN_SELECT

    uid = _uid(user_id)
    if isinstance(status, str):
        status = [status]
    placeholders = ",".join("?" * len(status))
    extra = ""
    params: list[Any] = [uid, *status]
    if prefilter_enabled and vector_llm_min is not None and "new" in status:
        extra = " AND (uj.vector_score IS NULL OR uj.vector_score >= ?)"
        params.append(vector_llm_min)
    if require_description:
        # Keep the batch limit meaningful: filter undescribed jobs in SQL
        # rather than fetching them and discarding them in Python.
        extra += (
            " AND ((cj.description_full IS NOT NULL AND cj.description_full != '')"
            " OR (cj.description_short IS NOT NULL AND cj.description_short != ''))"
        )
    params.append(limit)
    with connect() as conn:
        rows = conn.execute(
            f"""
            {JOB_JOIN_SELECT} {JOB_JOIN_FROM}
            WHERE uj.user_id = ? AND uj.status IN ({placeholders}){extra}
            ORDER BY COALESCE(uj.match_score, uj.vector_score, -1) DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [_job_row_to_dict(r) for r in rows]


def get_jobs_by_statuses(
    statuses: list[str],
    limit: int = 500,
    user_id: int | None = None,
) -> list[dict]:
    return get_jobs_by_status(statuses, limit, user_id)


def get_job(job_id: int, user_id: int | None = None) -> dict | None:
    from src.catalog_db import JOB_JOIN_FROM, JOB_JOIN_SELECT

    uid = _uid(user_id)
    with connect() as conn:
        row = conn.execute(
            f"{JOB_JOIN_SELECT} {JOB_JOIN_FROM} WHERE uj.id = ? AND uj.user_id = ?",
            (job_id, uid),
        ).fetchone()
    return _job_row_to_dict(row) if row else None


def update_job(job_id: int, user_id: int | None = None, **fields: Any) -> None:
    from src.catalog_db import CATALOG_FIELDS, USER_JOB_FIELDS

    uid = _uid(user_id)
    if not fields:
        return
    catalog_updates = {k: v for k, v in fields.items() if k in CATALOG_FIELDS}
    user_updates = {k: v for k, v in fields.items() if k in USER_JOB_FIELDS}
    with connect() as conn:
        if user_updates:
            cols = ", ".join(f"{k}=?" for k in user_updates)
            conn.execute(
                f"UPDATE user_jobs SET {cols} WHERE id=? AND user_id=?",
                (*user_updates.values(), job_id, uid),
            )
        if catalog_updates:
            row = conn.execute(
                "SELECT catalog_job_id FROM user_jobs WHERE id=? AND user_id=?",
                (job_id, uid),
            ).fetchone()
            if row:
                cols = ", ".join(f"{k}=?" for k in catalog_updates)
                conn.execute(
                    f"UPDATE catalog_jobs SET {cols} WHERE id=?",
                    (*catalog_updates.values(), row["catalog_job_id"]),
                )


def set_job_status(job_id: int, status: str, user_id: int | None = None) -> None:
    old = get_job(job_id, user_id)
    fields: dict[str, Any] = {"status": status}
    if status == "applied":
        fields["applied_at"] = datetime.utcnow().isoformat()
    update_job(job_id, user_id, **fields)
    if old and old.get("status") != status:
        log_application_event(
            job_id,
            "status_change",
            {"from": old.get("status"), "to": status},
            user_id=user_id,
        )


def get_jobs_needing_enrichment(limit: int = 100, user_id: int | None = None) -> list[dict]:
    from src.catalog_db import get_catalog_jobs_needing_enrichment

    return get_catalog_jobs_needing_enrichment(limit=limit)


def list_all_jobs(limit: int = 200, user_id: int | None = None) -> list[dict]:
    return get_jobs(limit=limit, user_id=user_id)


# --- Profile ---


def save_profile(
    resume_json: dict,
    source_pdf: str,
    github_json: dict | None = None,
    evaluation_json: dict | None = None,
    user_id: int | None = None,
) -> None:
    uid = _uid(user_id)
    now = datetime.utcnow().isoformat()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO profile (user_id, resume_json, source_pdf_path, github_json, evaluation_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                resume_json=excluded.resume_json,
                source_pdf_path=excluded.source_pdf_path,
                github_json=excluded.github_json,
                evaluation_json=excluded.evaluation_json,
                updated_at=excluded.updated_at
            """,
            (
                uid,
                json.dumps(resume_json, ensure_ascii=False),
                source_pdf,
                json.dumps(github_json or {}, ensure_ascii=False),
                json.dumps(evaluation_json or {}, ensure_ascii=False) if evaluation_json else None,
                now,
            ),
        )


def get_profile(user_id: int | None = None) -> dict | None:
    uid = _uid(user_id)
    with connect() as conn:
        row = conn.execute("SELECT * FROM profile WHERE user_id = ?", (uid,)).fetchone()
    if not row:
        return None
    keys = row.keys()
    eval_raw = row["evaluation_json"] if "evaluation_json" in keys else None
    return {
        "resume_json": json.loads(row["resume_json"] or "{}"),
        "source_pdf_path": row["source_pdf_path"],
        "github_json": json.loads(row["github_json"] or "{}"),
        "evaluation_json": json.loads(eval_raw or "{}") if eval_raw else None,
        "updated_at": row["updated_at"],
    }


# --- Applications ---


def save_application(
    job_id: int,
    tailored_cv_path: str = "",
    cover_letter_path: str = "",
    cover_letter_text: str = "",
    tailored_resume_json: dict | None = None,
    apply_method: str = "",
    apply_result: str = "",
    cv_template_id: int | None = None,
    user_id: int | None = None,
) -> None:
    if user_id is not None and not user_owns_job(job_id, user_id):
        raise PermissionError(f"Job {job_id} not owned by user {user_id}")
    now = datetime.utcnow().isoformat()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO applications (job_id, tailored_cv_path, cover_letter_path,
                cover_letter_text, tailored_resume_json, apply_method, apply_result,
                cv_template_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                tailored_cv_path=COALESCE(NULLIF(excluded.tailored_cv_path,''), tailored_cv_path),
                cover_letter_path=COALESCE(NULLIF(excluded.cover_letter_path,''), cover_letter_path),
                cover_letter_text=COALESCE(NULLIF(excluded.cover_letter_text,''), cover_letter_text),
                tailored_resume_json=COALESCE(NULLIF(excluded.tailored_resume_json,''), tailored_resume_json),
                apply_method=COALESCE(NULLIF(excluded.apply_method,''), apply_method),
                apply_result=COALESCE(NULLIF(excluded.apply_result,''), apply_result),
                cv_template_id=COALESCE(excluded.cv_template_id, cv_template_id),
                version=COALESCE(version, 1) + CASE
                    WHEN NULLIF(excluded.tailored_cv_path,'') IS NOT NULL
                      OR NULLIF(excluded.cover_letter_path,'') IS NOT NULL THEN 1 ELSE 0 END,
                updated_at=excluded.updated_at
            """,
            (
                job_id,
                tailored_cv_path,
                cover_letter_path,
                cover_letter_text,
                json.dumps(tailored_resume_json or {}, ensure_ascii=False),
                apply_method,
                apply_result,
                cv_template_id,
                now,
                now,
            ),
        )


def set_application_template(job_id: int, cv_template_id: int | None, user_id: int | None = None) -> None:
    if user_id is not None and not user_owns_job(job_id, user_id):
        raise PermissionError(f"Job {job_id} not owned by user {user_id}")
    now = datetime.utcnow().isoformat()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO applications (job_id, cv_template_id, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                cv_template_id = excluded.cv_template_id,
                updated_at = excluded.updated_at
            """,
            (job_id, cv_template_id, now, now),
        )


def get_application(job_id: int, user_id: int | None = None) -> dict | None:
    if user_id is not None and not user_owns_job(job_id, user_id):
        return None
    with connect() as conn:
        row = conn.execute("SELECT * FROM applications WHERE job_id = ?", (job_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["tailored_resume_json"] = json.loads(d.get("tailored_resume_json") or "{}")
    return d


def count_applications_today(user_id: int | None = None) -> int:
    uid = _uid(user_id)
    today = datetime.utcnow().strftime("%Y-%m-%d")
    with connect() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) as c FROM user_jobs
            WHERE user_id = ? AND status = 'applied' AND applied_at LIKE ?
            """,
            (uid, f"{today}%"),
        ).fetchone()
    return row["c"] if row else 0


def log_application_event(
    job_id: int,
    event_type: str,
    detail: dict[str, Any] | None = None,
    user_id: int | None = None,
) -> None:
    if user_id is not None and not user_owns_job(job_id, user_id):
        raise PermissionError(f"Job {job_id} not owned by user {user_id}")
    now = datetime.utcnow().isoformat()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO application_events (job_id, event_type, detail_json, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (job_id, event_type, json.dumps(detail or {}, ensure_ascii=False), now),
        )


def get_application_events(job_id: int, limit: int = 50, user_id: int | None = None) -> list[dict]:
    if user_id is not None and not user_owns_job(job_id, user_id):
        return []
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM application_events
            WHERE job_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (job_id, limit),
        ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d["detail"] = json.loads(d.pop("detail_json") or "{}")
        result.append(d)
    return result


def get_last_application_event(job_id: int, user_id: int | None = None) -> dict | None:
    events = get_application_events(job_id, limit=1, user_id=user_id)
    return events[0] if events else None


# --- Pipeline runs ---


def save_pipeline_run(
    run_id: str,
    stages: list[str],
    summary: dict[str, Any],
    log_tail: list[str],
    error: str | None,
    started_at: str,
    finished_at: str,
    user_id: int | None = None,
) -> None:
    uid = _uid(user_id)
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO pipeline_runs (id, user_id, stages_json, summary_json, log_tail, error, started_at, finished_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                stages_json=excluded.stages_json,
                summary_json=excluded.summary_json,
                log_tail=excluded.log_tail,
                error=excluded.error,
                finished_at=excluded.finished_at
            """,
            (
                run_id,
                uid,
                json.dumps(stages),
                json.dumps(summary),
                "\n".join(log_tail[-100:]),
                error,
                started_at,
                finished_at,
            ),
        )


def get_last_pipeline_run(user_id: int | None = None) -> dict | None:
    uid = _uid(user_id)
    with connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM pipeline_runs
            WHERE user_id = ?
            ORDER BY finished_at DESC
            LIMIT 1
            """,
            (uid,),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["stages"] = json.loads(d.pop("stages_json") or "[]")
    d["summary"] = json.loads(d.pop("summary_json") or "{}")
    d["log"] = (d.pop("log_tail") or "").splitlines()
    return d


# --- Source registry ---


def upsert_source_registry(
    source: dict,
    status: str = "ok",
    error: str = "",
    user_id: int | None = None,
) -> None:
    uid = _uid(user_id)
    now = datetime.utcnow().isoformat()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO source_registry (user_id, id, name, adapter, config_json, enabled,
                discovered_at, last_run_at, last_status, last_error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, id) DO UPDATE SET
                name=excluded.name,
                adapter=excluded.adapter,
                config_json=excluded.config_json,
                enabled=excluded.enabled,
                last_run_at=excluded.last_run_at,
                last_status=excluded.last_status,
                last_error=excluded.last_error
            """,
            (
                uid,
                source.get("id", ""),
                source.get("name", ""),
                source.get("adapter", ""),
                json.dumps(source, ensure_ascii=False),
                1 if source.get("enabled", True) else 0,
                source.get("discovered_at", now),
                now,
                status,
                error,
            ),
        )


def count_jobs_by_status(user_id: int | None = None) -> dict[str, int]:
    uid = _uid(user_id)
    with connect() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) as c FROM user_jobs WHERE user_id = ? GROUP BY status",
            (uid,),
        ).fetchall()
    return {row["status"]: row["c"] for row in rows}


def clear_user_jobs(user_id: int | None = None) -> dict[str, int]:
    """Delete a user's job queue (and its applications/events), keeping their
    account, profile, CV templates, sources, and settings untouched. Does not
    touch the shared catalog_jobs table or any other user's data."""
    uid = _uid(user_id)
    with connect() as conn:
        events = conn.execute(
            "DELETE FROM application_events WHERE job_id IN (SELECT id FROM user_jobs WHERE user_id = ?)",
            (uid,),
        ).rowcount
        applications = conn.execute(
            "DELETE FROM applications WHERE job_id IN (SELECT id FROM user_jobs WHERE user_id = ?)",
            (uid,),
        ).rowcount
        jobs = conn.execute("DELETE FROM user_jobs WHERE user_id = ?", (uid,)).rowcount
    return {"jobs": jobs, "applications": applications, "events": events}


def list_applications(
    status: str | None = None,
    source: str | None = None,
    min_score: float | None = None,
    tab: str | None = None,
    limit: int = 200,
    offset: int = 0,
    user_id: int | None = None,
) -> list[dict]:
    uid = _uid(user_id)
    clauses: list[str] = ["uj.user_id = ?"]
    params: list[Any] = [uid]
    if status:
        clauses.append("uj.status = ?")
        params.append(status)
    elif tab == "history":
        clauses.append("uj.status IN ('applied', 'rejected', 'failed')")
    elif tab == "active":
        clauses.append("uj.status NOT IN ('applied', 'rejected', 'failed', 'stale')")
    if source:
        clauses.append("cj.source = ?")
        params.append(source)
    if min_score is not None:
        clauses.append("uj.match_score >= ?")
        params.append(min_score)
    where = f"WHERE {' AND '.join(clauses)}"
    params.extend([limit, offset])
    from src.catalog_db import JOB_JOIN_FROM, JOB_JOIN_SELECT

    with connect() as conn:
        rows = conn.execute(
            f"""
            {JOB_JOIN_SELECT},
                   a.apply_method, a.apply_result, a.updated_at AS app_updated_at,
                   a.tailored_cv_path, a.cover_letter_path,
                   (
                     SELECT ae.event_type FROM application_events ae
                     WHERE ae.job_id = uj.id
                     ORDER BY ae.created_at DESC LIMIT 1
                   ) AS last_event_type,
                   (
                     SELECT ae.created_at FROM application_events ae
                     WHERE ae.job_id = uj.id
                     ORDER BY ae.created_at DESC LIMIT 1
                   ) AS last_event_at
            {JOB_JOIN_FROM}
            LEFT JOIN applications a ON a.job_id = uj.id
            {where}
            ORDER BY COALESCE(uj.applied_at, cj.last_seen) DESC, COALESCE(uj.match_score, -1) DESC
            LIMIT ? OFFSET ?
            """,
            params,
        ).fetchall()
    return [_job_row_to_dict(r) for r in rows]


def get_pipeline_readiness(user_id: int | None = None) -> dict[str, Any]:
    profile = get_profile(user_id)
    status_counts = count_jobs_by_status(user_id)
    enrich_needed = len(get_jobs_needing_enrichment(limit=10000, user_id=user_id))
    evaluation = (profile or {}).get("evaluation_json")
    return {
        "profile_loaded": profile is not None,
        "profile_updated_at": profile.get("updated_at") if profile else None,
        "profile_evaluated": bool(evaluation),
        "jobs_new": status_counts.get("new", 0),
        "jobs_scored": status_counts.get("scored", 0),
        "jobs_queued": status_counts.get("queued", 0),
        "jobs_approved": status_counts.get("approved", 0),
        "jobs_applied": status_counts.get("applied", 0),
        "jobs_needing_enrich": enrich_needed,
        "status_counts": status_counts,
    }
