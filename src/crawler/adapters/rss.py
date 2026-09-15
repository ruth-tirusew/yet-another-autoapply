"""Generic RSS feed adapter."""

from __future__ import annotations

try:
    import feedparser
except ImportError:
    feedparser = None

from src.crawler.adapters.base import BaseAdapter
from src.crawler.filters import is_relevant, normalize_job
from src.crawler.http import clean


class RssAdapter(BaseAdapter):
    def fetch(self) -> list[dict]:
        if feedparser is None:
            print(f"  [{self.name}] feedparser not installed")
            return []

        feed_urls = self.config.get("feed_urls") or []
        if self.config.get("feed_url"):
            feed_urls = list(feed_urls) + [self.config["feed_url"]]
        if not feed_urls:
            return []

        jobs: list[dict] = []
        seen: set[str] = set()
        for feed_url in feed_urls:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries:
                title = entry.get("title", "")
                desc = entry.get("summary", entry.get("description", ""))
                if not is_relevant(title, description=str(desc)):
                    continue
                link = entry.get("link", "")
                if link in seen:
                    continue
                seen.add(link)
                company = title.split(" at ")[-1].strip() if " at " in title else ""
                if ":" in title and not company:
                    parts = title.split(":")
                    company = parts[0].strip()
                    title = ":".join(parts[1:]).strip() if len(parts) > 1 else title
                job = normalize_job(
                    source=self.name,
                    title=title,
                    company=company,
                    url=link,
                    date_posted=entry.get("published", entry.get("updated", "")),
                    description=clean(str(desc)),
                    tags=self.config.get("tags", ""),
                )
                if job:
                    jobs.append(job)

        print(f"  {self.name}: {len(jobs)}")
        return jobs
