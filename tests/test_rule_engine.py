"""Unit tests for the deterministic rule-based matcher (src/rule_engine.py).

Pure-function module — no DB/LLM mocking needed, unlike the LLM matcher path.
"""

from __future__ import annotations

import unittest

from src.rule_engine import (
    UNCLASSIFIED_MARKER,
    _extract_required_years,
    _years_of_experience,
    compute_role_fit,
    score_job_rules,
)

RULES_CFG = {
    "seniority_levels": [
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
    ],
    "candidate_seniority": "senior",
    "synonyms": {"k8s": "kubernetes", "js": "javascript"},
}


def _resume(skills=None, work=None):
    return {
        "skills": skills or [],
        "work": work or [],
    }


class FullMatchTests(unittest.TestCase):
    def test_full_skill_experience_seniority_match(self):
        resume = _resume(
            skills=[{"name": "Backend", "keywords": ["Python", "FastAPI", "Kubernetes"]}],
            work=[
                {
                    "position": "Senior Backend Engineer",
                    "name": "Acme",
                    "startDate": "2018-01",
                    "endDate": "Present",
                }
            ],
        )
        job = {
            "title": "Senior Backend Engineer",
            "description_full": "5+ years experience with Python, FastAPI and Kubernetes required.",
        }
        result = score_job_rules(resume, job, RULES_CFG, threshold=70, role_fit_min=15)
        self.assertEqual(result.engine, "rules")
        self.assertEqual(result.recommendation, "apply")
        self.assertEqual(result.role_fit.score, result.role_fit.max)
        self.assertGreaterEqual(result.overall_score, 85)


class PartialSkillOverlapTests(unittest.TestCase):
    def test_partial_overlap_lowers_skill_score_and_lists_gaps(self):
        resume = _resume(
            skills=[{"name": "Backend", "keywords": ["Python", "Rust", "PostgreSQL"]}],
            work=[{"position": "Backend Engineer", "startDate": "2019-01", "endDate": "Present"}],
        )
        job = {
            "title": "Backend Engineer",
            "description_full": "Looking for a Python engineer.",
        }
        result = score_job_rules(resume, job, RULES_CFG)
        self.assertLess(result.skill_match.score, result.skill_match.max)
        self.assertIn("rust", result.gaps)
        self.assertIn("postgresql", result.gaps)


class SeniorityMismatchTests(unittest.TestCase):
    def test_ic_resume_vs_director_title_scores_low_and_skips(self):
        resume = _resume(
            skills=[{"name": "Backend", "keywords": ["Python"]}],
            work=[{"position": "Backend Engineer", "startDate": "2020-01", "endDate": "Present"}],
        )
        job = {"title": "Director of Engineering", "description_full": "Python shop."}
        cfg = {**RULES_CFG, "candidate_seniority": "mid"}
        result = score_job_rules(resume, job, cfg, threshold=70, role_fit_min=15)
        self.assertLessEqual(result.role_fit.score, 5)
        self.assertEqual(result.recommendation, "skip")

    def test_unclassifiable_title_is_neutral_and_flagged(self):
        score = compute_role_fit(_resume(), "Growth Ninja", RULES_CFG)
        self.assertIn(UNCLASSIFIED_MARKER, score.evidence)
        self.assertGreater(score.score, 0)
        self.assertLess(score.score, score.max)


class MissingWorkHistoryTests(unittest.TestCase):
    def test_empty_work_history_does_not_crash(self):
        resume = _resume(skills=[{"name": "Backend", "keywords": ["Python"]}], work=[])
        job = {"title": "Backend Engineer", "description_full": "3+ years Python experience."}
        result = score_job_rules(resume, job, RULES_CFG)
        self.assertEqual(result.engine, "rules")
        self.assertEqual(_years_of_experience([]), 0.0)
        # Zero years against a 3-year requirement should score low, not crash.
        self.assertLess(result.experience_match.score, result.experience_match.max)


class RequiredYearsExtractionTests(unittest.TestCase):
    def test_explicit_years_requirement_is_extracted(self):
        self.assertEqual(_extract_required_years("Must have 5+ years experience."), 5)
        self.assertEqual(_extract_required_years("3-5 years of experience"), 3)

    def test_no_requirement_returns_none(self):
        self.assertIsNone(_extract_required_years("Great team, remote friendly."))


if __name__ == "__main__":
    unittest.main()
