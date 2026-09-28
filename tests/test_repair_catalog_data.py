"""Tests for scripts/repair_catalog_data.py — the one-off Phase 3 cleanup
for catalog rows corrupted by the pre-fix status-leak bug (see
tests/test_catalog_sync.py::CatalogSyncTests for the bug itself).
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.helpers import TempDBTestCase

import scripts.repair_catalog_data as repair


class RehashCatalogUrlsTests(TempDBTestCase):
    """Regression: url_hash's normalization changed in Phase 2 (a trailing
    slash / tracking params / scheme-host casing no longer distinguish two
    URLs). A row hashed before that change won't match what a fresh crawl
    computes for the exact same URL, so a re-crawl of a posting already in
    the catalog would create a duplicate instead of updating it.
    """

    def _insert_stale_hash(self, *, url, stale_hash, **extra):
        from src.db import connect

        with connect() as conn:
            cols = ["url_hash", "url", "title", "status", "first_seen"] + list(extra.keys())
            vals = [stale_hash, url, "Engineer", "active", "2020-01-01"] + list(extra.values())
            placeholders = ",".join("?" * len(vals))
            return conn.execute(
                f"INSERT INTO catalog_jobs ({','.join(cols)}) VALUES ({placeholders})", vals
            ).lastrowid

    def test_renames_a_stale_hash_to_the_current_normalization(self):
        from src.db import connect, url_hash

        real_hash = url_hash("https://example.com/jobs/1/")  # trailing slash normalizes away
        cat_id = self._insert_stale_hash(
            url="https://example.com/jobs/1/", stale_hash="deliberately-wrong-hash"
        )

        result = repair.rehash_catalog_urls(apply=True)
        self.assertEqual(result, {"renamed": 1, "merged": 0})

        with connect() as conn:
            row = conn.execute("SELECT url_hash FROM catalog_jobs WHERE id = ?", (cat_id,)).fetchone()
        self.assertEqual(row["url_hash"], real_hash)

    def test_dry_run_does_not_write(self):
        cat_id = self._insert_stale_hash(url="https://example.com/jobs/1/", stale_hash="wrong")

        repair.rehash_catalog_urls(apply=False)

        from src.db import connect

        with connect() as conn:
            row = conn.execute("SELECT url_hash FROM catalog_jobs WHERE id = ?", (cat_id,)).fetchone()
        self.assertEqual(row["url_hash"], "wrong")

    def test_leaves_an_already_correct_hash_alone(self):
        from src.db import connect, url_hash

        url = "https://example.com/jobs/1"
        correct = url_hash(url)
        cat_id = self._insert_stale_hash(url=url, stale_hash=correct)

        result = repair.rehash_catalog_urls(apply=True)
        self.assertEqual(result, {"renamed": 0, "merged": 0})

        with connect() as conn:
            row = conn.execute("SELECT url_hash FROM catalog_jobs WHERE id = ?", (cat_id,)).fetchone()
        self.assertEqual(row["url_hash"], correct)

    def test_merges_two_rows_that_now_normalize_to_the_same_url(self):
        from src.db import connect

        # Two raw URLs that differ only by a now-stripped param — under the
        # old normalization these hashed differently and became two rows.
        winner_id = self._insert_stale_hash(
            url="https://example.com/jobs/1",
            stale_hash="stale-a",
            description_full="Full description",
        )
        loser_id = self._insert_stale_hash(
            url="https://example.com/jobs/1?utm_source=twitter",
            stale_hash="stale-b",
        )

        result = repair.rehash_catalog_urls(apply=True)
        self.assertEqual(result["merged"], 1)

        with connect() as conn:
            remaining = conn.execute("SELECT id FROM catalog_jobs").fetchall()
        self.assertEqual({r["id"] for r in remaining}, {winner_id})

    def test_merge_prefers_the_row_with_a_description(self):
        from src.db import connect

        no_desc_id = self._insert_stale_hash(url="https://example.com/jobs/1", stale_hash="stale-a")
        has_desc_id = self._insert_stale_hash(
            url="https://example.com/jobs/1/",
            stale_hash="stale-b",
            description_full="Full description",
        )

        repair.rehash_catalog_urls(apply=True)

        with connect() as conn:
            remaining = conn.execute("SELECT id FROM catalog_jobs").fetchall()
        self.assertEqual({r["id"] for r in remaining}, {has_desc_id})

    def test_merge_reassigns_a_losers_user_jobs_row_to_the_winner(self):
        from src.db import connect, create_user

        uid = create_user("merge@test.com", display_name="M")["id"]
        winner_id = self._insert_stale_hash(
            url="https://example.com/jobs/1", stale_hash="stale-a", description_full="desc"
        )
        loser_id = self._insert_stale_hash(url="https://example.com/jobs/1/", stale_hash="stale-b")
        with connect() as conn:
            uj_id = conn.execute(
                "INSERT INTO user_jobs (user_id, catalog_job_id, status) VALUES (?, ?, 'scored')",
                (uid, loser_id),
            ).lastrowid

        repair.rehash_catalog_urls(apply=True)

        with connect() as conn:
            row = conn.execute("SELECT catalog_job_id, status FROM user_jobs WHERE id = ?", (uj_id,)).fetchone()
        self.assertEqual(row["catalog_job_id"], winner_id)
        self.assertEqual(row["status"], "scored", "the user's existing decision must survive the merge")

    def test_merge_drops_a_losers_user_jobs_row_if_the_user_already_has_the_winner(self):
        """A user can't have two user_jobs rows for the same catalog_job_id
        (UNIQUE(user_id, catalog_job_id)) — when both the winner and loser
        already have a row for the same user, the loser's must be dropped,
        not silently violate the constraint."""
        from src.db import connect, create_user

        uid = create_user("dupuser@test.com", display_name="D")["id"]
        winner_id = self._insert_stale_hash(
            url="https://example.com/jobs/1", stale_hash="stale-a", description_full="desc"
        )
        loser_id = self._insert_stale_hash(url="https://example.com/jobs/1/", stale_hash="stale-b")
        with connect() as conn:
            conn.execute(
                "INSERT INTO user_jobs (user_id, catalog_job_id, status) VALUES (?, ?, 'applied')",
                (uid, winner_id),
            )
            loser_uj_id = conn.execute(
                "INSERT INTO user_jobs (user_id, catalog_job_id, status) VALUES (?, ?, 'new')",
                (uid, loser_id),
            ).lastrowid

        repair.rehash_catalog_urls(apply=True)

        with connect() as conn:
            surviving = conn.execute(
                "SELECT status FROM user_jobs WHERE user_id = ?", (uid,)
            ).fetchall()
            dropped = conn.execute("SELECT 1 FROM user_jobs WHERE id = ?", (loser_uj_id,)).fetchone()
        self.assertEqual(len(surviving), 1)
        self.assertEqual(surviving[0]["status"], "applied", "the real decision must survive, not the blank 'new' one")
        self.assertIsNone(dropped)


class BackfillFingerprintsTests(TempDBTestCase):
    def test_computes_fingerprint_for_rows_missing_one(self):
        from src.db import connect

        with connect() as conn:
            conn.execute(
                "INSERT INTO catalog_jobs (url_hash, title, company, location, status, fingerprint) "
                "VALUES ('h1', 'Engineer', 'Acme', 'Remote', 'active', NULL)"
            )

        count = repair.backfill_fingerprints(apply=True)
        self.assertEqual(count, 1)

        from src.catalog_db import compute_fingerprint

        with connect() as conn:
            fp = conn.execute("SELECT fingerprint FROM catalog_jobs WHERE url_hash='h1'").fetchone()["fingerprint"]
        self.assertEqual(fp, compute_fingerprint("Engineer", "Acme", "Remote"))

    def test_dry_run_does_not_write(self):
        from src.db import connect

        with connect() as conn:
            conn.execute(
                "INSERT INTO catalog_jobs (url_hash, title, company, location, status, fingerprint) "
                "VALUES ('h1', 'Engineer', 'Acme', 'Remote', 'active', NULL)"
            )

        repair.backfill_fingerprints(apply=False)

        with connect() as conn:
            fp = conn.execute("SELECT fingerprint FROM catalog_jobs WHERE url_hash='h1'").fetchone()["fingerprint"]
        self.assertIsNone(fp)

    def test_leaves_existing_fingerprints_alone(self):
        from src.db import connect

        with connect() as conn:
            conn.execute(
                "INSERT INTO catalog_jobs (url_hash, title, company, location, status, fingerprint) "
                "VALUES ('h1', 'Engineer', 'Acme', 'Remote', 'active', 'already-set')"
            )

        count = repair.backfill_fingerprints(apply=True)
        self.assertEqual(count, 0)

        with connect() as conn:
            fp = conn.execute("SELECT fingerprint FROM catalog_jobs WHERE url_hash='h1'").fetchone()["fingerprint"]
        self.assertEqual(fp, "already-set")


class RepairCatalogStatusTests(TempDBTestCase):
    def _insert_catalog_job(self, url_hash: str, status: str, last_seen: str, fingerprint: str = "") -> int:
        from src.db import connect

        with connect() as conn:
            cur = conn.execute(
                "INSERT INTO catalog_jobs (url_hash, title, status, last_seen, fingerprint) "
                "VALUES (?, 'Engineer', ?, ?, ?)",
                (url_hash, status, last_seen, fingerprint),
            )
            return cur.lastrowid

    def test_dry_run_reports_but_does_not_write(self):
        recent = datetime.utcnow().isoformat()
        self._insert_catalog_job("h1", "skipped", recent)

        result = repair.repair_catalog_status(apply=False, stale_days=30)
        self.assertEqual(result["active"], 1)

        from src.db import connect

        with connect() as conn:
            status = conn.execute("SELECT status FROM catalog_jobs WHERE url_hash='h1'").fetchone()["status"]
        self.assertEqual(status, "skipped", "dry run must not write")

    def test_apply_resets_recent_corrupted_row_to_active(self):
        recent = datetime.utcnow().isoformat()
        self._insert_catalog_job("h1", "scored", recent)

        repair.repair_catalog_status(apply=True, stale_days=30)

        from src.db import connect

        with connect() as conn:
            status = conn.execute("SELECT status FROM catalog_jobs WHERE url_hash='h1'").fetchone()["status"]
        self.assertEqual(status, "active")

    def test_apply_resets_old_corrupted_row_to_stale_not_active(self):
        old = (datetime.utcnow() - timedelta(days=90)).isoformat()
        self._insert_catalog_job("h1", "rejected", old)

        repair.repair_catalog_status(apply=True, stale_days=30)

        from src.db import connect

        with connect() as conn:
            status = conn.execute("SELECT status FROM catalog_jobs WHERE url_hash='h1'").fetchone()["status"]
        self.assertEqual(status, "stale")

    def test_leaves_already_valid_statuses_alone(self):
        recent = datetime.utcnow().isoformat()
        self._insert_catalog_job("h1", "active", recent)
        self._insert_catalog_job("h2", "stale", recent)

        result = repair.repair_catalog_status(apply=True, stale_days=30)
        self.assertEqual(result, {"active": 0, "stale": 0})


class TagLegacyClassificationsTests(TempDBTestCase):
    def test_tags_resolved_rows_with_no_classified_by(self):
        from src.db import connect, create_user

        uid = create_user("legacy@test.com", display_name="Legacy")["id"]
        with connect() as conn:
            cat_id = conn.execute(
                "INSERT INTO catalog_jobs (url_hash, title, status) VALUES ('h1', 'Engineer', 'active')"
            ).lastrowid
            conn.execute(
                "INSERT INTO user_jobs (user_id, catalog_job_id, status, classified_by) "
                "VALUES (?, ?, 'rejected', NULL)",
                (uid, cat_id),
            )

        count = repair.tag_legacy_classifications(apply=True)
        self.assertEqual(count, 1)

        with connect() as conn:
            classified_by = conn.execute(
                "SELECT classified_by FROM user_jobs WHERE user_id = ?", (uid,)
            ).fetchone()["classified_by"]
        self.assertEqual(classified_by, "legacy_unknown")

    def test_does_not_tag_new_jobs(self):
        from src.db import connect, create_user

        uid = create_user("newjob@test.com", display_name="New")["id"]
        with connect() as conn:
            cat_id = conn.execute(
                "INSERT INTO catalog_jobs (url_hash, title, status) VALUES ('h1', 'Engineer', 'active')"
            ).lastrowid
            conn.execute(
                "INSERT INTO user_jobs (user_id, catalog_job_id, status, classified_by) "
                "VALUES (?, ?, 'new', NULL)",
                (uid, cat_id),
            )

        count = repair.tag_legacy_classifications(apply=True)
        self.assertEqual(count, 0)


class InheritDuplicateDecisionsTests(TempDBTestCase):
    def _setup_user_with_resolved_and_duplicate(self, resolved_status: str):
        from src.db import connect, create_user

        uid = create_user(f"{resolved_status}@test.com", display_name=resolved_status)["id"]
        with connect() as conn:
            resolved_cat = conn.execute(
                "INSERT INTO catalog_jobs (url_hash, title, company, location, status, fingerprint) "
                "VALUES ('resolved', 'Engineer', 'Acme', 'Remote', 'active', 'fp1')"
            ).lastrowid
            conn.execute(
                "INSERT INTO user_jobs (user_id, catalog_job_id, status, classified_by) VALUES (?, ?, ?, 'llm')",
                (uid, resolved_cat, resolved_status),
            )
            dup_cat = conn.execute(
                "INSERT INTO catalog_jobs (url_hash, title, company, location, status, fingerprint) "
                "VALUES ('dup', 'Engineer', 'Acme', 'Remote', 'active', 'fp1')"
            ).lastrowid
            dup_uj = conn.execute(
                "INSERT INTO user_jobs (user_id, catalog_job_id, status, classified_by) VALUES (?, ?, 'new', NULL)",
                (uid, dup_cat),
            ).lastrowid
        return uid, dup_uj

    def test_inherits_rejected_decision_onto_new_duplicate(self):
        uid, dup_uj = self._setup_user_with_resolved_and_duplicate("rejected")

        count = repair.inherit_duplicate_decisions(apply=True)
        self.assertEqual(count, 1)

        from src.db import connect

        with connect() as conn:
            row = conn.execute(
                "SELECT status, classified_by FROM user_jobs WHERE id = ?", (dup_uj,)
            ).fetchone()
        self.assertEqual(row["status"], "rejected")
        self.assertEqual(row["classified_by"], "duplicate_of_resolved")

    def test_inherits_skipped_decision_onto_new_duplicate(self):
        uid, dup_uj = self._setup_user_with_resolved_and_duplicate("skipped")

        repair.inherit_duplicate_decisions(apply=True)

        from src.db import connect

        with connect() as conn:
            status = conn.execute("SELECT status FROM user_jobs WHERE id = ?", (dup_uj,)).fetchone()["status"]
        self.assertEqual(status, "skipped")

    def test_never_inherits_applied_onto_a_new_duplicate(self):
        """A duplicate posting must never be silently marked 'applied' —
        the person did not actually apply to THIS requisition, and if it's
        genuinely still open, hiding it behind a false 'applied' status
        could cost them a real application."""
        uid, dup_uj = self._setup_user_with_resolved_and_duplicate("applied")

        count = repair.inherit_duplicate_decisions(apply=True)
        self.assertEqual(count, 0)

        from src.db import connect

        with connect() as conn:
            row = conn.execute(
                "SELECT status, classified_by FROM user_jobs WHERE id = ?", (dup_uj,)
            ).fetchone()
        self.assertEqual(row["status"], "new")
        self.assertIsNone(row["classified_by"])

    def test_dry_run_reports_but_does_not_write(self):
        uid, dup_uj = self._setup_user_with_resolved_and_duplicate("rejected")

        count = repair.inherit_duplicate_decisions(apply=False)
        self.assertEqual(count, 1)

        from src.db import connect

        with connect() as conn:
            status = conn.execute("SELECT status FROM user_jobs WHERE id = ?", (dup_uj,)).fetchone()["status"]
        self.assertEqual(status, "new", "dry run must not write")

    def test_does_not_inherit_across_different_users(self):
        """A resolved decision from one user must never leak into another
        user's queue, even for an identical fingerprint."""
        from src.db import connect, create_user

        u1, _ = self._setup_user_with_resolved_and_duplicate("rejected")
        u2 = create_user("other@test.com", display_name="Other")["id"]
        with connect() as conn:
            cat_id = conn.execute("SELECT id FROM catalog_jobs WHERE url_hash = 'dup'").fetchone()["id"]
            other_uj = conn.execute(
                "INSERT INTO user_jobs (user_id, catalog_job_id, status, classified_by) VALUES (?, ?, 'new', NULL)",
                (u2, cat_id),
            ).lastrowid

        repair.inherit_duplicate_decisions(apply=True)

        with connect() as conn:
            status = conn.execute("SELECT status FROM user_jobs WHERE id = ?", (other_uj,)).fetchone()["status"]
        self.assertEqual(status, "new")


if __name__ == "__main__":
    import unittest

    unittest.main()
