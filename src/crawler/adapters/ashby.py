"""Generic Ashby ATS adapter."""

from __future__ import annotations

import time

from src.crawler.adapters.base import BaseAdapter
from src.crawler.filters import is_relevant, is_worldwide, normalize_job
from src.crawler.http import clean, get


class AshbyAdapter(BaseAdapter):
    def fetch(self) -> list[dict]:
        companies = self._company_entries()
        if not companies:
            return []

        jobs: list[dict] = []
        multi = len(companies) > 1
        for slug, display in companies:
            jobs.extend(self._fetch_company(slug, display, quiet=multi))
        if multi:
            print(f"  Ashby (all): {len(jobs)}")
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
            r = get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
            if r.status_code != 200:
                if not quiet:
                    print(f"  Ashby ({display}): 0")
                return []
            for item in r.json().get("jobs", []):
                title = item.get("title", "")
                if not is_relevant(title):
                    continue
                loc = item.get("location", "") or "Remote"
                if item.get("isRemote") and not is_worldwide(loc):
                    loc = "Remote"
                elif not is_worldwide(loc):
                    continue
                job = normalize_job(
                    source=f"Ashby ({display})",
                    title=title,
                    company=display,
                    url=item.get("jobUrl") or item.get("applyUrl", ""),
                    location=loc,
                    date_posted=item.get("publishedAt", ""),
                    description=clean(item.get("descriptionPlain", "") or item.get("description", "")),
                    source_id=str(item.get("id", "")),
                )
                if job:
                    jobs.append(job)
            time.sleep(0.5)
        except Exception as e:
            print(f"  [Ashby/{slug}] {e}")

        if not quiet:
            print(f"  Ashby ({display}): {len(jobs)}")
        elif jobs:
            print(f"  Ashby ({display}): {len(jobs)}")
        return jobs
