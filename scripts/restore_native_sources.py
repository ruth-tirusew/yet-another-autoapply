#!/usr/bin/env python3
"""One-off repair: restore the native-adapter sources lost to the yaml_import bug.

``src/yaml_import.py`` used to rename ``config/sources.yaml`` to
``sources.yaml.imported`` right after its one-time import and never looked
at the backup again. Any later reset of ``data/jobs.db`` (a fresh clone, a
wiped DB) found no live ``sources.yaml`` to import from and silently fell
back to seeding just the single Job Board Aggregator default — permanently
losing the 22 native ATS/RSS/JSON sources that had been configured. That
import path is now repaired (``yaml_import._first_existing`` falls back to
the ``.imported`` backup), but the fix only prevents this happening again;
it doesn't retroactively restore a database that already lost its sources,
because ``auto_import_if_empty()`` only runs when the platform's config
collections are empty, and this database's collections (pipeline, matching,
...) are already populated from normal use.

This script re-reads ``config/sources.yaml.imported`` directly and merges
its sources into both the platform source list and every existing user's
list, additively (``save_user_sources_bulk`` is an upsert — it only adds or
updates rows by ``source_id``, never removes one). The aggregator entry in
the backup uses the underscored id ``job_board_aggregator``, which differs
from the currently-seeded hyphenated ``job-board-aggregator`` — restoring
it as-is would add a *second*, duplicate aggregator source and double the
aggregator crawl, so it's skipped; the currently-configured aggregator
source is left untouched.

Safe to run more than once.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config_store import PLATFORM_SOURCES_USER_ID
from src.db import init_db, list_user_ids, list_user_sources, save_user_sources_bulk
from src.yaml_import import _load_yaml

SOURCES_BACKUP = ROOT / "config" / "sources.yaml.imported"
SKIP_ADAPTERS = {"job_board_aggregator"}  # already present under a different id


def main() -> None:
    init_db()

    raw = _load_yaml(SOURCES_BACKUP)
    all_sources = list(raw.get("sources", []))
    if not all_sources:
        print(f"No sources found in {SOURCES_BACKUP} — nothing to restore.")
        return

    restorable = [s for s in all_sources if s.get("adapter") not in SKIP_ADAPTERS]
    skipped = [s.get("id") for s in all_sources if s.get("adapter") in SKIP_ADAPTERS]
    if skipped:
        print(f"Skipping already-present aggregator source(s): {skipped}")

    before = {s["id"] for s in list_user_sources(PLATFORM_SOURCES_USER_ID)}
    save_user_sources_bulk(PLATFORM_SOURCES_USER_ID, restorable)
    after = {s["id"] for s in list_user_sources(PLATFORM_SOURCES_USER_ID)}
    print(f"Platform sources: {len(before)} -> {len(after)} (+{len(after - before)} new)")

    for uid in list_user_ids():
        before_u = {s["id"] for s in list_user_sources(uid)}
        save_user_sources_bulk(uid, restorable)
        after_u = {s["id"] for s in list_user_sources(uid)}
        print(f"User {uid} sources: {len(before_u)} -> {len(after_u)} (+{len(after_u - before_u)} new)")

    print("Done. Review Settings → Sources to confirm which native sources should stay enabled.")


if __name__ == "__main__":
    main()
