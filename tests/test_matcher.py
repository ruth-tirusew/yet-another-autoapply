"""Tests for scoring-mode routing in src/matcher.py::match_job.

Verifies that scoring_mode="rules" never calls the LLM, and that
scoring_mode="hybrid" only falls back to the LLM when the rule engine's
result is ambiguous (near the threshold, or an unclassifiable seniority).
"""

from __future__ import annotations

import json
from unittest import mock

from tests.helpers import TempDBTestCase

SENIORITY_LEVELS = [
    "intern",
    "junior",
    "mid",
    "senior",
    "staff",
    "principal",
    "lead",
    "manager",
    "director",
    "vp",
]


def _cfg(scoring_mode: str) -> dict:
    return {
        "match_threshold": 70,
        "quality_model": "should-not-be-used",
        "pipeline": {
            "role_fit_min": 15,
            "maybe_score_boost": 10,
            "match_resume_chars": 6000,
            "match_github_chars": 2000,
            "match_job_desc_chars": 5000,
        },
        "matching": {
            "scoring_mode": scoring_mode,
            "rules": {
                "hybrid_band": 10,
                "seniority_levels": SENIORITY_LEVELS,
                "candidate_seniority": "senior",
                "synonyms": {},
            },
        },
    }


LLM_RESULT = {
    "overall_score": 55,
    "skill_match": {"score": 20, "max": 40, "evidence": "llm evidence"},
    "experience_match": {"score": 20, "max": 35, "evidence": "llm evidence"},
    "role_fit": {"score": 15, "max": 25, "evidence": "llm evidence"},
    "gaps": [],
    "strengths": [],
    "recommendation": "maybe",
    "tailoring_hints": [],
}


class MatcherScoringModeTests(TempDBTestCase):
    def setUp(self):
        super().setUp()

        from src.db import connect, create_user

        user = create_user("matcher@test.com", display_name="Matcher")
        self.user_id = user["id"]

        resume = {
            "basics": {"name": "Test Candidate"},
            "skills": [{"name": "Backend", "keywords": ["Python", "FastAPI", "Kubernetes"]}],
            "work": [
                {
                    "position": "Senior Backend Engineer",
                    "name": "Acme",
                    "startDate": "2018-01",
                    "endDate": "Present",
                }
            ],
        }
        with connect() as conn:
            conn.execute(
                "INSERT INTO profile (user_id, resume_json, updated_at) VALUES (?, ?, '2026-01-01')",
                (self.user_id, json.dumps(resume)),
            )

    def _insert_job(self, title: str, description: str) -> int:
        from src.db import connect

        with connect() as conn:
            conn.execute(
                """
                INSERT INTO catalog_jobs (url_hash, title, company, location, description_full, status)
                VALUES (?, ?, 'Acme', 'Remote - Worldwide', ?, 'active')
                """,
                (title, title, description),
            )
            catalog_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                "INSERT INTO user_jobs (user_id, catalog_job_id, status) VALUES (?, ?, 'new')",
                (self.user_id, catalog_id),
            )
            return conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def test_rules_mode_never_calls_llm(self):
        from src import matcher

        job_id = self._insert_job(
            "Senior Backend Engineer",
            "5+ years experience with Python, FastAPI and Kubernetes required.",
        )
        with mock.patch("src.matcher.get_config", return_value=_cfg("rules")), mock.patch(
            "src.matcher.chat_json", side_effect=AssertionError("LLM should not be called")
        ) as mocked_chat:
            result = matcher.match_job(job_id, user_id=self.user_id)

        self.assertIsNotNone(result)
        self.assertEqual(result.engine, "rules")
        mocked_chat.assert_not_called()

        stored = matcher.get_job(job_id, user_id=self.user_id)
        self.assertEqual(json.loads(stored["match_details"])["engine"], "rules")

    def test_hybrid_mode_skips_llm_when_unambiguous(self):
        from src import matcher

        job_id = self._insert_job(
            "Senior Backend Engineer",
            "5+ years experience with Python, FastAPI and Kubernetes required.",
        )
        with mock.patch("src.matcher.get_config", return_value=_cfg("hybrid")), mock.patch(
            "src.matcher.chat_json", side_effect=AssertionError("LLM should not be called")
        ) as mocked_chat:
            result = matcher.match_job(job_id, user_id=self.user_id)

        self.assertIsNotNone(result)
        self.assertEqual(result.engine, "rules")
        mocked_chat.assert_not_called()

    def test_hybrid_mode_falls_back_to_llm_when_seniority_unclassifiable(self):
        from src import matcher

        job_id = self._insert_job("Growth Ninja", "Python required.")
        with mock.patch("src.matcher.get_config", return_value=_cfg("hybrid")), mock.patch(
            "src.matcher.chat_json", return_value=LLM_RESULT
        ) as mocked_chat:
            result = matcher.match_job(job_id, user_id=self.user_id)

        self.assertIsNotNone(result)
        self.assertEqual(result.engine, "llm")
        mocked_chat.assert_called_once()


if __name__ == "__main__":
    import unittest

    unittest.main()
