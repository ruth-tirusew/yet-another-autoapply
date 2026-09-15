"""Regression tests for src/profile.py::upload_cv surfacing hiring_agent failures.

Previously, extract_resume_from_pdf/evaluate_profile caught every exception
and returned None/{} with only a print() — so a broken Ollama connection, a
bad API key, or a missing dependency all made profile upload look like it
succeeded, with no visible sign that extraction fell back to a weaker path
or that no evaluation score was produced. Fix 3 makes those functions raise
HiringAgentError, and upload_cv now catches it, keeps working (falls back
for extraction, skips scoring for evaluation), and reports what happened
via a "warnings" list instead of staying silent.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("SESSION_SECRET", "test-session-secret-for-unit-tests")


class UploadCvWarningsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        self.db_path = self.tmp_path / "test.db"
        self.data_dir = self.tmp_path / "data"
        self.patcher_db = mock.patch("src.settings.DB_PATH", self.db_path)
        self.patcher_data = mock.patch("src.settings.DATA_DIR", self.data_dir)
        self.patcher_db.start()
        self.patcher_data.start()
        self.patcher_import = mock.patch("src.yaml_import.auto_import_if_empty", return_value=False)
        self.patcher_import.start()
        self.patcher_dirs = mock.patch("src.db.ensure_dirs")
        self.patcher_dirs.start()

        from src.db import create_user, init_db
        from src.tenant import set_tenant_user_id

        init_db()
        user = create_user("upload-warnings@test.com", display_name="Warnings Test")
        self.uid = user["id"]
        set_tenant_user_id(self.uid)

        self.pdf_path = self.tmp_path / "resume.pdf"
        self.pdf_path.write_bytes(b"%PDF-1.4 fake")

        self.resume = {"basics": {"name": "Test Candidate"}, "work": [], "projects": []}

        # Keep these best-effort steps out of the picture — irrelevant to this test.
        self.patcher_github = mock.patch("src.profile.fetch_github_for_resume", return_value={})
        self.patcher_github.start()
        self.patcher_generalize = mock.patch(
            "src.coaching.generalize_profile.build_general_resume",
            side_effect=RuntimeError("generalize skipped in test"),
        )
        self.patcher_generalize.start()

    def tearDown(self):
        mock.patch.stopall()
        self.tmp.cleanup()

    def test_extraction_failure_falls_back_and_warns(self):
        from src.hiring_agent_bridge import HiringAgentError
        from src.profile import upload_cv

        with mock.patch(
            "src.profile.extract_resume_from_pdf", side_effect=HiringAgentError("Ollama unreachable")
        ), mock.patch("src.profile.extract_with_ollama", return_value=self.resume) as fallback, mock.patch(
            "src.profile.evaluate_profile", return_value={"scores": {}}
        ):
            result = upload_cv(str(self.pdf_path), user_id=self.uid)

        fallback.assert_called_once()
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("Ollama unreachable", result["warnings"][0])
        self.assertIn("fallback extractor", result["warnings"][0])

    def test_evaluation_failure_still_saves_profile_and_warns(self):
        from src.db import get_profile
        from src.hiring_agent_bridge import HiringAgentError
        from src.profile import upload_cv

        with mock.patch("src.profile.extract_resume_from_pdf", return_value=self.resume), mock.patch(
            "src.profile.evaluate_profile", side_effect=HiringAgentError("bad Gemini API key")
        ):
            result = upload_cv(str(self.pdf_path), user_id=self.uid)

        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("bad Gemini API key", result["warnings"][0])
        self.assertIn("without a score", result["warnings"][0])
        saved = get_profile(self.uid)
        self.assertIsNotNone(saved)
        self.assertIsNone(saved["evaluation_json"])

    def test_happy_path_has_no_warnings(self):
        from src.profile import upload_cv

        with mock.patch("src.profile.extract_resume_from_pdf", return_value=self.resume), mock.patch(
            "src.profile.evaluate_profile", return_value={"scores": {}}
        ):
            result = upload_cv(str(self.pdf_path), user_id=self.uid)

        self.assertEqual(result["warnings"], [])


if __name__ == "__main__":
    unittest.main()
