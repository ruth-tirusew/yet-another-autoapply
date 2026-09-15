"""Regression test: build_general_resume() must normalize LLM-returned skills.

The LLM sometimes returns `skills` as a flat list of strings instead of JSON
Resume's [{"name": ..., "keywords": [...]}] shape. Left un-normalized, the
cached general resume crashes resume_to_text() (see
tests/test_hiring_agent_bridge.py) for every job matched against it.
"""

from __future__ import annotations

import json
from unittest import mock

from tests.helpers import TempDBTestCase


class BuildGeneralResumeSkillsNormalizationTests(TempDBTestCase):
    def setUp(self):
        super().setUp()
        from src.db import create_user, save_profile

        user = create_user("generalize@test.com", display_name="Generalize")
        self.uid = user["id"]
        save_profile(
            {"basics": {"name": "Test Candidate"}, "skills": [{"name": "Backend", "keywords": ["Python"]}]},
            "/tmp/fake.pdf",
            user_id=self.uid,
        )

    def test_flat_string_skills_from_llm_are_normalized_before_caching(self):
        from src.coaching.generalize_profile import build_general_resume

        llm_response = {
            "resume": {
                "basics": {"name": "Test Candidate"},
                "skills": ["Python", "Go", "Kubernetes"],
            },
            "niche_terms": [],
        }
        with mock.patch(
            "src.coaching.generalize_profile.chat_json", return_value=llm_response
        ):
            result = build_general_resume(self.uid)

        self.assertEqual(
            result.resume["skills"],
            [{"name": "Python"}, {"name": "Go"}, {"name": "Kubernetes"}],
        )

        from src.settings import user_profile_dir

        cached = json.loads(
            (user_profile_dir(self.uid) / "resume_general.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            cached["resume"]["skills"],
            [{"name": "Python"}, {"name": "Go"}, {"name": "Kubernetes"}],
        )


if __name__ == "__main__":
    import unittest

    unittest.main()
