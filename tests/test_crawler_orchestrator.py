"""Tests for src/crawler/orchestrator.py, specifically that target companies
actually get crawled.

TargetCompaniesAdapter was registered in the adapter registry but never
added to any source list crawl_all() actually iterates — a user could add
companies on the Targets page and nothing would ever fetch them. These
tests pin down the fix: crawl_all() must include one synthetic source per
user with an enabled target company, and jobs it returns must reach the
shared catalog like any other source.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from tests.helpers import TempDBTestCase


class TargetCompanySourcesTests(TempDBTestCase):
    def test_no_sources_when_no_users_have_target_companies(self):
        from src.crawler.orchestrator import _target_company_sources

        self.assertEqual(_target_company_sources(), [])

    def test_one_source_per_user_with_an_enabled_company(self):
        from src.catalog_db import add_target_company
        from src.crawler.orchestrator import _target_company_sources
        from src.db import create_user

        uid = create_user("targets@test.com", display_name="T")["id"]
        add_target_company(uid, "acme", "greenhouse")

        sources = _target_company_sources()
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["adapter"], "target_companies")
        self.assertEqual(sources[0]["config"]["user_id"], uid)

    def test_disabled_only_company_produces_no_source(self):
        from src.catalog_db import add_target_company, toggle_target_company, list_target_companies
        from src.crawler.orchestrator import _target_company_sources
        from src.db import create_user

        uid = create_user("disabled@test.com", display_name="D")["id"]
        add_target_company(uid, "acme", "greenhouse")
        company_id = list_target_companies(uid)[0]["id"]
        toggle_target_company(company_id, uid, False)

        self.assertEqual(_target_company_sources(), [])


class CrawlAllIncludesTargetCompaniesTests(TempDBTestCase):
    def test_crawl_all_fetches_and_upserts_target_company_jobs(self):
        from src.catalog_db import add_target_company
        from src.db import connect, create_user

        uid = create_user("crawl@test.com", display_name="C")["id"]
        add_target_company(uid, "acme", "greenhouse")

        fake_adapter = MagicMock()
        fake_adapter.fetch.return_value = [
            {
                "title": "Staff Engineer",
                "company": "Acme",
                "location": "Remote",
                "url": "https://boards.greenhouse.io/acme/jobs/1",
                "source_tier": "native",
                "company_slug": "acme",
            }
        ]

        def _get_adapter(source):
            return fake_adapter

        with patch("src.crawler.orchestrator.get_adapter", side_effect=_get_adapter), patch(
            "src.crawler.orchestrator.list_sources", return_value=[]
        ):
            from src.crawler.orchestrator import crawl_all

            jobs = crawl_all(persist=True)

        self.assertEqual(len(jobs), 1)
        with connect() as conn:
            row = conn.execute(
                "SELECT source_tier, company_slug FROM catalog_jobs "
                "WHERE url = 'https://boards.greenhouse.io/acme/jobs/1'"
            ).fetchone()
        self.assertEqual(row["source_tier"], "native")
        self.assertEqual(row["company_slug"], "acme")


if __name__ == "__main__":
    import unittest

    unittest.main()
