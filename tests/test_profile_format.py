"""Tests for resume formatting helpers."""

import unittest

from src.profile_format import normalize_skills
from src.render_cv import render_resume_html


class NormalizeSkillsTests(unittest.TestCase):
    def test_string_list_becomes_named_groups(self):
        skills = normalize_skills(["FastAPI", "Go", "GraphQL"])
        self.assertEqual(
            skills,
            [{"name": "FastAPI"}, {"name": "Go"}, {"name": "GraphQL"}],
        )

    def test_json_resume_groups_preserved(self):
        raw = [{"name": "Backend", "keywords": ["FastAPI", "Go"]}]
        self.assertEqual(normalize_skills(raw), raw)

    def test_keywords_only_expanded_to_names(self):
        skills = normalize_skills([{"keywords": ["Docker", "PostgreSQL"]}])
        self.assertEqual(
            skills,
            [{"name": "Docker"}, {"name": "PostgreSQL"}],
        )

    def test_empty_and_null_skipped(self):
        self.assertEqual(normalize_skills(["", "  ", None, 42]), [])

    def test_single_skill_dict_wrapped(self):
        raw = {"name": "Backend", "keywords": ["FastAPI", "Go"]}
        self.assertEqual(normalize_skills(raw), [raw])

    def test_single_skill_string_wrapped(self):
        self.assertEqual(normalize_skills("Python"), [{"name": "Python"}])


class RenderSkillsSectionTests(unittest.TestCase):
    def test_flat_string_skills_render_in_html(self):
        html = render_resume_html(
            {
                "basics": {"name": "Jane"},
                "skills": ["FastAPI", "Go", "GraphQL"],
            }
        )
        self.assertIn("FastAPI", html)
        self.assertIn("Go", html)
        self.assertIn("GraphQL", html)
        self.assertNotIn("; ;", html)


if __name__ == "__main__":
    unittest.main()
