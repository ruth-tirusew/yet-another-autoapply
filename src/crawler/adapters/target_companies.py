"""Fetch jobs from user target company list across ATS APIs."""

from __future__ import annotations

from typing import Any

from src.crawler.adapters.ashby import AshbyAdapter
from src.crawler.adapters.base import BaseAdapter
from src.crawler.adapters.greenhouse import GreenhouseAdapter
from src.crawler.adapters.lever import LeverAdapter
from src.crawler.adapters.smartrecruiters import SmartRecruitersAdapter
from src.crawler.adapters.workable import WorkableAdapter
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
