"""Tests for dual profile variants (general + niche)."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("SESSION_SECRET", "test-session-secret-for-unit-tests")

FIXTURE_NICHE_TERMS = ["murabaha", "sharia-compliant", "islamic finance"]


class ProfileVariantTests(unittest.TestCase):
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

        from src.catalog_db import migrate_catalog_schema
        from src.db import connect, create_user, init_db, save_profile
        from src.tenant import set_tenant_user_id

        init_db()
        user = create_user("variant@test.com", display_name="Variant")
        self.uid = user["id"]
        set_tenant_user_id(self.uid)
        self.profile_dir = self.data_dir / "users" / str(self.uid) / "profile"
        self.profile_dir.mkdir(parents=True, exist_ok=True)

        self.niche_resume = {
            "basics": {"name": "Test User", "summary": "Murabaha and Sharia-compliant lending on Fineract"},
            "work": [
                {
                    "name": "Bank",
                    "position": "Backend Engineer",
                    "summary": "Built Murabaha loan servicing on Apache Fineract",
                }
            ],
            "projects": [],
        }
        self.general_resume = {
            "basics": {"name": "Test User", "summary": "Backend lending platform engineer"},
            "work": [
                {
                    "name": "Bank",
                    "position": "Backend Engineer",
                    "summary": "Built loan servicing on open-source core banking",
                }
            ],
            "projects": [],
        }
        save_profile(
            self.niche_resume,
            str(self.profile_dir / "source_cv.pdf"),
            user_id=self.uid,
        )
        with connect() as conn:
            migrate_catalog_schema(conn)

        from src.coaching.generalize_profile import CachedGeneralResume, CachedNicheTerms, cache_key
        from src.db import get_profile

        prof = get_profile(self.uid)
        assert prof is not None
        key = cache_key(prof)
        (self.profile_dir / "resume_general.json").write_text(
            json.dumps(
                CachedGeneralResume(
                    cache_key=key,
                    generated_at="2026-06-01T00:00:00Z",
                    resume=self.general_resume,
                ).model_dump()
            ),
            encoding="utf-8",
        )
        (self.profile_dir / "niche_terms.json").write_text(
            json.dumps(
                CachedNicheTerms(
                    cache_key=key,
                    generated_at="2026-06-01T00:00:00Z",
                    terms=FIXTURE_NICHE_TERMS,
                ).model_dump()
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        from src.tenant import set_tenant_user_id

        set_tenant_user_id(None)
        self.patcher_import.stop()
        self.patcher_dirs.stop()
        self.patcher_db.stop()
        self.patcher_data.stop()
        self.tmp.cleanup()

    def test_general_cache_invalidates_on_upload(self):
        from src.coaching.generalize_profile import cache_key, generalize_status
        from src.db import get_profile, save_profile

        prof = get_profile(self.uid)
        assert prof is not None
        status = generalize_status(self.uid)
        self.assertTrue(status["ready"])

        save_profile(
            prof["resume_json"],
            prof["source_pdf_path"],
            prof.get("github_json"),
            prof.get("evaluation_json"),
            user_id=self.uid,
        )
        prof2 = get_profile(self.uid)
        new_key = cache_key(prof2)
        self.assertNotEqual(new_key, status["cache_key"])
        status2 = generalize_status(self.uid)
        self.assertTrue(status2["stale"])

    def test_resolve_resume_picks_general_for_generic_job(self):
        from src.profile import resolve_resume_for_job

        job = {
            "title": "Backend Engineer",
            "description_full": "Build APIs and microservices for a fintech startup.",
        }
        resume = resolve_resume_for_job(job, self.uid)
        self.assertEqual(resume["basics"]["summary"], self.general_resume["basics"]["summary"])

    def test_resolve_resume_picks_niche_when_posting_matches(self):
        from src.profile import resolve_resume_for_job

        job = {
            "title": "Islamic Finance Engineer",
            "description_full": "Murabaha product experience required.",
        }
        resume = resolve_resume_for_job(job, self.uid)
        self.assertEqual(resume["basics"]["summary"], self.niche_resume["basics"]["summary"])

    def _mock_embed(self, texts, **kwargs):
        """Fake embedder: general-resume/generic-posting text -> [1, 0], niche text -> [0, 1]."""
        out = []
        for text in texts:
            lowered = text.lower()
            if "murabaha" in lowered or "sharia" in lowered:
                out.append([0.0, 1.0])
            else:
                out.append([1.0, 0.0])
        return out

    def test_prefilter_uses_general_resume_for_a_generic_job(self):
        """A job that doesn't mention niche terms must be scored against the
        general resume variant — if the niche variant were used instead, its
        chunks would embed to [0, 1] against a [1, 0] posting and the score
        would collapse to ~0, so this fails loudly on a regression."""
        from src.db import upsert_job
        from src.prefilter import prefilter_job

        user_job_id = upsert_job(
            {
                "title": "Backend Engineer",
                "company": "Acme",
                "url": "https://example.com/jobs/general-1",
                "description": "We need a backend engineer to build and maintain APIs and services.",
            },
            source_id="test",
            user_id=self.uid,
        )
        from src.catalog_db import update_catalog_job
        from src.db import get_job

        catalog_job_id = get_job(user_job_id, user_id=self.uid)["catalog_job_id"]
        update_catalog_job(
            catalog_job_id,
            description_full=(
                "Requirements\n- Build and maintain backend APIs and services on open-source core banking\n"
            ),
        )

        with mock.patch("src.matching.vectors.embed_texts", side_effect=self._mock_embed):
            score = prefilter_job(user_job_id, user_id=self.uid)
        self.assertIsNotNone(score)
        assert score is not None
        self.assertGreater(score, 0.8)

    def test_prefilter_uses_niche_resume_when_posting_mentions_it(self):
        from src.db import get_job, upsert_job
        from src.catalog_db import update_catalog_job
        from src.prefilter import prefilter_job

        user_job_id = upsert_job(
            {
                "title": "Islamic Finance Engineer",
                "company": "Acme Bank",
                "url": "https://example.com/jobs/niche-1",
                "description": "Murabaha product engineering role.",
            },
            source_id="test",
            user_id=self.uid,
        )
        catalog_job_id = get_job(user_job_id, user_id=self.uid)["catalog_job_id"]
        update_catalog_job(
            catalog_job_id,
            description_full="Requirements\n- Murabaha and Sharia-compliant lending experience on Fineract\n",
        )

        with mock.patch("src.matching.vectors.embed_texts", side_effect=self._mock_embed):
            score = prefilter_job(user_job_id, user_id=self.uid)
        self.assertIsNotNone(score)
        assert score is not None
        self.assertGreater(score, 0.8)

    def test_matcher_general_variant_lacks_niche_phrasing(self):
        from src.hiring_agent_bridge import resume_to_text
        from src.profile import resolve_resume_for_job

        job = {
            "title": "Platform Engineer",
            "description_full": "Distributed systems and APIs.",
        }
        resume = resolve_resume_for_job(job, self.uid)
        text, _ = resume_to_text(resume)
        self.assertNotIn("Murabaha", text)
        self.assertNotIn("Sharia", text)


if __name__ == "__main__":
    unittest.main()
