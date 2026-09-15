"""Legacy HTML scrapers wired through config/sources.yaml."""

from __future__ import annotations

from src.crawler.adapters.base import BaseAdapter
from src.crawler.filters import normalize_job
from src.crawler.legacy_scrapers import SCRAPERS


class LegacyAdapter(BaseAdapter):
    def fetch(self) -> list[dict]:
        scraper_name = self.config.get("scraper")
        scrapers = self.config.get("scrapers") or ([scraper_name] if scraper_name else [])
        jobs: list[dict] = []
        seen: set[str] = set()

        for name in scrapers:
            fn = SCRAPERS.get(name)
            if not fn:
                print(f"  [legacy/{self.source_id}] unknown scraper: {name}")
                continue
            for raw in fn():
                url = raw.get("url", "")
                if not url or url in seen:
                    continue
                seen.add(url)
                job = normalize_job(
                    source=raw.get("source", self.name),
                    title=raw.get("title", ""),
                    company=raw.get("company", ""),
                    url=url,
                    location=raw.get("location", "Worldwide Remote"),
                    salary=raw.get("salary", ""),
                    tags=raw.get("tags", ""),
                    date_posted=raw.get("date_posted", ""),
                    description=raw.get("description", ""),
                    source_id=raw.get("source_id", ""),
                )
                if job:
                    jobs.append(job)

        if len(scrapers) == 1:
            return jobs
        print(f"  Legacy ({self.name}): {len(jobs)}")
        return jobs
