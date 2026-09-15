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


if __name__ == "__main__":
    import unittest

    unittest.main()
