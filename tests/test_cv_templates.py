"""Tests for CV template studio."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jinja2 import Template

from src.cv_templates.builtins import BUILTIN_DEFINITIONS, classic_preset, modern_preset
from src.cv_templates.compiler import compile_preset
from src.cv_templates.models import CvTemplatePreset
from src.cv_templates.store import ensure_user_templates, get_default_template, list_templates
from src.profile_format import normalize_skills
from src.render_cv import render_resume_html


SAMPLE_RESUME = {
    "basics": {"name": "Jane", "summary": "Engineer"},
    "skills": [{"name": "Python"}, {"name": "Go"}],
    "work": [{"name": "Co", "position": "Dev", "highlights": ["Shipped APIs"]}],
}


class CompilerTests(unittest.TestCase):
    def test_compile_classic_includes_sections(self):
        html = compile_preset(classic_preset())
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("Experience", html)
        self.assertIn("@media print", html)

    def test_compile_modern_uses_tags_partial(self):
        html = compile_preset(modern_preset())
        self.assertIn("skill-tag", html)

    def test_compiled_template_renders_resume(self):
        html_source = compile_preset(classic_preset())
        html = Template(html_source).render(resume=SAMPLE_RESUME)
        self.assertIn("Jane", html)
        self.assertIn("Python", html)

    def test_hidden_sections_omitted_from_output(self):
        preset = classic_preset()
        preset.sections = [s for s in preset.sections if s.id != "skills"]
        html_source = compile_preset(preset)
        html = Template(html_source).render(resume=SAMPLE_RESUME)
        self.assertNotIn("<h2>Skills</h2>", html)


class BuiltinTests(unittest.TestCase):
    def test_three_builtins_defined(self):
        self.assertEqual(set(BUILTIN_DEFINITIONS.keys()), {"classic", "compact", "modern"})


class TemplateStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        import src.settings as settings_mod

        self._orig_db = settings_mod.DB_PATH
        self._orig_data = settings_mod.DATA_DIR
        settings_mod.DB_PATH = self.tmp_path / "jobs.db"
        settings_mod.DATA_DIR = self.tmp_path

        from src.db import connect, init_db

        init_db()
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO users (id, email, password_hash, display_name, created_at, updated_at)
                VALUES (1, 'test@example.com', 'x', 'Test', '2020-01-01', '2020-01-01')
                """
            )

    def tearDown(self):
        import src.settings as settings_mod

        settings_mod.DB_PATH = self._orig_db
        settings_mod.DATA_DIR = self._orig_data
        self.tmp.cleanup()

    def test_seed_creates_builtin_templates(self):
        templates = ensure_user_templates(1)
        self.assertEqual(len(templates), 3)
        slugs = {t.slug for t in templates}
        self.assertIn("classic", slugs)

    def test_default_is_classic(self):
        ensure_user_templates(1)
        default = get_default_template(1)
        self.assertEqual(default.slug, "classic")

    def test_list_templates_returns_seeded_rows(self):
        ensure_user_templates(1)
        self.assertEqual(len(list_templates(1)), 3)


class RenderCvTemplateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        import src.settings as settings_mod

        self._orig_db = settings_mod.DB_PATH
        self._orig_data = settings_mod.DATA_DIR
        settings_mod.DB_PATH = self.tmp_path / "jobs.db"
        settings_mod.DATA_DIR = self.tmp_path

        from src.db import connect, init_db

        init_db()
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO users (id, email, password_hash, display_name, created_at, updated_at)
                VALUES (1, 'test@example.com', 'x', 'Test', '2020-01-01', '2020-01-01')
                """
            )
        ensure_user_templates(1)

    def tearDown(self):
        import src.settings as settings_mod

        settings_mod.DB_PATH = self._orig_db
        settings_mod.DATA_DIR = self._orig_data
        self.tmp.cleanup()

    def test_render_resume_html_with_default_template(self):
        html = render_resume_html(SAMPLE_RESUME, user_id=1)
        self.assertIn("Jane", html)
        skills = normalize_skills(SAMPLE_RESUME["skills"])
        for skill in skills:
            self.assertIn(skill["name"], html)


if __name__ == "__main__":
    unittest.main()
