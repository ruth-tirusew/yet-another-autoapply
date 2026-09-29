"""Fetch jobs from user target company list across ATS APIs."""

from __future__ import annotations

from typing import Any

from src.crawler.adapters.ashby import AshbyAdapter
from src.crawler.adapters.base import BaseAdapter
from src.crawler.adapters.greenhouse import GreenhouseAdapter
from src.crawler.adapters.lever import LeverAdapter
from src.crawler.adapters.smartrecruiters import SmartRecruitersAdapter
from src.crawler.adapters.workable import WorkableAdapter
from src.catalog_db import expire_missing_catalog_jobs
from src.db import connect
from src.tenant import resolve_user_id

_ATS_ADAPTERS: dict[str, type[BaseAdapter]] = {
    "greenhouse": GreenhouseAdapter,
    "lever": LeverAdapter,
    "ashby": AshbyAdapter,
    "workable": WorkableAdapter,
    "smartrecruiters": SmartRecruitersAdapter,
}


class TargetCompaniesAdapter(BaseAdapter):
    def fetch(self) -> list[dict]:
        uid = resolve_user_id(self.config.get("user_id"))
        companies = _list_target_companies(uid)
        if not companies:
            return []

        # A dry-run crawl (crawl_all(persist=False)) must not have the side
        # effect of expiring catalog rows — expiry is itself a persisted
        # write, just one this adapter makes directly instead of through
        # upsert_job. Default True so a direct/test construction of this
        # adapter (no "persist" key in config) keeps expiring, matching
        # this adapter's behavior before persist-gating existed.
        persist = bool(self.config.get("persist", True))

        jobs: list[dict] = []
        for co in companies:
            ats = co.get("ats_type", "")
            cls = _ATS_ADAPTERS.get(ats)
            if not cls:
                continue
            source_config: dict[str, Any] = {
                "id": f"target-{co['company_slug']}",
                "name": co.get("display_name") or co["company_slug"],
                "adapter": ats,
                "company_slug": co["company_slug"],
                "display_name": co.get("display_name") or co["company_slug"],
            }
            adapter = cls(source_config)
            fetched = adapter.fetch()
            for job in fetched:
                job["source"] = f"Target: {co.get('display_name') or co['company_slug']}"
            jobs.extend(fetched)

            # Only a *confirmed successful* fetch of this company's full
            # current listing may expire anything — see
            # expire_missing_catalog_jobs. company_status[slug] is absent
            # entirely if _company_entries() never even tried this slug.
            slug = co["company_slug"]
            if not persist or not getattr(adapter, "company_status", {}).get(slug):
                continue

            # company_urls (all URLs the listing showed, unfiltered) is
            # what a real adapter tracks; fall back to the filtered
            # `fetched` list for a test double/mock that doesn't set it,
            # so a merely irrelevant/wrong-location job isn't read as
            # "closed" on a real crawl.
            company_urls = getattr(adapter, "company_urls", None)
            if isinstance(company_urls, dict):
                current_urls = company_urls.get(slug, set())
            else:
                current_urls = {j["url"] for j in fetched}
            expired = expire_missing_catalog_jobs(ats, slug, current_urls)
            if expired:
                print(f"  Target {slug}: {expired} job(s) no longer listed, marked expired")
        print(f"  Target companies: {len(jobs)}")
        return jobs


def _list_target_companies(user_id: int) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT company_slug, ats_type, display_name, tier
            FROM target_companies
            WHERE user_id = ? AND enabled = 1
            ORDER BY tier ASC, display_name ASC
            """,
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]
