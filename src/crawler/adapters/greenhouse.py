"""Generic Greenhouse ATS adapter."""

from __future__ import annotations

import time

from src.crawler.adapters.base import BaseAdapter
from src.crawler.filters import is_relevant, is_worldwide, normalize_job
from src.crawler.http import clean, get


class GreenhouseAdapter(BaseAdapter):
    def fetch(self) -> list[dict]:
        companies = self._company_entries()
        if not companies:
            return []

        # Per-slug fetch outcome (True = fetched successfully, even if 0
        # jobs; False = the request failed) — set by _fetch_company. Lets a
        # caller (TargetCompaniesAdapter) tell "this company genuinely has
        # no open roles right now" apart from "the fetch failed", which
        # matters for deciding whether it's safe to mark any of that
        # company's previously-seen jobs as closed.
        self.company_status: dict[str, bool] = {}
        # Every URL this company's own listing showed, before the
        # relevance/location filters below drop most of them — a closing
        # check (TargetCompaniesAdapter/expire_missing_catalog_jobs) must
        # compare against the company's *full* current listing, not just
        # the subset this crawl happened to keep for matching, or a job
        # that's merely irrelevant/wrong-location would read as "closed".
        self.company_urls: dict[str, set[str]] = {}
        jobs: list[dict] = []
        multi = len(companies) > 1
        for slug, display in companies:
            jobs.extend(self._fetch_company(slug, display, quiet=multi))
        if multi:
            print(f"  Greenhouse (all): {len(jobs)}")
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
            r = get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")
            if r.status_code != 200:
                if not quiet:
                    print(f"  Greenhouse ({display}): 0")
                self.company_status[slug] = False
                return []
            for item in r.json().get("jobs", []):
                title = item.get("title", "")
                url = item.get("absolute_url", "")
                if url:
                    self.company_urls.setdefault(slug, set()).add(url)
                if not is_relevant(title):
                    continue
                loc = item.get("location", {}).get("name", "") or "Remote"
                if not is_worldwide(loc):
                    continue
                job = normalize_job(
                    source=f"Greenhouse ({display})",
                    title=title,
                    company=display,
                    url=url,
                    location=loc,
                    date_posted=item.get("updated_at", ""),
                    description=clean(item.get("content", "")),
                    source_id=str(item.get("id", "")),
                    source_tier="native",
                    company_slug=slug,
                )
                if job:
                    jobs.append(job)
            time.sleep(0.5)
            self.company_status[slug] = True
        except Exception as e:
            print(f"  [Greenhouse/{slug}] {e}")
            self.company_status[slug] = False

        if not quiet:
            print(f"  Greenhouse ({display}): {len(jobs)}")
        elif jobs:
            print(f"  Greenhouse ({display}): {len(jobs)}")
        return jobs
