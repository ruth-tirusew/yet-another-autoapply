"""Tests for src/db.py::clear_user_jobs (the "Clear jobs" button's backend)."""

from __future__ import annotations

from tests.helpers import TempDBTestCase


class ClearUserJobsTests(TempDBTestCase):
    def setUp(self):
        super().setUp()
        from src.db import connect, create_user

        self.user1 = create_user("clear1@test.com", display_name="Clear1")["id"]
        self.user2 = create_user("clear2@test.com", display_name="Clear2")["id"]

        with connect() as conn:
            conn.execute(
                "INSERT INTO profile (user_id, resume_json, updated_at) VALUES (?, '{}', '2026-01-01')",
                (self.user1,),
            )
            conn.execute(
                "INSERT INTO catalog_jobs (url_hash, title, status) VALUES ('a', 'Job A', 'active')"
            )
            conn.execute(
                "INSERT INTO catalog_jobs (url_hash, title, status) VALUES ('b', 'Job B', 'active')"
            )
            conn.execute(
                "INSERT INTO user_jobs (user_id, catalog_job_id, status) VALUES (?, 1, 'queued')",
                (self.user1,),
            )
            conn.execute(
                "INSERT INTO user_jobs (user_id, catalog_job_id, status) VALUES (?, 2, 'applied')",
                (self.user1,),
            )
            conn.execute(
                "INSERT INTO user_jobs (user_id, catalog_job_id, status) VALUES (?, 1, 'new')",
                (self.user2,),
            )
            conn.execute(
                "INSERT INTO applications (job_id, apply_method) VALUES (2, 'greenhouse')"
            )
            conn.execute(
                "INSERT INTO application_events (job_id, event_type, detail_json, created_at) "
                "VALUES (1, 'matched', '{}', '2026-01-01')"
            )
            conn.execute(
                "INSERT INTO application_events (job_id, event_type, detail_json, created_at) "
                "VALUES (2, 'applied', '{}', '2026-01-01')"
            )
            conn.execute(
                "INSERT INTO application_events (job_id, event_type, detail_json, created_at) "
                "VALUES (3, 'matched', '{}', '2026-01-01')"
            )

    def test_clears_only_the_target_users_jobs_applications_and_events(self):
        from src.db import clear_user_jobs, connect

        result = clear_user_jobs(self.user1)
        self.assertEqual(result, {"jobs": 2, "applications": 1, "events": 2})

        with connect() as conn:
            remaining_jobs = conn.execute(
                "SELECT user_id FROM user_jobs"
            ).fetchall()
            remaining_apps = conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
            remaining_events = conn.execute("SELECT COUNT(*) FROM application_events").fetchone()[0]
            catalog_count = conn.execute("SELECT COUNT(*) FROM catalog_jobs").fetchone()[0]

        self.assertEqual([r["user_id"] for r in remaining_jobs], [self.user2])
        self.assertEqual(remaining_apps, 0)
        self.assertEqual(remaining_events, 1)
        self.assertEqual(catalog_count, 2)  # shared catalog untouched

    def test_keeps_profile_and_account_intact(self):
        from src.db import clear_user_jobs, get_profile, get_user_by_id

        clear_user_jobs(self.user1)
        self.assertIsNotNone(get_user_by_id(self.user1))
        self.assertIsNotNone(get_profile(self.user1))

    def test_clearing_empty_queue_is_a_noop(self):
        from src.db import clear_user_jobs

        result = clear_user_jobs(self.user2)
        second = clear_user_jobs(self.user2)
        self.assertEqual(second, {"jobs": 0, "applications": 0, "events": 0})


if __name__ == "__main__":
    import unittest

    unittest.main()
