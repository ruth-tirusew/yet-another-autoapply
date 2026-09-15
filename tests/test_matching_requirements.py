"""Stage-1 extraction: caching on the catalog row and invalidation."""

from __future__ import annotations

from unittest import mock

from src.matching.requirements import (
    extract_requirements,
    heuristic_extract,
    load_cached,
    source_hash,
)
from tests.helpers import TempDBTestCase

POSTING = """About us
We have been around for 15 years.

Requirements
- 5+ years of Python experience
- Experience with Kubernetes

Nice to have
- Go

Benefits
- Snacks
"""

LLM_RESPONSE = {
    "seniority": "senior",
    "years_required": 5,
    "requirements": [
        {"text": "5+ years of Python experience", "kind": "must", "category": "experience", "terms": ["python"]},
        {"text": "Experience with Kubernetes", "kind": "must", "category": "skill", "terms": ["kubernetes"]},
        {"text": "Go", "kind": "nice", "category": "skill", "terms": ["go"]},
    ],
}


class RequirementExtractionTests(TempDBTestCase):
    def setUp(self):
        super().setUp()
        from src.db import upsert_job

        self.catalog_id = upsert_job(
            {
                "title": "Senior Backend Engineer",
                "company": "Acme",
                "url": "https://example.com/jobs/1",
                "description_full": POSTING,
            },
            source_id="test",
        )
        self.job = {
            "id": 1,
            "catalog_job_id": self.catalog_id,
            "title": "Senior Backend Engineer",
            "company": "Acme",
            "description_full": POSTING,
        }

    def _extract(self, response=LLM_RESPONSE):
        return self._extract_job(self.job, response)

    def _extract_job(self, job, response=LLM_RESPONSE):
        # Patched where requirements.py looks these up (imported at module
        # level), not where src.llm/src.config_store define them.
        with mock.patch("src.matching.requirements.chat_json", return_value=response), mock.patch(
            "src.matching.requirements.get_merged_config", return_value={"quality_model": "test-model"}
        ):
            return extract_requirements(job)

    def test_extraction_is_cached_on_the_catalog_row(self):
        req_set = self._extract()
        self.assertEqual(len(req_set.requirements), 3)
        self.assertEqual(req_set.seniority, "senior")
        self.assertEqual(req_set.years_required, 5)
        cached = load_cached(self.catalog_id, source_hash(self.job))
        self.assertIsNotNone(cached)
        self.assertEqual(len(cached.requirements), 3)

    def test_second_call_reuses_the_cache_instead_of_calling_the_model(self):
        self._extract()
        chat = mock.Mock()
        with mock.patch("src.matching.requirements.chat_json", chat):
            again = extract_requirements(self.job)
        chat.assert_not_called()
        self.assertEqual(len(again.requirements), 3)

    def test_enrichment_of_a_truncated_snippet_invalidates_the_cache(self):
        """A job first seen as a 350-char crawl snippet must be re-extracted once enriched."""
        snippet_job = dict(self.job, description_full="", description_short="Senior Backend Engineer at Acme. Apply now.")
        self._extract_job(snippet_job)
        self.assertIsNotNone(load_cached(self.catalog_id, source_hash(snippet_job)))

        chat = mock.Mock(return_value=LLM_RESPONSE)
        with mock.patch("src.matching.requirements.chat_json", chat), mock.patch(
            "src.matching.requirements.get_merged_config", return_value={"quality_model": "test-model"}
        ):
            extract_requirements(self.job)  # same catalog row, now with the full description
        chat.assert_called_once()

    def test_unchanged_boilerplate_does_not_invalidate_the_cache(self):
        """Only the requirements section is hashed, so benefits edits are free."""
        self._extract()
        chat = mock.Mock()
        with mock.patch("src.matching.requirements.chat_json", chat):
            extract_requirements(dict(self.job, description_full=POSTING + "\n- Gym membership\n"))
        chat.assert_not_called()

    def test_model_failure_degrades_to_bullet_heuristics(self):
        with mock.patch("src.matching.requirements.chat_json", side_effect=RuntimeError("ollama down")), mock.patch(
            "src.matching.requirements.get_merged_config", return_value={"quality_model": "test-model"}
        ):
            req_set = extract_requirements(self.job)
        self.assertEqual(req_set.engine, "heuristic")
        texts = [r.text for r in req_set.requirements]
        self.assertIn("5+ years of Python experience", texts)
        self.assertIn("Go", texts)

    def test_heuristic_results_are_not_cached_so_a_later_run_can_upgrade(self):
        with mock.patch("src.matching.requirements.chat_json", side_effect=RuntimeError("down")), mock.patch(
            "src.matching.requirements.get_merged_config", return_value={"quality_model": "test-model"}
        ):
            extract_requirements(self.job)
        self.assertIsNone(load_cached(self.catalog_id, source_hash(self.job)))

    def test_duplicate_requirements_are_collapsed_and_ids_assigned(self):
        response = dict(
            LLM_RESPONSE,
            requirements=[
                {"text": "Experience with Kubernetes", "kind": "must"},
                {"text": "experience with kubernetes", "kind": "must"},
            ],
        )
        req_set = self._extract(response)
        self.assertEqual(len(req_set.requirements), 1)
        self.assertEqual(req_set.requirements[0].id, "r1")

    def test_heuristic_extract_reads_years_and_seniority(self):
        req_set = heuristic_extract(self.job)
        self.assertEqual(req_set.years_required, 5)
        self.assertEqual(req_set.seniority, "senior")
        self.assertEqual(req_set.engine, "heuristic")
