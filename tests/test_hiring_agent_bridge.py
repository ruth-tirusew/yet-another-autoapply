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

from src.hiring_agent_bridge import resume_to_text


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
