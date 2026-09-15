"""Tests for src/cover_letter.py (LLM-generated cover letters, gated by ATS fit)."""

from __future__ import annotations

import json
from unittest import mock

from tests.helpers import TempDBTestCase


class CoverLetterTests(TempDBTestCase):
    def setUp(self):
        super().setUp()
        from src.db import connect, create_user

        self.user_id = create_user("coverletter@test.com", display_name="Cover")["id"]
        resume = {"basics": {"name": "Test Candidate", "summary": "Backend engineer."}}
        with connect() as conn:
            conn.execute(
                "INSERT INTO profile (user_id, resume_json, updated_at) VALUES (?, ?, '2026-01-01')",
                (self.user_id, json.dumps(resume)),
            )

    def _insert_job(self, match_details: dict | None, title: str = "Backend Engineer") -> int:
        from src.db import connect

        details_json = json.dumps(match_details) if match_details is not None else None
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO catalog_jobs (url_hash, title, description_full, status)
                VALUES (?, ?, 'Great backend role.', 'active')
                """,
                (title, title),
            )
            catalog_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                "INSERT INTO user_jobs (user_id, catalog_job_id, status, match_details) VALUES (?, ?, 'scored', ?)",
                (self.user_id, catalog_id, details_json),
            )
            return conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def test_missing_job_raises_value_error(self):
        from src.cover_letter import generate_cover_letter

        with self.assertRaises(ValueError):
            generate_cover_letter(999999, user_id=self.user_id)

    def test_ats_skip_recommendation_short_circuits_without_llm(self):
        from src.cover_letter import CoverLetterSkipped, generate_cover_letter

        job_id = self._insert_job(
            {
                "overall_score": 20,
                "skill_match": {"score": 5, "max": 40, "evidence": ""},
                "experience_match": {"score": 5, "max": 35, "evidence": ""},
                "role_fit": {"score": 5, "max": 25, "evidence": ""},
                "recommendation": "skip",
            }
        )
        with mock.patch("src.cover_letter.chat", side_effect=AssertionError("LLM should not be called")):
            with self.assertRaises(CoverLetterSkipped):
                generate_cover_letter(job_id, user_id=self.user_id)

    def test_low_role_fit_short_circuits_without_llm(self):
        from src.cover_letter import CoverLetterSkipped, generate_cover_letter

        job_id = self._insert_job(
            {
                "overall_score": 60,
                "skill_match": {"score": 30, "max": 40, "evidence": ""},
                "experience_match": {"score": 25, "max": 35, "evidence": ""},
                "role_fit": {"score": 5, "max": 25, "evidence": ""},
                "recommendation": "maybe",
            }
        )
        with mock.patch("src.cover_letter.chat", side_effect=AssertionError("LLM should not be called")):
            with self.assertRaises(CoverLetterSkipped):
                generate_cover_letter(job_id, user_id=self.user_id)

    def test_force_bypasses_skip_checks(self):
        from src.cover_letter import generate_cover_letter

        job_id = self._insert_job({
            "overall_score": 10,
            "skill_match": {"score": 1, "max": 40, "evidence": ""},
            "experience_match": {"score": 1, "max": 35, "evidence": ""},
            "role_fit": {"score": 1, "max": 25, "evidence": ""},
            "recommendation": "skip",
        })
        with mock.patch("src.cover_letter.chat", return_value="Dear Hiring Manager, ..."):
            path = generate_cover_letter(job_id, force=True, user_id=self.user_id)

        self.assertTrue(path.exists())
        self.assertEqual(path.read_text(encoding="utf-8"), "Dear Hiring Manager, ...")

    def test_happy_path_writes_file_and_logs_event(self):
        from src.cover_letter import generate_cover_letter
        from src.db import get_job

        job_id = self._insert_job(None)
        with mock.patch("src.cover_letter.chat", return_value="Dear Hiring Manager, I am excited..."):
            path = generate_cover_letter(job_id, user_id=self.user_id)

        self.assertTrue(path.exists())
        self.assertIn("excited", path.read_text(encoding="utf-8"))
        updated = get_job(job_id, user_id=self.user_id)
        self.assertIsNotNone(updated)


if __name__ == "__main__":
    import unittest

    unittest.main()
