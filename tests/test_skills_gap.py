"""Tests for skills gap aggregation."""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.coaching.skills_gap import aggregate_gaps


class SkillsGapTests(unittest.TestCase):
    def test_aggregate_gaps_counts_frequency(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            conn = sqlite3.connect(db_path)
            conn.executescript(
                """
                CREATE TABLE catalog_jobs (id INTEGER PRIMARY KEY, url_hash TEXT UNIQUE, status TEXT);
                CREATE TABLE user_jobs (
                    id INTEGER PRIMARY KEY, user_id INTEGER, catalog_job_id INTEGER,
                    status TEXT, match_score REAL, match_details TEXT
                );
                INSERT INTO catalog_jobs (id, url_hash, status) VALUES (1, 'a', 'active');
                """
            )
            conn.execute(
                """
                INSERT INTO user_jobs (id, user_id, catalog_job_id, status, match_score, match_details)
                VALUES (1, 1, 1, 'queued', 85, ?)
                """,
                (json.dumps({"gaps": ["Kubernetes", "GraphQL"]}),),
            )
            conn.execute(
                """
                INSERT INTO user_jobs (id, user_id, catalog_job_id, status, match_score, match_details)
                VALUES (2, 1, 1, 'scored', 70, ?)
                """,
                (json.dumps({"gaps": ["Kubernetes", "Terraform"]}),),
            )
            conn.commit()
            conn.close()

            import src.db as dbmod
            import src.settings as settings

            old_path = settings.DB_PATH
            settings.DB_PATH = db_path
            dbmod.settings.DB_PATH = db_path
            try:
                freq = aggregate_gaps(user_id=1)
                self.assertEqual(freq["Kubernetes"], 2)
                self.assertEqual(freq["GraphQL"], 1)
            finally:
                settings.DB_PATH = old_path
                dbmod.settings.DB_PATH = old_path


if __name__ == "__main__":
    unittest.main()
