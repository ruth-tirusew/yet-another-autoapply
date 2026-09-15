"""Tests for src/enrich.py (full job-description fetch + ATS-specific extraction)."""

from __future__ import annotations

import unittest
from unittest import mock

from src.enrich import _extract_text
from tests.helpers import TempDBTestCase


class ExtractTextTests(unittest.TestCase):
    def test_greenhouse_selector_extraction(self):
        html = f"<html><body><div id='content'>{'A' * 250}</div></body></html>"
        text = _extract_text(html, "https://boards.greenhouse.io/acme/jobs/123")
        self.assertEqual(text, "A" * 250)

    def test_falls_back_to_body_when_no_selector_matches(self):
        html = "<html><body><script>ignored()</script><p>Job details go here.</p></body></html>"
        text = _extract_text(html, "https://example.com/job/1")
        self.assertIn("Job details go here.", text)
        self.assertNotIn("ignored()", text)

    def test_collapses_excess_blank_lines_in_fallback(self):
        html = "<html><body><p>One</p><p>\n\n\n\n</p><p>Two</p></body></html>"
        text = _extract_text(html, "https://example.com/job/2")
        self.assertNotIn("\n\n\n", text)


class EnrichJobsTests(TempDBTestCase):
    def setUp(self):
        super().setUp()
        from src.db import connect

        with connect() as conn:
            conn.execute(
                """
                INSERT INTO catalog_jobs (url_hash, title, url, description_short, status)
                VALUES ('needs-enrich', 'Engineer', 'https://boards.greenhouse.io/acme/jobs/1', 'short desc', 'active')
                """
            )

    def test_successful_fetch_updates_description_full(self):
        from src.enrich import enrich_jobs

        fake_response = mock.Mock(status_code=200, text="<html><body><div id='content'>" + "B" * 210 + "</div></body></html>")
        with mock.patch("src.enrich.get", return_value=fake_response):
            count = enrich_jobs(limit=10, delay=0)

        self.assertEqual(count, 1)
        from src.catalog_db import get_catalog_job

        updated = get_catalog_job(1)
        self.assertIn("B" * 210, updated["description_full"])

    def test_non_200_response_is_skipped(self):
        from src.enrich import enrich_jobs

        fake_response = mock.Mock(status_code=404, text="")
        with mock.patch("src.enrich.get", return_value=fake_response):
            count = enrich_jobs(limit=10, delay=0)
        self.assertEqual(count, 0)

    def test_exception_during_fetch_does_not_crash(self):
        from src.enrich import enrich_jobs

        with mock.patch("src.enrich.get", side_effect=ConnectionError("boom")):
            count = enrich_jobs(limit=10, delay=0)
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
