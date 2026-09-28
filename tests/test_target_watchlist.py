"""Tests for the hybrid target-company watchlist (catalog_db.sync_target_company_watchlist
and the manual/auto CRUD helpers it builds on).

Design recap: a company is auto-added once >=2 of its jobs score at or
above match_threshold, or 1 is applied to — only among source_tier='native'
catalog rows, since only a native ATS fetch reliably knows the company's
slug. A person's own additions (or a pin) are 'manual' and never touched by
auto-add/prune. Removing an auto-added company blocklists it so it's never
re-added; a routine auto-prune does not blocklist, since the company might
start matching well again later.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from tests.helpers import TempDBTestCase


class WatchlistHelpersTestCase(TempDBTestCase):
    def setUp(self):
        super().setUp()
        from src.db import connect, create_user

        self.uid = create_user("watchlist@test.com", display_name="W")["id"]
        self.connect = connect

    def _seed_job(self, *, company_slug, ats_type="greenhouse", company="Acme",
                  status="scored", match_score=None, source_tier="native", url=None):
        with self.connect() as conn:
            uh = url or f"https://x/{company_slug}/{status}/{match_score}/{len(company_slug)}-{id(object())}"
            cat_id = conn.execute(
                "INSERT INTO catalog_jobs (url_hash, url, title, company, company_slug, ats_type, "
                "source_tier, status) VALUES (?, ?, 'Engineer', ?, ?, ?, ?, 'active')",
                (uh, uh, company, company_slug, ats_type, source_tier),
            ).lastrowid
            conn.execute(
                "INSERT INTO user_jobs (user_id, catalog_job_id, status, match_score) VALUES (?, ?, ?, ?)",
                (self.uid, cat_id, status, match_score),
            )
        return cat_id


class AutoAddTests(WatchlistHelpersTestCase):
    def test_adds_company_with_two_jobs_at_or_above_threshold(self):
        from src.catalog_db import sync_target_company_watchlist, list_target_companies

        self._seed_job(company_slug="acme", status="scored", match_score=80)
        self._seed_job(company_slug="acme", status="scored", match_score=90)

        result = sync_target_company_watchlist(self.uid, threshold=70)
        self.assertEqual(result["added"], 1)

        companies = list_target_companies(self.uid)
        self.assertEqual(len(companies), 1)
        self.assertEqual(companies[0]["company_slug"], "acme")
        self.assertEqual(companies[0]["origin"], "auto")

    def test_does_not_add_with_only_one_good_job(self):
        from src.catalog_db import sync_target_company_watchlist

        self._seed_job(company_slug="acme", status="scored", match_score=90)

        result = sync_target_company_watchlist(self.uid, threshold=70)
        self.assertEqual(result["added"], 0)

    def test_adds_company_with_a_single_applied_job(self):
        from src.catalog_db import sync_target_company_watchlist

        self._seed_job(company_slug="acme", status="applied", match_score=None)

        result = sync_target_company_watchlist(self.uid, threshold=70)
        self.assertEqual(result["added"], 1)

    def test_ignores_aggregator_sourced_jobs(self):
        """An aggregator posting has no reliable company_slug to re-crawl by."""
        from src.catalog_db import sync_target_company_watchlist

        self._seed_job(company_slug="acme", status="scored", match_score=80, source_tier="aggregator")
        self._seed_job(company_slug="acme", status="scored", match_score=90, source_tier="aggregator")

        result = sync_target_company_watchlist(self.uid, threshold=70)
        self.assertEqual(result["added"], 0)

    def test_never_re_adds_a_blocklisted_company(self):
        from src.catalog_db import block_target_company, sync_target_company_watchlist

        block_target_company(self.uid, "acme", "greenhouse")
        self._seed_job(company_slug="acme", status="applied")

        result = sync_target_company_watchlist(self.uid, threshold=70)
        self.assertEqual(result["added"], 0)

    def test_respects_auto_max_cap(self):
        from src.catalog_db import sync_target_company_watchlist

        self._seed_job(company_slug="acme", status="applied")
        self._seed_job(company_slug="beta", status="applied")

        result = sync_target_company_watchlist(self.uid, threshold=70, auto_max=1)
        self.assertEqual(result["added"], 1)

    def test_does_not_duplicate_an_already_present_manual_company(self):
        from src.catalog_db import add_target_company, sync_target_company_watchlist, list_target_companies

        add_target_company(self.uid, "acme", "greenhouse", origin="manual")
        self._seed_job(company_slug="acme", status="applied")

        result = sync_target_company_watchlist(self.uid, threshold=70)
        self.assertEqual(result["added"], 0)
        companies = list_target_companies(self.uid)
        self.assertEqual(len(companies), 1)
        self.assertEqual(companies[0]["origin"], "manual")


class AutoPruneTests(WatchlistHelpersTestCase):
    def _add_old_auto_company(self, slug, days_old):
        from src.db import connect

        old = (datetime.utcnow() - timedelta(days=days_old)).isoformat()
        with connect() as conn:
            conn.execute(
                "INSERT INTO target_companies (user_id, company_slug, ats_type, origin, "
                "created_at, updated_at) VALUES (?, ?, 'greenhouse', 'auto', ?, ?)",
                (self.uid, slug, old, old),
            )

    def test_prunes_auto_company_with_no_qualifying_job_past_grace_period(self):
        from src.catalog_db import sync_target_company_watchlist

        self._add_old_auto_company("stale-co", days_old=90)

        result = sync_target_company_watchlist(self.uid, threshold=70, auto_prune_days=60)
        self.assertEqual(result["pruned"], 1)

    def test_does_not_prune_within_grace_period(self):
        from src.catalog_db import sync_target_company_watchlist

        self._add_old_auto_company("young-co", days_old=10)

        result = sync_target_company_watchlist(self.uid, threshold=70, auto_prune_days=60)
        self.assertEqual(result["pruned"], 0)

    def test_does_not_prune_a_company_still_scoring_well(self):
        from src.catalog_db import sync_target_company_watchlist

        self._add_old_auto_company("good-co", days_old=90)
        self._seed_job(company_slug="good-co", status="scored", match_score=95)

        result = sync_target_company_watchlist(self.uid, threshold=70, auto_prune_days=60)
        self.assertEqual(result["pruned"], 0)

    def test_prunes_once_the_qualifying_job_closes(self):
        """Regression: match_score is never cleared off a user_jobs row
        once its posting closes, so a lifetime "has it ever scored well"
        check would find the very (now-closed) job that got the company
        auto-added in the first place and could never prune it. Pruning
        must only count a *currently open* (catalog status='active')
        qualifying job."""
        from src.catalog_db import sync_target_company_watchlist
        from src.db import connect

        self._add_old_auto_company("closed-co", days_old=90)
        cat_id = self._seed_job(company_slug="closed-co", status="scored", match_score=95)
        with connect() as conn:
            conn.execute("UPDATE catalog_jobs SET status = 'expired' WHERE id = ?", (cat_id,))

        result = sync_target_company_watchlist(self.uid, threshold=70, auto_prune_days=60)
        self.assertEqual(result["pruned"], 1)

    def test_never_prunes_a_company_someone_applied_to_even_if_old(self):
        from src.catalog_db import sync_target_company_watchlist
        from src.db import connect

        self._add_old_auto_company("applied-co", days_old=90)
        cat_id = self._seed_job(company_slug="applied-co", status="applied", match_score=95)
        # Even if the job posting itself has since closed, having applied
        # is a lasting reason to keep watching the company.
        with connect() as conn:
            conn.execute("UPDATE catalog_jobs SET status = 'expired' WHERE id = ?", (cat_id,))

        result = sync_target_company_watchlist(self.uid, threshold=70, auto_prune_days=60)
        self.assertEqual(result["pruned"], 0)

    def test_never_prunes_a_manual_company(self):
        from src.catalog_db import add_target_company, sync_target_company_watchlist
        from src.db import connect

        add_target_company(self.uid, "manual-co", "greenhouse", origin="manual")
        old = (datetime.utcnow() - timedelta(days=200)).isoformat()
        with connect() as conn:
            conn.execute(
                "UPDATE target_companies SET created_at = ? WHERE user_id = ? AND company_slug = ?",
                (old, self.uid, "manual-co"),
            )

        result = sync_target_company_watchlist(self.uid, threshold=70, auto_prune_days=60)
        self.assertEqual(result["pruned"], 0)


class DeleteAndPinTests(WatchlistHelpersTestCase):
    def test_deleting_an_auto_company_with_blocklist_prevents_re_add(self):
        from src.catalog_db import (
            add_target_company,
            delete_target_company,
            list_target_companies,
            sync_target_company_watchlist,
        )

        add_target_company(self.uid, "acme", "greenhouse", origin="auto")
        company_id = list_target_companies(self.uid)[0]["id"]
        delete_target_company(company_id, self.uid, blocklist=True)

        self._seed_job(company_slug="acme", status="applied")
        result = sync_target_company_watchlist(self.uid, threshold=70)
        self.assertEqual(result["added"], 0)

    def test_deleting_a_manual_company_without_blocklist_allows_future_auto_add(self):
        from src.catalog_db import (
            add_target_company,
            delete_target_company,
            list_target_companies,
            sync_target_company_watchlist,
        )

        add_target_company(self.uid, "acme", "greenhouse", origin="manual")
        company_id = list_target_companies(self.uid)[0]["id"]
        delete_target_company(company_id, self.uid, blocklist=False)

        self._seed_job(company_slug="acme", status="applied")
        result = sync_target_company_watchlist(self.uid, threshold=70)
        self.assertEqual(result["added"], 1)

    def test_pinning_promotes_auto_to_manual_and_survives_prune(self):
        from src.catalog_db import (
            add_target_company,
            list_target_companies,
            pin_target_company,
            sync_target_company_watchlist,
        )
        from src.db import connect

        add_target_company(self.uid, "acme", "greenhouse", origin="auto")
        company_id = list_target_companies(self.uid)[0]["id"]
        pin_target_company(company_id, self.uid)

        old = (datetime.utcnow() - timedelta(days=200)).isoformat()
        with connect() as conn:
            conn.execute(
                "UPDATE target_companies SET created_at = ? WHERE id = ?", (old, company_id)
            )

        companies = list_target_companies(self.uid)
        self.assertEqual(companies[0]["origin"], "manual")

        result = sync_target_company_watchlist(self.uid, threshold=70, auto_prune_days=60)
        self.assertEqual(result["pruned"], 0)

    def test_add_target_company_never_demotes_manual_to_auto(self):
        from src.catalog_db import add_target_company, list_target_companies

        add_target_company(self.uid, "acme", "greenhouse", origin="manual")
        add_target_company(self.uid, "acme", "greenhouse", origin="auto")  # e.g. a later auto-add pass

        companies = list_target_companies(self.uid)
        self.assertEqual(companies[0]["origin"], "manual")


if __name__ == "__main__":
    import unittest

    unittest.main()
