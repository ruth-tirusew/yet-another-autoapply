"""Generic SmartRecruiters ATS adapter."""

from __future__ import annotations

import time

from src.crawler.adapters.base import BaseAdapter
from src.crawler.filters import is_relevant, is_worldwide, normalize_job
from src.crawler.http import get

PAGE_SIZE = 100
# SmartRecruiters paginates at ~100/page; cap how many pages we'll fetch per
# company so a bad/looping API response can't spin forever.
MAX_PAGES = 20


class SmartRecruitersAdapter(BaseAdapter):
    def fetch(self) -> list[dict]:
        companies = self._company_entries()
        if not companies:
            return []

        jobs: list[dict] = []
        multi = len(companies) > 1
        for slug, display in companies:
            jobs.extend(self._fetch_company(slug, display, quiet=multi))
        if multi:
            print(f"  SmartRecruiters (all): {len(jobs)}")
        return jobs

    def _company_entries(self) -> list[tuple[str, str]]:
        entries: list[tuple[str, str]] = []
        companies = self.config.get("companies") or []
        for entry in companies:
            if isinstance(entry, str):
                entries.append((entry, entry.replace("-", " ").title()))
            elif isinstance(entry, dict):
                slug = entry.get("slug") or entry.get("company_slug") or ""
                display = entry.get("display_name") or slug.replace("-", " ").title()
                if slug:
                    entries.append((slug, display))
        if not entries and self.config.get("company_slug"):
            slug = self.config["company_slug"]
            display = self.config.get("display_name", slug.replace("-", " ").title())
            entries.append((slug, display))
        return entries

    def _fetch_company(self, slug: str, display: str, *, quiet: bool = False) -> list[dict]:
        jobs: list[dict] = []
        try:
            offset = 0
            total_found = None
            for _ in range(MAX_PAGES):
                r = get(
                    f"https://api.smartrecruiters.com/v1/companies/{slug}/postings",
                    params={"limit": PAGE_SIZE, "offset": offset},
                )
                if r.status_code != 200:
                    break
                data = r.json()
                content = data.get("content", [])
                if not content:
                    break
                for item in content:
                    title = item.get("name", "")
                    if not is_relevant(title):
                        continue
                    loc = item.get("location", {}) or {}
                    if isinstance(loc, dict):
                        loc_str = loc.get("fullLocation", "") or loc.get("city", "") or ""
                        if item.get("remote"):
                            loc_str = loc_str or "Remote"
                    else:
                        loc_str = str(loc)
                    if not is_worldwide(loc_str):
                        continue
                    ref = item.get("refNumber", "") or item.get("id", "")
                    job = normalize_job(
                        source=f"SmartRecruiters ({display})",
                        title=title,
                        company=display,
                        url=f"https://jobs.smartrecruiters.com/{slug}/{ref}",
                        location=loc_str or "Remote",
                        date_posted=item.get("releasedDate", ""),
                        description="",
                        source_id=str(ref),
                    )
                    if job:
                        jobs.append(job)

                total_found = data.get("totalFound", total_found)
                offset += len(content)
                if len(content) < PAGE_SIZE or (total_found is not None and offset >= total_found):
                    break
                time.sleep(0.5)
        except Exception as e:
            print(f"  [SmartRecruiters/{slug}] {e}")

        if not quiet:
            print(f"  SmartRecruiters ({display}): {len(jobs)}")
        elif jobs:
            print(f"  SmartRecruiters ({display}): {len(jobs)}")
        return jobs
