"""Configurable JSON API adapter."""

from __future__ import annotations

import time
from typing import Any

from src.crawler.adapters.base import BaseAdapter
from src.crawler.filters import is_relevant, is_worldwide, normalize_job
from src.crawler.http import clean, get


def _dig(obj: Any, path: str) -> Any:
    if path == ".":
        return obj
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit():
            cur = cur[int(part)]
        else:
            return None
    return cur


class JsonApiAdapter(BaseAdapter):
    def fetch(self) -> list[dict]:
        jobs: list[dict] = []
        seen: set[str] = set()
        label = self.config.get("source_label", self.name)
        items_path = self.config.get("items_path", "jobs")
        params_list = self.config.get("params_list")
        single_params = self.config.get("params", {})
        filter_relevant = self.config.get("filter_relevant", False)
        skip_first = self.config.get("skip_first", False)

        param_sets = params_list if params_list else [single_params]

        for params in param_sets:
            try:
                r = get(self.config["url"], params=params or None)
                data = r.json()
                items = _dig(data, items_path)
                if items is None and self.config.get("is_list_root"):
                    items = data if isinstance(data, list) else []
                if not isinstance(items, list):
                    continue
                if skip_first and items:
                    items = items[1:]

                for item in items:
                    if not isinstance(item, dict):
                        continue
                    job = self._map_item(item, label, filter_relevant)
                    if not job:
                        continue
                    key = job["url"]
                    if key in seen:
                        continue
                    seen.add(key)
                    jobs.append(job)
                time.sleep(1)
            except Exception as e:
                print(f"  [{label}] {e}")

        print(f"  {label}: {len(jobs)}")
        return jobs

    def _map_item(self, item: dict, label: str, filter_relevant: bool) -> dict | None:
        """Map common API field names to normalized job."""
        title = (
            item.get("title") or item.get("position") or item.get("jobTitle")
            or item.get("job_title") or ""
        )
        desc = (
            item.get("description") or item.get("jobDescription")
            or item.get("content") or item.get("job_description") or ""
        )

        tags_raw = item.get("tags") or item.get("jobIndustry") or []
        if isinstance(tags_raw, list):
            tags_str = ", ".join(str(t) for t in tags_raw)
        else:
            tags_str = str(tags_raw or "")

        if filter_relevant and not is_relevant(title, tags_str, str(desc)):
            return None
        if not filter_relevant and label not in (
            "Remotive", "Jobicy", "Arbeitnow", "Himalayas", "Golang Cafe",
        ):
            if not is_relevant(title, tags_str, str(desc)):
                return None

        loc = (
            item.get("location") or item.get("candidate_required_location")
            or item.get("jobGeo") or item.get("locationRestrictions") or "Worldwide"
        )
        if isinstance(loc, dict):
            loc = loc.get("name", "Remote")
        if isinstance(loc, list):
            loc = ", ".join(str(x) for x in loc) or "Worldwide"
        if not is_worldwide(str(loc)):
            return None

        url = item.get("url") or item.get("absolute_url") or ""
        if not url and item.get("slug"):
            url = f"https://himalayas.app/jobs/{item['slug']}"
        if not url and item.get("id"):
            url = f"https://remoteok.com/l/{item['id']}"

        company = item.get("company_name") or item.get("companyName") or item.get("company") or ""
        if isinstance(company, dict):
            company = company.get("name", "")

        uid = str(item.get("id") or item.get("slug") or item.get("jobSlug") or "")

        return normalize_job(
            source=label,
            title=title,
            company=str(company),
            url=url,
            location=str(loc),
            salary=str(item.get("salary") or item.get("salaryRange") or item.get("annualSalaryMin") or ""),
            tags=tags_str,
            date_posted=str(item.get("date") or item.get("publication_date") or item.get("pubDate") or item.get("createdAt") or item.get("created_at") or item.get("updated_at") or ""),
            description=clean(str(desc)),
            source_id=uid,
        )
