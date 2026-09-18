"""Adapter for Feashliaa/job-board-aggregator chunked job data.

The aggregator's generated chunks now live in a separate data repo
(Feashliaa/job-board-data), served over GitHub Pages, not in the
job-board-aggregator repo itself.
"""

from __future__ import annotations

import gzip
import json
import time
from datetime import datetime, timezone
from typing import Any

from src.crawler.adapters.base import BaseAdapter
from src.crawler.filters import is_relevant, normalize_job
from src.crawler.http import get
from src.db import get_existing_url_hashes, url_hash
from src.tenant import resolve_user_id

DEFAULT_BASE_URL = "https://feashliaa.github.io/job-board-data/data/chunks"


def _format_company(slug: str) -> str:
    return slug.replace("-", " ").replace("_", " ").title()


def _chunk_window(chunks: list[str], max_chunks: int, rotate: bool) -> list[str]:
    if not chunks or max_chunks <= 0:
        return []
    if len(chunks) <= max_chunks:
        return chunks
    if not rotate:
        return chunks[:max_chunks]
    day = datetime.now(timezone.utc).timetuple().tm_yday
    start = day % len(chunks)
    selected: list[str] = []
    for i in range(max_chunks):
        selected.append(chunks[(start + i) % len(chunks)])
    return selected


class JobBoardAggregatorAdapter(BaseAdapter):
    def fetch(self) -> list[dict]:
        jobs: list[dict] = []
        seen: set[str] = set()
        label = self.config.get("source_label", self.name)
        base_url = (self.config.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
        remote_only = self.config.get("remote_only", True)
        exclude_recruiters = self.config.get("exclude_recruiters", True)
        max_chunks = int(self.config.get("max_chunks", 6))
        max_jobs = int(self.config.get("max_jobs", 500))
        chunk_rotation = self.config.get("chunk_rotation", True)
        skip_existing = self.config.get("skip_existing", True)
        existing_hashes = get_existing_url_hashes(resolve_user_id()) if skip_existing else set()
        skipped_existing = 0

        try:
            manifest = get(f"{base_url}/jobs_manifest.json", timeout=30).json()
        except Exception as e:
            print(f"  [{label}] manifest fetch failed: {e}")
            return jobs

        chunks = manifest.get("chunks") or []
        if not isinstance(chunks, list):
            print(f"  [{label}] invalid manifest")
            return jobs

        selected = _chunk_window(chunks, max_chunks, chunk_rotation)
        for chunk_name in selected:
            if len(jobs) >= max_jobs:
                break
            try:
                r = get(f"{base_url}/{chunk_name}", timeout=90)
                payload = json.loads(gzip.decompress(r.content))
            except Exception as e:
                print(f"  [{label}] {chunk_name}: {e}")
                continue

            if not isinstance(payload, list):
                continue

            for item in payload:
                if len(jobs) >= max_jobs:
                    break
                if not isinstance(item, dict):
                    continue
                job = self._map_item(
                    item,
                    label,
                    remote_only=remote_only,
                    exclude_recruiters=exclude_recruiters,
                )
                if not job:
                    continue
                url = job["url"]
                uh = url_hash(url)
                if skip_existing and uh in existing_hashes:
                    skipped_existing += 1
                    continue
                if url in seen:
                    continue
                seen.add(url)
                jobs.append(job)
                if skip_existing:
                    existing_hashes.add(uh)

            time.sleep(0.5)

        suffix = f", {skipped_existing} already in DB" if skipped_existing else ""
        print(f"  {label}: {len(jobs)} new (from {len(selected)}/{len(chunks)} chunks{suffix})")
        return jobs

    def _map_item(
        self,
        item: dict[str, Any],
        label: str,
        *,
        remote_only: bool,
        exclude_recruiters: bool,
    ) -> dict | None:
        if remote_only and not item.get("remote"):
            return None
        if exclude_recruiters and item.get("is_recruiter"):
            return None

        title = str(item.get("title") or "").strip()
        if not title or not is_relevant(title):
            return None

        location = str(item.get("location") or "").strip()
        if item.get("remote") and not location:
            location = "Remote"

        company = _format_company(str(item.get("company") or ""))
        ats = str(item.get("ats") or "")
        skill = str(item.get("skill_level") or "")
        tags = ", ".join(part for part in (ats, skill) if part)

        salary = item.get("salary")
        salary_str = str(salary) if salary not in (None, "") else ""

        url = str(item.get("url") or "").strip()
        source_id = url.rsplit("/", 1)[-1] if url else ""

        return normalize_job(
            source=label,
            title=title,
            company=company,
            url=url,
            location=location or "Remote",
            salary=salary_str,
            tags=tags,
            date_posted=str(item.get("scraped_at") or ""),
            description="",
            source_id=source_id,
        )
