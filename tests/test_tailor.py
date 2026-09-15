"""Tests for CV tailoring merge behavior."""

import copy
import unittest


class MergeTailoredTests(unittest.TestCase):
    def setUp(self):
        from src.tailor import merge_tailored

        self.merge = merge_tailored
        self.master = {
            "basics": {"name": "Jane", "summary": "Original summary"},
            "work": None,
            "projects": [{"name": "Project A", "description": "Built APIs"}],
            "skills": [{"name": "Python", "keywords": ["FastAPI"]}],
        }

    def test_preserves_projects_when_llm_omits_them(self):
        merged = self.merge(self.master, {"basics": {"summary": "Tailored summary"}, "work": None})
        self.assertEqual(len(merged["projects"]), 1)
        self.assertEqual(merged["basics"]["summary"], "Tailored summary")

    def test_keeps_master_when_patch_empty(self):
        merged = self.merge(self.master, {})
        self.assertEqual(merged["projects"], self.master["projects"])

    def test_applies_skill_reorder_when_provided(self):
        patch_skills = [{"name": "Go", "keywords": ["backend"]}]
        merged = self.merge(self.master, {"skills": patch_skills})
        self.assertEqual(merged["skills"], patch_skills)

    def test_normalizes_flat_string_skills_from_llm(self):
        merged = self.merge(self.master, {"skills": ["Go", "FastAPI"]})
        self.assertEqual(
            merged["skills"],
            [{"name": "Go"}, {"name": "FastAPI"}],
        )


if __name__ == "__main__":
    unittest.main()
