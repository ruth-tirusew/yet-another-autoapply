"""Generic Workable ATS adapter."""

from __future__ import annotations

import time

from src.crawler.adapters.base import BaseAdapter
from src.crawler.filters import is_relevant, is_worldwide, normalize_job
from src.crawler.http import clean, get


class WorkableAdapter(BaseAdapter):
    def fetch(self) -> list[dict]:
        companies = self._company_entries()
        if not companies:
            return []

        jobs: list[dict] = []
        multi = len(companies) > 1
        for slug, display in companies:
            jobs.extend(self._fetch_company(slug, display, quiet=multi))
        if multi:
            print(f"  Workable (all): {len(jobs)}")
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
            r = get(f"https://apply.workable.com/api/v1/widget/accounts/{slug}")
            if r.status_code != 200:
                if not quiet:
                    print(f"  Workable ({display}): 0")
                return []
            for item in r.json().get("jobs", []):
                title = item.get("title", "")
                if not is_relevant(title):
                    continue
                loc = item.get("location", {}) or {}
                if isinstance(loc, dict):
                    loc_str = loc.get("location_str", "") or loc.get("country", "") or ""
                    if loc.get("telecommuting"):
                        loc_str = loc_str or "Remote"
                else:
                    loc_str = str(loc)
                if not is_worldwide(loc_str):
                    continue
                job = normalize_job(
                    source=f"Workable ({display})",
                    title=title,
                    company=display,
                    url=f"https://apply.workable.com/{slug}/j/{item.get('shortcode', '')}",
                    location=loc_str or "Remote",
                    date_posted=item.get("created_at", ""),
                    description=clean(item.get("description", "")),
                    source_id=str(item.get("shortcode", "")),
                )
                if job:
                    jobs.append(job)
            time.sleep(0.5)
        except Exception as e:
            print(f"  [Workable/{slug}] {e}")

        if not quiet:
            print(f"  Workable ({display}): {len(jobs)}")
        elif jobs:
            print(f"  Workable ({display}): {len(jobs)}")
        return jobs
