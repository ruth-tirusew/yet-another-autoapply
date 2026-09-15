"""Tests for src/eligibility_backfill.py (re-checking location eligibility)."""

from __future__ import annotations

import json

from tests.helpers import TempDBTestCase


class RecheckEligibilityTests(TempDBTestCase):
    def setUp(self):
        super().setUp()
        from src.db import connect, create_user

        self.user_id = create_user("backfill@test.com", display_name="Backfill")["id"]
        resume = {"basics": {"location": {"countryCode": "DE"}}}
        with connect() as conn:
            conn.execute(
                "INSERT INTO profile (user_id, resume_json, updated_at) VALUES (?, ?, '2026-01-01')",
                (self.user_id, json.dumps(resume)),
            )

    def _insert_job(self, url_hash: str, location: str, status: str = "new") -> int:
        from src.db import connect

        with connect() as conn:
            conn.execute(
                "INSERT INTO catalog_jobs (url_hash, title, location, status) VALUES (?, 'Engineer', ?, 'active')",
                (url_hash, location),
            )
            catalog_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                "INSERT INTO user_jobs (user_id, catalog_job_id, status) VALUES (?, ?, ?)",
                (self.user_id, catalog_id, status),
            )
            return conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def test_marks_newly_ineligible_job_skipped(self):
        from src.eligibility_backfill import recheck_eligibility
        from src.db import get_job

        job_id = self._insert_job("us-only", "Remote - US only")
        stats = recheck_eligibility(self.user_id)
        self.assertEqual(stats["checked"], 1)
        self.assertEqual(stats["skipped"], 1)
        self.assertEqual(stats["unchanged"], 0)

        updated = get_job(job_id, user_id=self.user_id)
        self.assertEqual(updated["status"], "skipped")
        self.assertEqual(json.loads(updated["match_details"])["recommendation"], "skip")

    def test_leaves_eligible_job_untouched(self):
        from src.eligibility_backfill import recheck_eligibility
        from src.db import get_job

        job_id = self._insert_job("worldwide", "Remote - Worldwide")
        stats = recheck_eligibility(self.user_id)
        self.assertEqual(stats["unchanged"], 1)
        self.assertEqual(stats["skipped"], 0)

        updated = get_job(job_id, user_id=self.user_id)
        self.assertEqual(updated["status"], "new")

    def test_ignores_jobs_outside_recheck_statuses(self):
        from src.eligibility_backfill import recheck_eligibility

        self._insert_job("already-applied", "Remote - US only", status="applied")
        stats = recheck_eligibility(self.user_id)
        self.assertEqual(stats["checked"], 0)

    def test_recheck_all_users_aggregates_totals(self):
        from src.db import connect, create_user
        from src.eligibility_backfill import recheck_eligibility_all_users

        self._insert_job("u1-bad", "Remote - US only")
        second_uid = create_user("backfill2@test.com", display_name="Backfill2")["id"]
        with connect() as conn:
            conn.execute(
                "INSERT INTO profile (user_id, resume_json, updated_at) VALUES (?, '{}', '2026-01-01')",
                (second_uid,),
            )
            conn.execute(
                "INSERT INTO catalog_jobs (url_hash, title, location, status) VALUES ('u2-ok', 'Engineer', 'Remote - Worldwide', 'active')"
            )
            catalog_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                "INSERT INTO user_jobs (user_id, catalog_job_id, status) VALUES (?, ?, 'new')",
                (second_uid, catalog_id),
            )

        totals = recheck_eligibility_all_users()
        self.assertEqual(totals["users"], 2)
        self.assertEqual(totals["checked"], 2)
        self.assertEqual(totals["skipped"], 1)
        self.assertEqual(totals["unchanged"], 1)


if __name__ == "__main__":
    import unittest

    unittest.main()
