"""End-to-end grounded matching through src.matcher.match_job."""

from __future__ import annotations

import json
from unittest import mock

from tests.helpers import TempDBTestCase

POSTING = """About Acme
We have been building payments for 15 years.

Requirements
- 4+ years of professional Python experience
- Experience running services on Kubernetes
- Strong SQL skills

Nice to have
- Go

Benefits
- Snacks and equity
"""

RESUME = {
    "basics": {
        "name": "Test Candidate",
        "summary": "Backend engineer",
        "location": {"city": "Addis Ababa", "countryCode": "ET"},
    },
    "work": [
        {
            "position": "Senior Backend Engineer",
            "name": "Acme",
            "startDate": "2018-01",
            "endDate": "",
            "highlights": [
                "Built Django and FastAPI services in Python",
                "Operated Kubernetes clusters in production",
            ],
        }
    ],
    "skills": [{"name": "Backend", "keywords": ["python", "sql", "postgres"]}],
}

EXTRACTION = {
    "seniority": "senior",
    "years_required": 4,
    "requirements": [
        {"text": "4+ years of professional Python experience", "kind": "must", "category": "experience"},
        {"text": "Experience running services on Kubernetes", "kind": "must", "category": "skill"},
        {"text": "Strong SQL skills", "kind": "must", "category": "skill"},
        {"text": "Go", "kind": "nice", "category": "skill"},
    ],
}

JUDGEMENT = {
    "verdicts": [
        {"id": "r1", "verdict": "met", "confidence": 0.9, "evidence": [{"chunk_id": "work-0", "quote": "Python"}]},
        {"id": "r2", "verdict": "met", "confidence": 0.9, "evidence": [{"chunk_id": "work-0", "quote": "Kubernetes"}]},
        {"id": "r3", "verdict": "partial", "confidence": 0.5, "evidence": [{"chunk_id": "skills-0", "quote": "sql"}]},
        {"id": "r4", "verdict": "missing", "confidence": 0.8, "evidence": []},
    ]
}

CONFIG = {
    "match_threshold": 70,
    "quality_model": "test-model",
    "llm_provider": "ollama",
    "ollama_host": "http://localhost:11434",
    "pipeline": {"role_fit_min": 15, "maybe_score_boost": 10},
    "matching": {
        "scoring_mode": "grounded",
        "max_match_attempts": 3,
        "rules": {"candidate_seniority": "senior"},
    },
}


class GroundedMatchTests(TempDBTestCase):
    def setUp(self):
        super().setUp()
        from src.db import create_user, save_profile, upsert_job
        from src.tenant import set_tenant_user_id

        self.uid = create_user("grounded@test.com", display_name="G")["id"]
        set_tenant_user_id(self.uid)
        save_profile(RESUME, "", user_id=self.uid)
        self.job_id = upsert_job(
            {
                "title": "Senior Backend Engineer",
                "company": "Acme",
                "url": "https://example.com/jobs/grounded-1",
                "location": "Remote",
                "description_full": POSTING,
            },
            source_id="test",
            user_id=self.uid,
        )

    def _match(self, chat_side_effect=None):
        from src.matcher import match_job

        side_effect = chat_side_effect or [EXTRACTION, JUDGEMENT]
        # requirements.py and judge.py each import chat_json/get_merged_config
        # at module level, so both must be patched at their own qualified
        # name (not src.llm/src.config_store, where they're merely defined) —
        # and both patched to the *same* mock so a shared side_effect list is
        # consumed in call order regardless of which module calls it first.
        chat = mock.Mock(side_effect=side_effect)
        with mock.patch("src.matching.requirements.chat_json", chat), mock.patch(
            "src.matching.judge.chat_json", chat
        ), mock.patch(
            "src.matching.requirements.get_merged_config", return_value=CONFIG
        ), mock.patch(
            "src.matching.judge.get_merged_config", return_value=CONFIG
        ), mock.patch(
            "src.config.get_config", return_value=CONFIG
        ), mock.patch(
            "src.config_store.get_merged_config", return_value=CONFIG
        ), mock.patch(
            "src.matcher.get_config", return_value=CONFIG
        ), mock.patch(
            "src.matcher.resume_to_text", return_value=("resume text", "")
        ), mock.patch(
            "src.matcher.format_evaluation_for_match", return_value=""
        ), mock.patch(
            "src.matching.vectors.embed_texts", side_effect=RuntimeError("no embedding backend")
        ):
            result = match_job(self.job_id, user_id=self.uid)
        return result, chat

    def test_produces_a_grounded_result_with_per_requirement_verdicts(self):
        result, _ = self._match()
        self.assertIsNotNone(result)
        self.assertEqual(result.engine, "grounded")
        self.assertEqual(len(result.requirements), 4)
        self.assertEqual({v.id for v in result.requirements}, {"r1", "r2", "r3", "r4"})

    def test_overall_score_is_the_sum_of_its_components(self):
        result, _ = self._match()
        self.assertEqual(
            result.overall_score,
            result.skill_match.score + result.experience_match.score + result.role_fit.score,
        )

    def test_gaps_and_hints_are_traceable_to_requirements(self):
        result, _ = self._match()
        self.assertTrue(any("Strong SQL skills" in h for h in result.tailoring_hints))
        self.assertTrue(any("Kubernetes" in s for s in result.strengths))
        # "Go" is only nice-to-have, so a missing nice item is not a required gap
        self.assertNotIn("Go", result.gaps)

    def test_result_records_provenance_for_comparability(self):
        result, _ = self._match()
        self.assertEqual(result.model, "test-model")
        self.assertIn("req-v1", result.prompt_version)
        self.assertEqual(result.retrieval, "lexical")
        self.assertTrue(any("embedding" in w.lower() for w in result.warnings))

    def test_details_round_trip_through_the_database(self):
        from src.ats import parse_match_details
        from src.db import get_job

        self._match()
        job = get_job(self.job_id, user_id=self.uid)
        stored = parse_match_details(job)
        self.assertIsNotNone(stored)
        self.assertEqual(stored.engine, "grounded")
        self.assertEqual(len(stored.requirements), 4)
        self.assertEqual(json.loads(job["match_details"])["coverage"], stored.coverage)

    def test_extraction_is_reused_for_a_second_user(self):
        """The shared catalog row means the second user only pays for judging."""
        from src.catalog_db import ensure_user_job_for_catalog
        from src.db import create_user, get_job, save_profile
        from src.matcher import match_job
        from src.tenant import set_tenant_user_id

        self._match()
        other = create_user("second@test.com", display_name="S")["id"]
        save_profile(RESUME, "", user_id=other)
        catalog_job_id = get_job(self.job_id, user_id=self.uid)["catalog_job_id"]
        other_job_id = ensure_user_job_for_catalog(catalog_job_id, other)
        set_tenant_user_id(other)

        chat = mock.Mock(side_effect=[JUDGEMENT])
        with mock.patch("src.matching.requirements.chat_json", chat), mock.patch(
            "src.matching.judge.chat_json", chat
        ), mock.patch(
            "src.matching.requirements.get_merged_config", return_value=CONFIG
        ), mock.patch(
            "src.matching.judge.get_merged_config", return_value=CONFIG
        ), mock.patch(
            "src.config.get_config", return_value=CONFIG
        ), mock.patch("src.config_store.get_merged_config", return_value=CONFIG), mock.patch(
            "src.matcher.get_config", return_value=CONFIG
        ), mock.patch(
            "src.matcher.resume_to_text", return_value=("resume text", "")
        ), mock.patch(
            "src.matcher.format_evaluation_for_match", return_value=""
        ), mock.patch(
            "src.matching.vectors.embed_texts", side_effect=RuntimeError("no embedding backend")
        ):
            result = match_job(other_job_id, user_id=other)
        self.assertEqual(chat.call_count, 1, "requirements must come from the catalog cache")
        self.assertEqual(len(result.requirements), 4)

    def test_judge_failure_falls_back_to_retrieval_verdicts(self):
        result, _ = self._match(chat_side_effect=[EXTRACTION, RuntimeError("model down")])
        self.assertEqual(result.engine, "grounded")
        self.assertEqual(len(result.requirements), 4)
        self.assertTrue(any("retrieval only" in w for w in result.warnings))

    def test_repeated_failures_retire_the_job_instead_of_retrying_forever(self):
        from src.db import get_job, update_job
        from src.matcher import _record_match_failure

        for _ in range(3):
            _record_match_failure(self.job_id, self.uid, "boom", CONFIG)
        job = get_job(self.job_id, user_id=self.uid)
        self.assertEqual(job["status"], "skipped")
        self.assertEqual(job["match_attempts"], 3)
        self.assertIn("scoring failed", job["match_summary"])

    def test_grounded_path_does_not_serialize_on_the_vendor_chdir(self):
        """resume_to_text() chdir()s the process under a global lock.

        The grounded path has no use for it, so it must not be called per job.
        """
        from src.matcher import match_job

        chat = mock.Mock(side_effect=[EXTRACTION, JUDGEMENT])
        with mock.patch("src.matching.requirements.chat_json", chat), mock.patch(
            "src.matching.judge.chat_json", chat
        ), mock.patch(
            "src.matching.requirements.get_merged_config", return_value=CONFIG
        ), mock.patch(
            "src.matching.judge.get_merged_config", return_value=CONFIG
        ), mock.patch(
            "src.config.get_config", return_value=CONFIG
        ), mock.patch("src.config_store.get_merged_config", return_value=CONFIG), mock.patch(
            "src.matcher.get_config", return_value=CONFIG
        ), mock.patch(
            "src.matching.vectors.embed_texts", side_effect=RuntimeError("no embedding backend")
        ), mock.patch(
            "src.matcher.resume_to_text"
        ) as resume_to_text:
            match_job(self.job_id, user_id=self.uid)
        resume_to_text.assert_not_called()
