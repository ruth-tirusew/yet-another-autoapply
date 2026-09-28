"""Tests for src/catalog_sync.py (shared catalog -> per-user job queue sync)."""

from __future__ import annotations

from tests.helpers import TempDBTestCase


class CatalogSyncTests(TempDBTestCase):
    def setUp(self):
        super().setUp()
        from src.db import connect, create_user

        self.user_id = create_user("sync@test.com", display_name="Sync")["id"]
        with connect() as conn:
            conn.execute(
                "INSERT INTO catalog_jobs (url_hash, title, status) VALUES ('a', 'Engineer A', 'active')"
            )
            conn.execute(
                "INSERT INTO catalog_jobs (url_hash, title, status) VALUES ('b', 'Engineer B', 'active')"
            )
            conn.execute(
                "INSERT INTO catalog_jobs (url_hash, title, status) VALUES ('c', 'Engineer C', 'closed')"
            )

    def test_syncs_only_active_jobs(self):
        from src.catalog_sync import sync_user_jobs
        from src.db import connect

        count = sync_user_jobs(user_id=self.user_id)
        self.assertEqual(count, 2)
        with connect() as conn:
            rows = conn.execute(
                "SELECT status FROM user_jobs WHERE user_id = ?", (self.user_id,)
            ).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["status"] == "new" for r in rows))

    def test_second_sync_is_idempotent(self):
        from src.catalog_sync import sync_user_jobs

        first = sync_user_jobs(user_id=self.user_id)
        second = sync_user_jobs(user_id=self.user_id)
        self.assertEqual(first, 2)
        self.assertEqual(second, 0)

    def test_respects_limit(self):
        from src.catalog_sync import sync_user_jobs

        count = sync_user_jobs(user_id=self.user_id, limit=1)
        self.assertEqual(count, 1)

    def test_user_status_update_does_not_leak_into_catalog(self):
        """update_job(status=...) must only ever touch user_jobs.status.

        catalog_jobs.status is the shared crawl lifecycle (active/stale/
        expired); user_jobs.status is one user's per-job workflow state
        (new/scored/queued/skipped/...). Both used to be named "status" and
        update_job() split a fields dict by column-name membership, so a
        per-user status write also matched CATALOG_FIELDS and overwrote the
        shared catalog row — a job another user had marked "skipped" would
        stop being synced to *any* user (it read as non-active), and a
        crawler re-seeing that URL would need to flip it back to "active"
        before it could re-enter anyone's queue, which is why closed jobs
        kept resurfacing as "new" after being resolved once.
        """
        from src.db import connect, update_job

        with connect() as conn:
            catalog_id = conn.execute(
                "SELECT id FROM catalog_jobs WHERE url_hash = 'a'"
            ).fetchone()["id"]
            conn.execute(
                "INSERT INTO user_jobs (user_id, catalog_job_id, status) VALUES (?, ?, 'new')",
                (self.user_id, catalog_id),
            )
            user_job_id = conn.execute(
                "SELECT id FROM user_jobs WHERE user_id = ? AND catalog_job_id = ?",
                (self.user_id, catalog_id),
            ).fetchone()["id"]

        update_job(user_job_id, user_id=self.user_id, status="skipped")

        with connect() as conn:
            catalog_status = conn.execute(
                "SELECT status FROM catalog_jobs WHERE id = ?", (catalog_id,)
            ).fetchone()["status"]
            user_status = conn.execute(
                "SELECT status FROM user_jobs WHERE id = ?", (user_job_id,)
            ).fetchone()["status"]

        self.assertEqual(catalog_status, "active")
        self.assertEqual(user_status, "skipped")


class NativeWinsOverAggregatorTests(TempDBTestCase):
    """A posting fetched directly from a company's own ATS API (Greenhouse,
    Lever, Ashby, Workable, SmartRecruiters — source_tier='native') is more
    trustworthy than the same URL relayed through a third-party aggregator.
    upsert_catalog_job must never let a later aggregator crawl overwrite a
    native row's data, only confirm it's still listed.
    """

    def test_aggregator_upsert_after_native_only_touches_last_seen(self):
        from src.db import connect, upsert_job

        upsert_job(
            {
                "title": "Native Title",
                "company": "Acme",
                "location": "Remote - Native Location",
                "description": "Full native description",
                "url": "https://boards.greenhouse.io/acme/jobs/1",
                "source_tier": "native",
                "company_slug": "acme",
            },
            source_id="greenhouse",
        )
        # A later aggregator crawl sees the *same* URL with thinner/staler data.
        upsert_job(
            {
                "title": "Native Title (stale aggregator copy)",
                "company": "Acme Inc",
                "location": "Worldwide Remote",
                "description": "",
                "url": "https://boards.greenhouse.io/acme/jobs/1",
                "source_tier": "aggregator",
            },
            source_id="job-board-aggregator",
        )

        with connect() as conn:
            row = conn.execute(
                "SELECT title, company, location, description_short, source_tier, company_slug "
                "FROM catalog_jobs WHERE url = 'https://boards.greenhouse.io/acme/jobs/1'"
            ).fetchone()

        self.assertEqual(row["title"], "Native Title")
        self.assertEqual(row["company"], "Acme")
        self.assertEqual(row["location"], "Remote - Native Location")
        self.assertEqual(row["description_short"], "Full native description")
        self.assertEqual(row["source_tier"], "native")
        self.assertEqual(row["company_slug"], "acme")

    def test_native_upsert_after_aggregator_upgrades_and_overwrites(self):
        from src.db import connect, upsert_job

        upsert_job(
            {
                "title": "Thin Aggregator Title",
                "company": "Acme Inc",
                "location": "Worldwide Remote",
                "url": "https://boards.greenhouse.io/acme/jobs/1",
                "source_tier": "aggregator",
            },
            source_id="job-board-aggregator",
        )
        upsert_job(
            {
                "title": "Native Title",
                "company": "Acme",
                "location": "Remote - Native Location",
                "url": "https://boards.greenhouse.io/acme/jobs/1",
                "source_tier": "native",
                "company_slug": "acme",
            },
            source_id="greenhouse",
        )

        with connect() as conn:
            row = conn.execute(
                "SELECT title, source_tier, company_slug FROM catalog_jobs "
                "WHERE url = 'https://boards.greenhouse.io/acme/jobs/1'"
            ).fetchone()

        self.assertEqual(row["title"], "Native Title")
        self.assertEqual(row["source_tier"], "native")
        self.assertEqual(row["company_slug"], "acme")

    def test_two_aggregator_upserts_still_update_normally(self):
        """The native-wins guard must not block ordinary aggregator-to-
        aggregator re-crawls from refreshing their own data."""
        from src.db import connect, upsert_job

        upsert_job(
            {"title": "Old Title", "company": "Acme", "location": "Remote",
             "url": "https://example.com/jobs/1", "source_tier": "aggregator"},
            source_id="agg",
        )
        upsert_job(
            {"title": "Updated Title", "company": "Acme", "location": "Remote",
             "url": "https://example.com/jobs/1", "source_tier": "aggregator"},
            source_id="agg",
        )

        with connect() as conn:
            title = conn.execute(
                "SELECT title FROM catalog_jobs WHERE url = 'https://example.com/jobs/1'"
            ).fetchone()["title"]
        self.assertEqual(title, "Updated Title")

    def test_aggregator_upsert_never_reactivates_an_expired_native_row(self):
        """Regression: the touch-only path used to set status='active'
        unconditionally. A job expire_missing_catalog_jobs confirmed closed
        (via a native re-crawl of the company's own listing) must stay
        'expired' even when a slower-cycling aggregator still lists it —
        the aggregator is not authoritative over a native closure signal."""
        from src.db import connect, upsert_job

        upsert_job(
            {"title": "Native Title", "company": "Acme", "location": "Remote",
             "url": "https://boards.greenhouse.io/acme/jobs/1",
             "source_tier": "native", "company_slug": "acme"},
            source_id="greenhouse",
        )
        with connect() as conn:
            conn.execute(
                "UPDATE catalog_jobs SET status='expired' "
                "WHERE url = 'https://boards.greenhouse.io/acme/jobs/1'"
            )

        upsert_job(
            {"title": "Stale aggregator copy", "company": "Acme", "location": "Remote",
             "url": "https://boards.greenhouse.io/acme/jobs/1", "source_tier": "aggregator"},
            source_id="job-board-aggregator",
        )

        with connect() as conn:
            row = conn.execute(
                "SELECT status, title FROM catalog_jobs WHERE url = 'https://boards.greenhouse.io/acme/jobs/1'"
            ).fetchone()
        self.assertEqual(row["status"], "expired")
        self.assertEqual(row["title"], "Native Title", "native data must still not be overwritten")


class TouchCatalogLastSeenTests(TempDBTestCase):
    """A source's skip_existing keeps an already-known URL out of the batch
    it hands to upsert_job, so that job's last_seen would otherwise freeze
    forever — mark_catalog_stale would then mark it stale purely because
    nothing touched it again, even while the board keeps listing it every
    crawl. touch_catalog_last_seen is the cheap "still listed" bump that
    keeps that from happening without paying for a full row rewrite.
    """

    def test_bumps_last_seen_and_reactivates_without_a_full_upsert(self):
        from datetime import datetime, timedelta

        from src.catalog_db import touch_catalog_last_seen
        from src.db import connect

        old = (datetime.utcnow() - timedelta(days=10)).isoformat()
        with connect() as conn:
            conn.execute(
                "INSERT INTO catalog_jobs (url_hash, title, status, first_seen, last_seen) "
                "VALUES ('h1', 'Engineer', 'stale', ?, ?)",
                (old, old),
            )

        updated = touch_catalog_last_seen(["h1"])
        self.assertEqual(updated, 1)

        with connect() as conn:
            row = conn.execute(
                "SELECT status, last_seen FROM catalog_jobs WHERE url_hash = 'h1'"
            ).fetchone()
        self.assertEqual(row["status"], "active")
        self.assertGreater(row["last_seen"], old)

    def test_empty_input_touches_nothing(self):
        from src.catalog_db import touch_catalog_last_seen

        self.assertEqual(touch_catalog_last_seen([]), 0)

    def test_never_reactivates_an_expired_row(self):
        """Regression: this used to set status='active' unconditionally
        for every hash it was given, with no check at all — a company's
        confirmed-closed native job would silently reopen the moment a
        slower-cycling aggregator crawl still listed the same URL."""
        from datetime import datetime, timedelta

        from src.catalog_db import touch_catalog_last_seen
        from src.db import connect

        old = (datetime.utcnow() - timedelta(days=10)).isoformat()
        with connect() as conn:
            conn.execute(
                "INSERT INTO catalog_jobs (url_hash, title, status, first_seen, last_seen) "
                "VALUES ('h1', 'Engineer', 'expired', ?, ?)",
                (old, old),
            )

        updated = touch_catalog_last_seen(["h1"])
        self.assertEqual(updated, 1)  # last_seen still bumps

        with connect() as conn:
            row = conn.execute(
                "SELECT status, last_seen FROM catalog_jobs WHERE url_hash = 'h1'"
            ).fetchone()
        self.assertEqual(row["status"], "expired")
        self.assertGreater(row["last_seen"], old)


class FingerprintDuplicateFlaggingTests(TempDBTestCase):
    """compute_fingerprint / possible_duplicates flag likely-duplicate
    postings (same title, company, location) without merging or hiding
    them — a company can legitimately have more than one open req for the
    same role in the same place, so only url_hash-identical rows (an exact
    re-crawl of the same link) are ever treated as *the same* catalog row;
    same-fingerprint rows stay independent and are just flagged for a
    person to judge.
    """

    def test_same_title_company_location_share_a_fingerprint(self):
        from src.catalog_db import compute_fingerprint

        a = compute_fingerprint("Senior Software Engineer", "Infios", "Remote - Mexico")
        b = compute_fingerprint("  senior software engineer  ", "INFIOS", "remote - mexico")
        self.assertEqual(a, b)

    def test_different_location_is_a_different_fingerprint(self):
        from src.catalog_db import compute_fingerprint

        mexico = compute_fingerprint("Senior Software Engineer", "Infios", "Remote - Mexico")
        india = compute_fingerprint("Senior Software Engineer", "Infios", "Remote - India")
        self.assertNotEqual(mexico, india)

    def test_missing_title_or_company_has_no_fingerprint(self):
        from src.catalog_db import compute_fingerprint

        self.assertEqual(compute_fingerprint("", "Infios", "Remote"), "")
        self.assertEqual(compute_fingerprint("Engineer", "", "Remote"), "")

    def test_upsert_sets_fingerprint_and_join_select_flags_duplicates_without_merging(self):
        from src.db import connect, upsert_job

        # Three distinct requisitions (distinct URLs) for the same role,
        # company, and location — the Infios case found in production data.
        for i in range(3):
            upsert_job(
                {
                    "title": "Senior Software Engineer",
                    "company": "Infios",
                    "location": "Remote - Mexico",
                    "url": f"https://infios.wd502.myworkdayjobs.com/infios/job/Remote-Mexico/req{i}",
                },
                source_id="test",
            )
        # An unrelated posting must not be flagged as a duplicate of them.
        upsert_job(
            {
                "title": "Staff Data Engineer",
                "company": "Othercorp",
                "location": "Remote",
                "url": "https://example.com/jobs/999",
            },
            source_id="test",
        )

        with connect() as conn:
            rows = conn.execute(
                "SELECT title, company, fingerprint FROM catalog_jobs ORDER BY id"
            ).fetchall()

        self.assertEqual(len({r["fingerprint"] for r in rows[:3]}), 1)
        self.assertNotEqual(rows[0]["fingerprint"], rows[3]["fingerprint"])

        # All three remain independent catalog rows -- flagging never
        # merges or drops one in favor of another.
        self.assertEqual(len(rows), 4)

        from src.db import create_user, get_jobs
        from src.catalog_db import sync_catalog_to_user

        uid = create_user("dupe@test.com", display_name="Dupe")["id"]
        sync_catalog_to_user(user_id=uid, limit=10)
        jobs = get_jobs(user_id=uid, limit=10)

        # All 3 Infios reqs must still be synced independently (not merged
        # into one row) ...
        infios_jobs = [j for j in jobs if j["company"] == "Infios"]
        self.assertEqual(len(infios_jobs), 3)
        # ... but each one is flagged as having 2 other similar postings.
        for j in infios_jobs:
            self.assertEqual(j["possible_duplicates"], 2)

        other = next(j for j in jobs if j["company"] == "Othercorp")
        self.assertEqual(other["possible_duplicates"], 0)


if __name__ == "__main__":
    import unittest

    unittest.main()
