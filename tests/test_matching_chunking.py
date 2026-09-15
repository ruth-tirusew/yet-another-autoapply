"""Chunking and posting-section extraction."""

from __future__ import annotations

import unittest

from src.matching.chunking import chunk_resume, heuristic_requirements, requirement_section

POSTING = """About Acme
We are a fast growing company and we have been building payments for 12 years.

What you'll need
- 5+ years of professional Python experience
- Experience running services on Kubernetes
- Strong SQL skills

Nice to have
- Go
- Terraform is a plus

Benefits
- Unlimited PTO for 10 years of service
- Equity

Equal Opportunity
Acme is an equal opportunity employer.
"""


class RequirementSectionTests(unittest.TestCase):
    def test_section_starts_at_requirements_heading_and_stops_at_benefits(self):
        section = requirement_section(POSTING)
        self.assertIn("5+ years of professional Python experience", section)
        self.assertIn("Terraform is a plus", section)
        self.assertNotIn("Unlimited PTO", section)
        self.assertNotIn("equal opportunity employer", section)
        self.assertNotIn("fast growing company", section)

    def test_section_falls_back_to_whole_description(self):
        text = "We need someone who knows Python."
        self.assertEqual(requirement_section(text), text)

    def test_empty_description(self):
        self.assertEqual(requirement_section(""), "")


class HeuristicRequirementTests(unittest.TestCase):
    def test_bullets_split_by_must_and_nice(self):
        pairs = heuristic_requirements(POSTING)
        musts = [text for text, kind in pairs if kind == "must"]
        nices = [text for text, kind in pairs if kind == "nice"]
        self.assertIn("5+ years of professional Python experience", musts)
        self.assertIn("Strong SQL skills", musts)
        self.assertIn("Go", nices)
        self.assertIn("Terraform is a plus", nices)
        self.assertNotIn("Equity", musts + nices)

    def test_limit_is_respected(self):
        self.assertLessEqual(len(heuristic_requirements(POSTING, limit=2)), 2)


class ChunkResumeTests(unittest.TestCase):
    def setUp(self):
        self.resume = {
            "basics": {"summary": "Backend engineer with API experience"},
            "work": [
                {
                    "position": "Senior Engineer",
                    "name": "Acme",
                    "startDate": "2020-01",
                    "endDate": "",
                    "highlights": ["Built Django services", "Ran Kubernetes clusters"],
                }
            ],
            "projects": [{"name": "crawler", "description": "Async scraper in Python"}],
            "skills": [{"name": "Backend", "keywords": ["python", "django", "sql"]}],
            "education": [{"studyType": "BSc", "area": "CS", "institution": "Uni"}],
        }

    def test_one_chunk_per_experience_unit(self):
        chunks = chunk_resume(self.resume)
        kinds = [c.kind for c in chunks]
        self.assertIn("summary", kinds)
        self.assertIn("work", kinds)
        self.assertIn("project", kinds)
        self.assertIn("skills", kinds)
        self.assertIn("education", kinds)
        self.assertEqual(len({c.id for c in chunks}), len(chunks), "chunk ids must be unique")

    def test_work_chunk_carries_highlights_and_a_readable_ref(self):
        work = next(c for c in chunk_resume(self.resume) if c.kind == "work")
        self.assertIn("Kubernetes", work.text)
        self.assertIn("Senior Engineer", work.ref)
        self.assertIn("Acme", work.ref)

    def test_empty_resume_yields_no_chunks(self):
        self.assertEqual(chunk_resume({}), [])
        self.assertEqual(chunk_resume(None), [])


if __name__ == "__main__":
    unittest.main()
