"""Tests for CV draft preview and apply."""

import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from src.coaching.cv_preview import (
    CvDraftCache,
    apply_cv_draft,
    build_cv_draft,
    cv_draft_status,
    load_cv_draft,
    save_cv_draft,
)
from src.coaching.profile_guide import ProfileGuideReport, save_cached_guide


SAMPLE_RESUME = {
    "basics": {"name": "Jane Doe", "summary": "Engineer"},
    "work": [{"name": "Acme", "position": "Dev", "summary": "Built APIs"}],
    "projects": [{"name": "API", "url": "https://github.com/jane/api"}],
}


SAMPLE_PROFILE = {
    "resume_json": SAMPLE_RESUME,
    "source_pdf_path": "/tmp/source_cv.pdf",
    "github_json": {},
    "updated_at": "2026-06-17T12:00:00",
}


class CvDraftCacheTests(unittest.TestCase):
    def test_draft_invalidates_when_guide_key_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile_dir = Path(tmp) / "profile"
            profile_dir.mkdir(parents=True)
            uid = 1
            guide_key_a = "key-a"
            guide_key_b = "key-b"

            with patch("src.coaching.cv_preview.user_profile_dir", return_value=profile_dir):
                save_cv_draft(uid, guide_key_a, SAMPLE_RESUME)

                with patch("src.coaching.cv_preview._guide_cache_key", return_value=guide_key_a):
                    status = cv_draft_status(uid)
                self.assertTrue(status["ready"])

                with patch("src.coaching.cv_preview._guide_cache_key", return_value=guide_key_b):
                    status = cv_draft_status(uid)
                self.assertFalse(status["ready"])
                self.assertTrue(status["stale"])


class ApplyCvDraftTests(unittest.TestCase):
    def test_apply_backs_up_and_saves_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile_dir = Path(tmp) / "profile"
            profile_dir.mkdir(parents=True)
            resume_path = profile_dir / "resume.json"
            resume_path.write_text(json.dumps(SAMPLE_RESUME), encoding="utf-8")
            pdf_path = profile_dir / "source_cv.pdf"
            pdf_path.write_text("pdf", encoding="utf-8")

            draft_resume = {
                **SAMPLE_RESUME,
                "basics": {"name": "Jane Doe", "summary": "Improved summary"},
            }
            guide_key = "test-key"
            cached = CvDraftCache(
                guide_cache_key=guide_key,
                generated_at="2026-06-17T12:00:00",
                resume=draft_resume,
            )
            (profile_dir / "cv_draft.json").write_text(
                cached.model_dump_json(), encoding="utf-8"
            )

            uid = 1
            prof = {
                **SAMPLE_PROFILE,
                "source_pdf_path": str(pdf_path),
            }

            with (
                patch("src.coaching.cv_preview.user_profile_dir", return_value=profile_dir),
                patch("src.coaching.cv_preview._guide_cache_key", return_value=guide_key),
                patch("src.coaching.cv_preview.get_profile", return_value=prof),
                patch("src.coaching.cv_preview.save_profile") as mock_save,
                patch(
                    "src.coaching.cv_preview.render_resume_pdf",
                    return_value=pdf_path,
                ),
            ):
                result = apply_cv_draft(uid)

            self.assertTrue(result["applied"])
            history_dirs = list((profile_dir / "history").iterdir())
            self.assertEqual(len(history_dirs), 1)
            backed_up = json.loads((history_dirs[0] / "resume.json").read_text())
            self.assertEqual(backed_up["basics"]["summary"], "Engineer")

            saved = json.loads(resume_path.read_text())
            self.assertEqual(saved["basics"]["summary"], "Improved summary")
            mock_save.assert_called_once()


class BuildCvDraftTests(unittest.TestCase):
    def test_build_without_llm_uses_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile_dir = Path(tmp) / "profile"
            profile_dir.mkdir(parents=True)
            uid = 1
            freq = Counter()
            key = "abc"

            with (
                patch("src.coaching.cv_preview.user_profile_dir", return_value=profile_dir),
                patch("src.coaching.cv_preview.get_profile", return_value=SAMPLE_PROFILE),
                patch("src.coaching.cv_preview._guide_cache_key", return_value=key),
                patch("src.coaching.cv_preview.aggregate_gaps", return_value=freq),
                patch("src.coaching.cv_preview.get_applicant", return_value={}),
                patch("src.coaching.profile_guide.user_profile_dir", return_value=profile_dir),
            ):
                save_cached_guide(
                    uid,
                    key,
                    ProfileGuideReport(
                        cv_tips=[{"title": "Improve summary", "detail": "Lead with impact."}]
                    ),
                )
                result = build_cv_draft(uid, use_llm=False)
                self.assertEqual(result["basics"]["name"], "Jane Doe")
                loaded = load_cv_draft(uid)
                assert loaded is not None
                self.assertEqual(loaded.guide_cache_key, key)


if __name__ == "__main__":
    unittest.main()
