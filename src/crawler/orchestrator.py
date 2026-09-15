"""Run all enabled platform sources concurrently (shared catalog)."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from src.config import get_config
from src.config_store import PLATFORM_SOURCES_USER_ID, list_sources
from src.crawler.registry import get_adapter
from src.db import init_db, mark_stale_jobs, upsert_job, upsert_source_registry


def crawl_all(persist: bool = True, user_id: int | None = None) -> list[dict]:
    init_db()
    cfg = get_config()
    sources = [s for s in list_sources(PLATFORM_SOURCES_USER_ID) if s.get("enabled", True)]
    all_jobs: list[dict] = []
    seen_urls: set[str] = set()
    platform_uid = PLATFORM_SOURCES_USER_ID

    def _fetch(source: dict[str, Any]) -> tuple[str, list[dict], str | None]:
        sid = source.get("id", "unknown")
        try:
            adapter = get_adapter(source)
            jobs = adapter.fetch()
            upsert_source_registry(source, status="ok", user_id=platform_uid)
            return sid, jobs, None
        except Exception as e:
            upsert_source_registry(source, status="error", error=str(e), user_id=platform_uid)
            return sid, [], str(e)

    print("=" * 55)
    print("  Job Crawler — platform catalog crawl")
    discovered = sum(1 for s in sources if s.get("discovered"))
    print(f"  Enabled sources: {len(sources)} ({discovered} from awesome-job-boards)")
    print("=" * 55)

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(_fetch, s): s for s in sources}
        for future in as_completed(futures):
            sid, jobs, err = future.result()
            if err:
                print(f"  [{sid}] FAILED: {err}")
                continue
            for job in jobs:
                url = job.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    all_jobs.append(job)
                    if persist:
                        upsert_job(job, source_id=sid)
            time.sleep(0.3)

    stale_days = cfg.get("stale_job_days", 30)
    stale = mark_stale_jobs(stale_days)
    if stale:
        print(f"  Marked {stale} stale catalog jobs")

    print(f"\n{'='*55}")
    print(f"  Total unique jobs: {len(all_jobs)}")
    print(f"{'='*55}")
    return all_jobs
