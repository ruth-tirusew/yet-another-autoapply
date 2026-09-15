"""Optional ever-jobs upstream adapter."""

from __future__ import annotations

import requests

from src.config import get_config
from src.keywords import build_search_query
from src.crawler.adapters.base import BaseAdapter
from src.crawler.filters import is_relevant, normalize_job
from src.crawler.http import HEADERS, clean


def _format_location(loc) -> str:
    if isinstance(loc, dict):
        parts = [loc.get("city"), loc.get("state"), loc.get("country")]
        text = ", ".join(p for p in parts if p)
        return text or "Remote"
    return str(loc or "Remote")


def _format_compensation(comp) -> str:
    if isinstance(comp, dict):
        parts = []
        if comp.get("minAmount") or comp.get("maxAmount"):
            lo = comp.get("minAmount", "")
            hi = comp.get("maxAmount", "")
            parts.append(f"{lo}-{hi}".strip("-"))
        if comp.get("currency"):
            parts.append(str(comp["currency"]))
        if comp.get("interval"):
            parts.append(str(comp["interval"]))
        return " ".join(parts)
    return str(comp or "")


def _resolve_search_term(ever: dict, adapter_config: dict) -> str | None:
    mode = adapter_config.get("search_mode") or ever.get("search_mode", "auto")
    if mode == "manual":
        term = adapter_config.get("search_term") or ever.get("search_term") or ""
        return term.strip() or None
    groups = adapter_config.get("search_groups") or ever.get("search_groups") or ["stack", "roles", "frameworks"]
    query = build_search_query(groups)
    return query or None


class EverJobsAdapter(BaseAdapter):
    def fetch(self) -> list[dict]:
        jobs: list[dict] = []
        cfg = get_config()
        ever = cfg.get("ever_jobs", {})
        base_url = (cfg.get("ever_jobs_url") or ever.get("api_url", "")).rstrip("/")
        if not base_url:
            print("  [Ever Jobs] API URL not configured")
            return jobs

        search_term = _resolve_search_term(ever, self.config)
        if not search_term:
            print("  [Ever Jobs] No search query configured (set keywords or manual search term)")
            return jobs

        payload: dict = {
            "searchTerm": search_term,
            "isRemote": self.config.get("is_remote", ever.get("is_remote", True)),
            "resultsWanted": self.config.get("results_wanted", ever.get("results_wanted", 50)),
        }
        site_type = self.config.get("site_type") or ever.get("site_type")
        if site_type:
            payload["siteType"] = site_type

        try:
            r = requests.post(
                f"{base_url}/api/jobs/search",
                json=payload,
                headers={**HEADERS, "Content-Type": "application/json"},
                timeout=120,
            )
            if r.status_code not in (200, 201):
                print(f"  [Ever Jobs] HTTP {r.status_code}: {r.text[:200]}")
                return jobs

            data = r.json()
            items = data if isinstance(data, list) else data.get("jobs", data.get("data", []))
            for item in items:
                if not isinstance(item, dict):
                    continue
                title = item.get("title", item.get("job_title", ""))
                if not is_relevant(title, description=str(item.get("description", ""))):
                    continue
                job = normalize_job(
                    source=f"Ever Jobs ({item.get('site', 'unknown')})",
                    title=title,
                    company=item.get("companyName", item.get("company", item.get("company_name", ""))),
                    url=item.get("jobUrl", item.get("job_url", item.get("url", ""))),
                    location=_format_location(item.get("location")),
                    salary=_format_compensation(item.get("compensation", item.get("salary", ""))),
                    description=clean(str(item.get("description", ""))),
                    source_id=str(item.get("id", "")),
                )
                if job:
                    jobs.append(job)
        except Exception as e:
            print(f"  [Ever Jobs] {e}")

        print(f"  Ever Jobs: {len(jobs)}")
        return jobs
