"""Fetch full job descriptions for catalog listings (platform-level)."""

from __future__ import annotations

import re
import time

from bs4 import BeautifulSoup

from src.config import get_config
from src.crawler.http import get
from src.catalog_db import get_catalog_jobs_needing_enrichment, update_catalog_job


def _extract_text(html: str, url: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.select("script, style, nav, header, footer, aside"):
        tag.decompose()

    selectors = []
    url_lower = url.lower()
    if "greenhouse.io" in url_lower:
        selectors = ["#content", ".content", "[data-qa='job-description']"]
    elif "lever.co" in url_lower:
        selectors = [".content", ".posting-page", ".section-wrapper"]
    elif "ashbyhq.com" in url_lower:
        selectors = ["[class*='jobDescription']", ".ashby-job-posting-description", "main"]
    elif "workable.com" in url_lower:
        selectors = ["[data-ui='job-description']", ".job-description", "main"]
    elif "smartrecruiters.com" in url_lower:
        selectors = [".job-description", "[itemprop='description']", "main"]
    elif "remotive.com" in url_lower:
        selectors = [".job-description", "article"]
    else:
        selectors = ["article", "main", ".job-description", ".description", "#job-description", ".content"]

    for sel in selectors:
        el = soup.select_one(sel)
        if el:
            text = el.get_text(separator="\n", strip=True)
            if len(text) > 200:
                return text[:15000]

    body = soup.find("body")
    if body:
        text = body.get_text(separator="\n", strip=True)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text[:15000]
    return ""


def enrich_jobs(limit: int = 100, delay: float | None = None, user_id: int | None = None) -> int:
    delay = delay if delay is not None else get_config().get("pipeline", {}).get("enrich_delay_seconds", 1.5)
    jobs = get_catalog_jobs_needing_enrichment(limit=limit)
    count = 0
    for job in jobs:
        url = job.get("url", "")
        if not url:
            continue
        try:
            r = get(url)
            if r.status_code != 200:
                continue
            full = _extract_text(r.text, url)
            if not full:
                full = job.get("description_short") or ""
            if full:
                update_catalog_job(job["id"], description_full=full)
                count += 1
            time.sleep(delay)
        except Exception as e:
            print(f"  [enrich] catalog {job['id']} {url[:60]}: {e}")
    print(f"  Enriched {count} catalog job descriptions")
    return count
