"""Probe URLs from awesome-job-boards and suggest registry entries."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

import requests

from src.settings import get_config

ATS_PATTERNS = {
    "greenhouse": re.compile(r"greenhouse\.io|boards\.greenhouse", re.I),
    "lever": re.compile(r"jobs\.lever\.co|lever\.co", re.I),
    "ashby": re.compile(r"jobs\.ashbyhq\.com", re.I),
}


def classify_url(url: str) -> dict[str, Any]:
    for adapter, pattern in ATS_PATTERNS.items():
        if pattern.search(url):
            slug = ""
            if adapter == "greenhouse":
                m = re.search(r"boards\.greenhouse\.io/([^/\s]+)", url)
                if m:
                    slug = m.group(1)
            return {"adapter": adapter, "company_slug": slug, "enabled": False}
    if url.endswith(".rss") or "/rss" in url:
        return {"adapter": "rss", "feed_url": url, "enabled": False}
    if "/api/" in url:
        return {"adapter": "json_api", "url": url, "enabled": False}
    return {"adapter": "json_api", "enabled": False, "note": "needs manual sources.yaml entry"}


RELEVANT_SECTIONS = {"programming", "remote", "devops", "tech", "open source"}
RELEVANT_SUBSECTIONS = {"go", "aggregator"}

SKIP_URL_FRAGMENTS = (
    "github.com/tramcar",
    "github.com/sindresorhus",
    "cdn.jsdelivr.net",
    "CONTRIBUTING",
)

# Domains with verified public feeds/APIs not already in config/sources.yaml
KNOWN_DOMAIN_TEMPLATES: dict[str, dict[str, Any]] = {}


def fetch_awesome_list() -> list[dict[str, Any]]:
    cfg = get_config()
    url = cfg.get("source_discovery", {}).get(
        "awesome_job_boards_url",
        "https://raw.githubusercontent.com/tramcar/awesome-job-boards/master/README.md",
    )
    try:
        text = requests.get(url, timeout=30).text
    except Exception:
        return []

    link_re = re.compile(r"\[([^\]]+)\]\((https?://[^\)]+)\)")
    results: list[dict[str, Any]] = []
    section = ""
    subsection = ""

    for line in text.splitlines():
        if line.startswith("## "):
            section = line[3:].split("(")[0].strip().lower()
            subsection = ""
            continue
        if line.startswith("### "):
            subsection = line[4:].strip().lower()
            continue
        if not line.strip().startswith("*"):
            continue
        if section not in RELEVANT_SECTIONS:
            if not (section == "programming" and subsection in RELEVANT_SUBSECTIONS):
                continue
        for name, link in link_re.findall(line):
            if any(skip in link for skip in SKIP_URL_FRAGMENTS):
                continue
            suggestion = classify_url(link)
            domain = urlparse(link).netloc.lower().removeprefix("www.")
            template = KNOWN_DOMAIN_TEMPLATES.get(domain)
            if template:
                suggestion = {**suggestion, **template, "adapter": template["adapter"]}
            results.append(
                {
                    "id": re.sub(r"[^a-z0-9]+", "_", name.lower())[:40],
                    "name": name.strip(),
                    "seed_url": link.strip(),
                    "section": section,
                    "subsection": subsection,
                    **suggestion,
                }
            )
    return results


def _is_crawlable(item: dict[str, Any]) -> bool:
    adapter = item.get("adapter")
    if adapter == "rss":
        return bool(item.get("feed_url"))
    if adapter == "greenhouse":
        return bool(item.get("company_slug"))
    if adapter == "json_api":
        url = item.get("url") or item.get("seed_url") or ""
        if "/api/" in url:
            return True
        domain = urlparse(url).netloc.lower().removeprefix("www.")
        return domain in KNOWN_DOMAIN_TEMPLATES
    if adapter == "legacy":
        return bool(item.get("scraper") or item.get("scrapers"))
    return False


def item_to_source(item: dict[str, Any]) -> dict[str, Any]:
    adapter = item["adapter"]
    source: dict[str, Any] = {
        "id": f"discovered_{item['id']}",
        "name": item.get("name", item["id"]),
        "adapter": adapter,
        "enabled": True,
        "discovered": True,
        "seed_url": item.get("seed_url", ""),
    }
    if adapter == "rss":
        source["feed_url"] = item.get("feed_url") or item.get("seed_url")
    elif adapter == "greenhouse":
        source["company_slug"] = item["company_slug"]
        source["display_name"] = item.get("name", item["company_slug"].title())
    elif adapter == "json_api":
        source["url"] = item.get("url") or item.get("seed_url")
        source["items_path"] = item.get("items_path", "jobs")
        source["filter_relevant"] = item.get("filter_relevant", True)
        if item.get("params"):
            source["params"] = item["params"]
        if item.get("params_list"):
            source["params_list"] = item["params_list"]
    return source


def _source_fingerprints(sources: list[dict[str, Any]]) -> set[str]:
    keys: set[str] = set()
    for s in sources:
        for url in (
            s.get("url"),
            s.get("feed_url"),
            s.get("seed_url"),
        ):
            if url:
                keys.add(_normalize_url(str(url)))
        for feed in s.get("feed_urls") or []:
            keys.add(_normalize_url(str(feed)))
        if s.get("company_slug"):
            keys.add(f"greenhouse:{s['company_slug'].lower()}")
        for entry in s.get("companies") or []:
            slug = entry if isinstance(entry, str) else entry.get("slug") or entry.get("company_slug")
            if slug:
                keys.add(f"greenhouse:{slug.lower()}")
    return keys


def _normalize_url(url: str) -> str:
    parsed = urlparse(url.strip().rstrip("/"))
    return f"{parsed.netloc.lower()}{parsed.path.rstrip('/')}"


def merge_discovered_sources(static_sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cfg = get_config()
    discovery = cfg.get("source_discovery", {})
    if not discovery.get("auto_enable_discovered"):
        return static_sources

    max_new = int(discovery.get("max_discovered", 40))
    known = _source_fingerprints(static_sources)
    static_ids = {s.get("id") for s in static_sources}
    merged = list(static_sources)
    added = 0

    for item in fetch_awesome_list():
        if added >= max_new:
            break
        if not _is_crawlable(item):
            continue
        source = item_to_source(item)
        sid = source["id"]
        if sid in static_ids:
            continue
        fp = _normalize_url(source.get("url") or source.get("feed_url") or source.get("seed_url", ""))
        if fp and fp in known:
            continue
        if source.get("company_slug"):
            gh_key = f"greenhouse:{source['company_slug'].lower()}"
            if gh_key in known:
                continue
            known.add(gh_key)
        elif fp:
            known.add(fp)
        merged.append(source)
        static_ids.add(sid)
        added += 1

    if added:
        print(f"  Source discovery: +{added} boards from awesome-job-boards")
    else:
        candidates = len(fetch_awesome_list())
        print(
            f"  Source discovery: 0 new crawlable boards "
            f"({candidates} relevant links — most need RSS/API or a legacy scraper)"
        )
    return merged
