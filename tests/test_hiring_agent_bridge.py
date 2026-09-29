"""Regression tests for src/hiring_agent_bridge.py::resume_to_text.

Covers a real bug: the LLM-generated "generalized" resume variant
(src/coaching/generalize_profile.py::build_general_resume) sometimes returns
`skills` as a flat list of strings (e.g. ["Python", "Go"]) instead of JSON
Resume's expected [{"name": ..., "keywords": [...]}] shape. Feeding that
straight into hiring_agent's strict JSONResume(**resume) model raised a
Pydantic ValidationError, which made every match_job() call fail for any
posting scored against the general resume variant.
"""

from __future__ import annotations

import unittest

from src.hiring_agent_bridge import clamp_evaluation, resume_to_text, unbullet_job_headers


class ResumeToTextSkillsShapeTests(unittest.TestCase):
    def test_flat_string_skills_do_not_crash(self):
        resume = {
            "basics": {"name": "Test Candidate", "email": "test@example.com"},
            "skills": ["Python", "Go", "Kubernetes"],
        }
        text, github_text = resume_to_text(resume)
        self.assertIn("Test Candidate", text)

    def test_proper_skill_dicts_still_work(self):
        resume = {
            "basics": {"name": "Test Candidate"},
            "skills": [{"name": "Backend", "keywords": ["Python", "Go"]}],
        }
        text, _ = resume_to_text(resume)
        self.assertIn("Test Candidate", text)

    def test_missing_skills_field_still_works(self):
        resume = {"basics": {"name": "Test Candidate"}}
        text, _ = resume_to_text(resume)
        self.assertIn("Test Candidate", text)


if __name__ == "__main__":
    unittest.main()


class UnbulletJobHeadersTests(unittest.TestCase):
    def test_bulleted_job_header_becomes_plain_line(self):
        md = (
            "- Extended the Java core.\n"
            "- **Backend Developer · Rahove** Sep 2023 – Mar 2024 Addis Ababa\n"
            "- Built real-time features.\n"
        )
        out = unbullet_job_headers(md)
        self.assertIn("\n**Backend Developer · Rahove** Sep 2023 – Mar 2024", out)
        self.assertIn("- Extended the Java core.", out)
        self.assertIn("- Built real-time features.", out)

    def test_bullet_without_date_range_is_kept(self):
        md = "- **Reconciliation** closed a 20M ETB gap in 2024\n"
        self.assertEqual(unbullet_job_headers(md), md)


class ClampEvaluationTests(unittest.TestCase):
    def _evaluation(self, **scores):
        return {"scores": {k: {"score": v, "max": 99, "evidence": "x"} for k, v in scores.items()}}

    def test_scores_capped_at_rubric_max(self):
        out = clamp_evaluation(self._evaluation(production=40, technical_skills=-3))
        self.assertEqual(out["scores"]["production"], {"score": 35.0, "max": 35, "evidence": "x"})
        self.assertEqual(out["scores"]["technical_skills"]["score"], 0.0)

    def test_open_source_capped_when_all_repos_are_self_projects(self):
        github = {"projects": [{"project_type": "self_project"}] * 3}
        out = clamp_evaluation(self._evaluation(open_source=15), github)
        self.assertEqual(out["scores"]["open_source"]["score"], 8.0)

    def test_open_source_not_capped_with_real_contributions(self):
        github = {"projects": [{"project_type": "self_project"}, {"project_type": "open_source"}]}
        out = clamp_evaluation(self._evaluation(open_source=15), github)
        self.assertEqual(out["scores"]["open_source"]["score"], 15.0)
