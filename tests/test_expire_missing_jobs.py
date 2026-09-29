"""Tests for 0d: marking a job 'expired' once a company's own native
listing no longer includes it.

Two layers are tested:

1. catalog_db.expire_missing_catalog_jobs itself — given a confirmed set
   of currently-listed URLs, it marks anything else for that company
   'expired' and demotes any still-'new'/'scored' user_jobs row to 'stale'.
2. The native adapters' success/failure tracking (company_status) — the
   thing that makes it safe to call step 1 at all. A failed fetch must
   never look like "the company closed every job".
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from tests.helpers import TempDBTestCase


class ExpireMissingCatalogJobsTests(TempDBTestCase):
    def _seed(self, url_hash, url, *, ats_type="greenhouse", company_slug="acme",
              source_tier="native", status="active"):
        from src.db import connect

        with connect() as conn:
            return conn.execute(
                "INSERT INTO catalog_jobs (url_hash, url, title, company, ats_type, "
                "company_slug, source_tier, status) VALUES (?, ?, 'Engineer', 'Acme', ?, ?, ?, ?)",
                (url_hash, url, ats_type, company_slug, source_tier, status),
            ).lastrowid

    def test_marks_missing_job_expired(self):
        from src.catalog_db import expire_missing_catalog_jobs
        from src.db import connect

        cat_id = self._seed("h1", "https://boards.greenhouse.io/acme/jobs/1")

        expired = expire_missing_catalog_jobs("greenhouse", "acme", current_urls=set())
        self.assertEqual(expired, 1)

        with connect() as conn:
            status = conn.execute("SELECT status FROM catalog_jobs WHERE id = ?", (cat_id,)).fetchone()["status"]
        self.assertEqual(status, "expired")

    def test_keeps_still_listed_job_active(self):
        from src.catalog_db import expire_missing_catalog_jobs
        from src.db import connect

        url = "https://boards.greenhouse.io/acme/jobs/1"
        cat_id = self._seed("h1", url)

        expired = expire_missing_catalog_jobs("greenhouse", "acme", current_urls={url})
        self.assertEqual(expired, 0)

        with connect() as conn:
            status = conn.execute("SELECT status FROM catalog_jobs WHERE id = ?", (cat_id,)).fetchone()["status"]
        self.assertEqual(status, "active")

    def test_never_touches_a_different_company(self):
        from src.catalog_db import expire_missing_catalog_jobs
        from src.db import connect

        other_id = self._seed("h1", "https://boards.greenhouse.io/other/jobs/1", company_slug="other")

        expire_missing_catalog_jobs("greenhouse", "acme", current_urls=set())

        with connect() as conn:
            status = conn.execute("SELECT status FROM catalog_jobs WHERE id = ?", (other_id,)).fetchone()["status"]
        self.assertEqual(status, "active")

    def test_never_touches_an_aggregator_sourced_row(self):
        """An aggregator row for a company also on the native watchlist
        must not be expired by the native side's listing — only native
        rows are ever authoritative for "still open"."""
        from src.catalog_db import expire_missing_catalog_jobs
        from src.db import connect

        agg_id = self._seed("h1", "https://someboard.com/acme/1", source_tier="aggregator")

        expire_missing_catalog_jobs("greenhouse", "acme", current_urls=set())

        with connect() as conn:
            status = conn.execute("SELECT status FROM catalog_jobs WHERE id = ?", (agg_id,)).fetchone()["status"]
        self.assertEqual(status, "active")

    def test_demotes_new_and_scored_user_jobs_but_not_applied(self):
        from src.catalog_db import expire_missing_catalog_jobs
        from src.db import connect, create_user

        uid = create_user("expire@test.com", display_name="E")["id"]
        new_cat = self._seed("h1", "https://boards.greenhouse.io/acme/jobs/1")
        applied_cat = self._seed("h2", "https://boards.greenhouse.io/acme/jobs/2")
        with connect() as conn:
            conn.execute(
                "INSERT INTO user_jobs (user_id, catalog_job_id, status) VALUES (?, ?, 'new')",
                (uid, new_cat),
            )
            conn.execute(
                "INSERT INTO user_jobs (user_id, catalog_job_id, status) VALUES (?, ?, 'applied')",
                (uid, applied_cat),
            )

        expire_missing_catalog_jobs("greenhouse", "acme", current_urls=set())

        with connect() as conn:
            new_status = conn.execute(
                "SELECT status FROM user_jobs WHERE catalog_job_id = ?", (new_cat,)
            ).fetchone()["status"]
            applied_status = conn.execute(
                "SELECT status FROM user_jobs WHERE catalog_job_id = ?", (applied_cat,)
            ).fetchone()["status"]
        self.assertEqual(new_status, "stale")
        self.assertEqual(applied_status, "applied", "a real application must never be invalidated")


class AdapterCompanyStatusTests(TempDBTestCase):
    """A non-200 or an exception must report failure, not an empty-but-ok
    result, so TargetCompaniesAdapter never expires anything off a failed
    fetch. Table-driven across all 5 native adapters."""

    def _adapter_cases(self):
        from src.crawler.adapters.ashby import AshbyAdapter
        from src.crawler.adapters.greenhouse import GreenhouseAdapter
        from src.crawler.adapters.lever import LeverAdapter
        from src.crawler.adapters.smartrecruiters import SmartRecruitersAdapter
        from src.crawler.adapters.workable import WorkableAdapter

        return [
            (GreenhouseAdapter, "src.crawler.adapters.greenhouse.get"),
            (LeverAdapter, "src.crawler.adapters.lever.get"),
            (AshbyAdapter, "src.crawler.adapters.ashby.get"),
            (WorkableAdapter, "src.crawler.adapters.workable.get"),
            (SmartRecruitersAdapter, "src.crawler.adapters.smartrecruiters.get"),
        ]

    def test_successful_fetch_with_zero_jobs_is_still_marked_ok(self):
        for cls, get_path in self._adapter_cases():
            with self.subTest(cls=cls.__name__):
                resp = MagicMock()
                resp.status_code = 200
                resp.json.return_value = {"jobs": [], "content": [], "data": []}
                adapter = cls({"company_slug": "acme"})
                with patch(get_path, return_value=resp):
                    adapter.fetch()
                self.assertTrue(
                    adapter.company_status.get("acme"),
                    f"{cls.__name__}: a clean 200 with zero jobs must report success",
                )

    def test_non_200_is_marked_failed(self):
        for cls, get_path in self._adapter_cases():
            with self.subTest(cls=cls.__name__):
                resp = MagicMock()
                resp.status_code = 404
                adapter = cls({"company_slug": "acme"})
                with patch(get_path, return_value=resp):
                    adapter.fetch()
                self.assertFalse(
                    adapter.company_status.get("acme", False),
                    f"{cls.__name__}: a non-200 response must report failure",
                )

    def test_exception_is_marked_failed(self):
        for cls, get_path in self._adapter_cases():
            with self.subTest(cls=cls.__name__):
                adapter = cls({"company_slug": "acme"})
                with patch(get_path, side_effect=RuntimeError("boom")):
                    adapter.fetch()
                self.assertFalse(
                    adapter.company_status.get("acme", False),
                    f"{cls.__name__}: a request exception must report failure",
                )


class ExpireMissingCatalogJobsDemotionScopeTests(TempDBTestCase):
    """0e: closing a posting must also pull an 'approved'/'queued' user_jobs
    row off the active queue (not just 'new'/'scored'), and must record
    *why* the status changed via classified_by."""

    def _seed(self, url_hash, url, *, ats_type="greenhouse", company_slug="acme",
              source_tier="native", status="active"):
        from src.db import connect

        with connect() as conn:
            return conn.execute(
                "INSERT INTO catalog_jobs (url_hash, url, title, company, ats_type, "
                "company_slug, source_tier, status) VALUES (?, ?, 'Engineer', 'Acme', ?, ?, ?, ?)",
                (url_hash, url, ats_type, company_slug, source_tier, status),
            ).lastrowid

    def test_demotes_approved_and_queued_but_not_needs_verification(self):
        from src.catalog_db import expire_missing_catalog_jobs
        from src.db import connect, create_user

        uid = create_user("demote@test.com", display_name="D")["id"]
        cats = {
            status: self._seed(f"h-{status}", f"https://boards.greenhouse.io/acme/jobs/{status}")
            for status in ("approved", "queued", "needs_verification")
        }
        with connect() as conn:
            for status, cat_id in cats.items():
                conn.execute(
                    "INSERT INTO user_jobs (user_id, catalog_job_id, status) VALUES (?, ?, ?)",
                    (uid, cat_id, status),
                )

        expire_missing_catalog_jobs("greenhouse", "acme", current_urls=set())

        with connect() as conn:
            statuses = {
                status: conn.execute(
                    "SELECT status FROM user_jobs WHERE catalog_job_id = ?", (cat_id,)
                ).fetchone()["status"]
                for status, cat_id in cats.items()
            }
        self.assertEqual(statuses["approved"], "stale")
        self.assertEqual(statuses["queued"], "stale")
        self.assertEqual(
            statuses["needs_verification"], "needs_verification",
            "a job with an apply attempt in flight must never be yanked out from under it",
        )

    def test_sets_classified_by_closed_on_demotion(self):
        from src.catalog_db import expire_missing_catalog_jobs
        from src.db import connect, create_user

        uid = create_user("closed@test.com", display_name="C")["id"]
        cat_id = self._seed("h1", "https://boards.greenhouse.io/acme/jobs/1")
        with connect() as conn:
            conn.execute(
                "INSERT INTO user_jobs (user_id, catalog_job_id, status) VALUES (?, ?, 'new')",
                (uid, cat_id),
            )

        expire_missing_catalog_jobs("greenhouse", "acme", current_urls=set())

        with connect() as conn:
            row = conn.execute(
                "SELECT status, classified_by FROM user_jobs WHERE catalog_job_id = ?", (cat_id,)
            ).fetchone()
        self.assertEqual(row["status"], "stale")
        self.assertEqual(row["classified_by"], "closed")

    def test_url_hash_comparison_survives_a_cosmetic_url_difference(self):
        """A stored URL and the fresh fetch's URL that only differ by a
        tracking param or trailing slash must still compare equal — see
        normalize_url. Comparing raw strings would wrongly expire this."""
        from src.catalog_db import expire_missing_catalog_jobs
        from src.db import connect

        cat_id = self._seed("h1", "https://boards.greenhouse.io/acme/jobs/1?utm_source=li")

        expired = expire_missing_catalog_jobs(
            "greenhouse", "acme", current_urls={"https://boards.greenhouse.io/acme/jobs/1/"}
        )
        self.assertEqual(expired, 0)

        with connect() as conn:
            status = conn.execute("SELECT status FROM catalog_jobs WHERE id = ?", (cat_id,)).fetchone()["status"]
        self.assertEqual(status, "active")


class MarkCatalogStaleClassificationTests(TempDBTestCase):
    def test_stale_timeout_demotes_approved_and_tags_classified_by(self):
        from src.catalog_db import mark_catalog_stale
        from src.db import connect, create_user
        from datetime import datetime, timedelta

        uid = create_user("timeout@test.com", display_name="T")["id"]
        old = (datetime.utcnow() - timedelta(days=90)).isoformat()
        with connect() as conn:
            cat_id = conn.execute(
                "INSERT INTO catalog_jobs (url_hash, url, title, company, ats_type, "
                "status, last_seen) VALUES ('h1', 'https://x.test/1', 'Engineer', 'Acme', "
                "'greenhouse', 'active', ?)",
                (old,),
            ).lastrowid
            conn.execute(
                "INSERT INTO user_jobs (user_id, catalog_job_id, status) VALUES (?, ?, 'approved')",
                (uid, cat_id),
            )

        mark_catalog_stale(days=30)

        with connect() as conn:
            row = conn.execute(
                "SELECT status, classified_by FROM user_jobs WHERE catalog_job_id = ?", (cat_id,)
            ).fetchone()
        self.assertEqual(row["status"], "stale")
        self.assertEqual(row["classified_by"], "stale_timeout")


class TargetCompaniesAdapterExpiryIntegrationTests(TempDBTestCase):
    def test_expires_only_after_a_successful_fetch(self):
        from src.catalog_db import add_target_company
        from src.crawler.adapters.target_companies import TargetCompaniesAdapter
        from src.db import connect, create_user

        uid = create_user("integration@test.com", display_name="I")["id"]
        add_target_company(uid, "acme", "greenhouse")
        with connect() as conn:
            gone_id = conn.execute(
                "INSERT INTO catalog_jobs (url_hash, url, title, company, ats_type, "
                "company_slug, source_tier, status) VALUES "
                "('h1', 'https://boards.greenhouse.io/acme/jobs/gone', 'Gone Role', 'Acme', "
                "'greenhouse', 'acme', 'native', 'active')"
            ).lastrowid

        fake_adapter = MagicMock()
        fake_adapter.fetch.return_value = []  # successful fetch, zero current jobs
        fake_adapter.company_status = {"acme": True}

        with patch(
            "src.crawler.adapters.target_companies._ATS_ADAPTERS",
            {"greenhouse": MagicMock(return_value=fake_adapter)},
        ):
            TargetCompaniesAdapter({"config": {"user_id": uid}}).fetch()

        with connect() as conn:
            status = conn.execute("SELECT status FROM catalog_jobs WHERE id = ?", (gone_id,)).fetchone()["status"]
        self.assertEqual(status, "expired")

    def test_does_not_expire_after_a_failed_fetch(self):
        from src.catalog_db import add_target_company
        from src.crawler.adapters.target_companies import TargetCompaniesAdapter
        from src.db import connect, create_user

        uid = create_user("failed@test.com", display_name="F")["id"]
        add_target_company(uid, "acme", "greenhouse")
        with connect() as conn:
            still_here_id = conn.execute(
                "INSERT INTO catalog_jobs (url_hash, url, title, company, ats_type, "
                "company_slug, source_tier, status) VALUES "
                "('h1', 'https://boards.greenhouse.io/acme/jobs/1', 'Role', 'Acme', "
                "'greenhouse', 'acme', 'native', 'active')"
            ).lastrowid

        fake_adapter = MagicMock()
        fake_adapter.fetch.return_value = []  # empty — but the fetch FAILED, not "zero jobs"
        fake_adapter.company_status = {"acme": False}

        with patch(
            "src.crawler.adapters.target_companies._ATS_ADAPTERS",
            {"greenhouse": MagicMock(return_value=fake_adapter)},
        ):
            TargetCompaniesAdapter({"config": {"user_id": uid}}).fetch()

        with connect() as conn:
            status = conn.execute(
                "SELECT status FROM catalog_jobs WHERE id = ?", (still_here_id,)
            ).fetchone()["status"]
        self.assertEqual(status, "active", "a failed fetch must never expire anything")

    def test_a_job_filtered_out_by_relevance_is_not_expired(self):
        """A real adapter drops an irrelevant/wrong-location job before
        returning it from fetch(), but it must still count as "still
        listed" for expiry — comparing only against the filtered jobs list
        would otherwise mark a job that's merely irrelevant as 'closed'."""
        from src.catalog_db import add_target_company
        from src.crawler.adapters.greenhouse import GreenhouseAdapter
        from src.crawler.adapters.target_companies import TargetCompaniesAdapter
        from src.db import connect, create_user

        uid = create_user("filtered@test.com", display_name="F")["id"]
        add_target_company(uid, "acme", "greenhouse")
        with connect() as conn:
            still_open_id = conn.execute(
                "INSERT INTO catalog_jobs (url_hash, url, title, company, ats_type, "
                "company_slug, source_tier, status) VALUES "
                "('h1', 'https://boards.greenhouse.io/acme/jobs/1', 'Sales Rep', 'Acme', "
                "'greenhouse', 'acme', 'native', 'active')"
            ).lastrowid

        resp = MagicMock()
        resp.status_code = 200
        # "Sales Rep" is filtered out by is_relevant, so GreenhouseAdapter's
        # own fetch() return value never mentions this URL at all.
        resp.json.return_value = {
            "jobs": [{
                "title": "Sales Rep",
                "location": {"name": "Remote"},
                "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
                "id": 1,
            }]
        }

        with patch(
            "src.crawler.adapters.target_companies._ATS_ADAPTERS",
            {"greenhouse": GreenhouseAdapter},
        ), patch("src.crawler.adapters.greenhouse.get", return_value=resp):
            TargetCompaniesAdapter({"config": {"user_id": uid}}).fetch()

        with connect() as conn:
            status = conn.execute(
                "SELECT status FROM catalog_jobs WHERE id = ?", (still_open_id,)
            ).fetchone()["status"]
        self.assertEqual(
            status, "active",
            "a job dropped by the relevance/location filter is still listed and must not expire",
        )


class TargetCompaniesAdapterPersistGateTests(TempDBTestCase):
    def test_persist_false_never_expires_anything(self):
        from src.catalog_db import add_target_company
        from src.crawler.adapters.target_companies import TargetCompaniesAdapter
        from src.db import connect, create_user

        uid = create_user("dryrun@test.com", display_name="R")["id"]
        add_target_company(uid, "acme", "greenhouse")
        with connect() as conn:
            gone_id = conn.execute(
                "INSERT INTO catalog_jobs (url_hash, url, title, company, ats_type, "
                "company_slug, source_tier, status) VALUES "
                "('h1', 'https://boards.greenhouse.io/acme/jobs/gone', 'Gone Role', 'Acme', "
                "'greenhouse', 'acme', 'native', 'active')"
            ).lastrowid

        fake_adapter = MagicMock()
        fake_adapter.fetch.return_value = []
        fake_adapter.company_status = {"acme": True}

        with patch(
            "src.crawler.adapters.target_companies._ATS_ADAPTERS",
            {"greenhouse": MagicMock(return_value=fake_adapter)},
        ):
            TargetCompaniesAdapter({"config": {"user_id": uid, "persist": False}}).fetch()

        with connect() as conn:
            status = conn.execute("SELECT status FROM catalog_jobs WHERE id = ?", (gone_id,)).fetchone()["status"]
        self.assertEqual(status, "active", "a dry-run crawl must not expire anything")


if __name__ == "__main__":
    import unittest

    unittest.main()
