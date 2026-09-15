"""Generic Lever ATS adapter."""

from __future__ import annotations

import time

from src.crawler.adapters.base import BaseAdapter
from src.crawler.filters import is_relevant, is_worldwide, normalize_job
from src.crawler.http import clean, get


class LeverAdapter(BaseAdapter):
    def fetch(self) -> list[dict]:
        companies = self._company_entries()
        if not companies:
            return []

        jobs: list[dict] = []
        multi = len(companies) > 1
        for slug, display in companies:
            jobs.extend(self._fetch_company(slug, display, quiet=multi))
        if multi:
            print(f"  Lever (all): {len(jobs)}")
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
            r = get(f"https://api.lever.co/v0/postings/{slug}?mode=json")
            if r.status_code != 200:
                if not quiet:
                    print(f"  Lever ({display}): 0")
                return []
            data = r.json()
            items = data if isinstance(data, list) else data.get("data", [])
            for item in items:
                title = item.get("text", "") or item.get("title", "")
                if not is_relevant(title):
                    continue
                loc = ""
                categories = item.get("categories") or {}
                if isinstance(categories, dict):
                    loc = categories.get("location", "") or ""
                if not loc:
                    loc = item.get("workplaceType", "") or "Remote"
                if not is_worldwide(loc):
                    continue
                desc = ""
                lists = item.get("lists") or []
                if lists:
                    desc = "\n".join(
                        f"{block.get('text', '')}\n" + "\n".join(
                            li.get("text", "") for li in (block.get("content") or [])
                        )
                        for block in lists
                    )
                job = normalize_job(
                    source=f"Lever ({display})",
                    title=title,
                    company=display,
                    url=item.get("hostedUrl") or item.get("applyUrl", ""),
                    location=loc or "Remote",
                    date_posted=item.get("createdAt", ""),
                    description=clean(desc),
                    source_id=str(item.get("id", "")),
                )
                if job:
                    jobs.append(job)
            time.sleep(0.5)
        except Exception as e:
            print(f"  [Lever/{slug}] {e}")

        if not quiet:
            print(f"  Lever ({display}): {len(jobs)}")
        elif jobs:
            print(f"  Lever ({display}): {len(jobs)}")
        return jobs
