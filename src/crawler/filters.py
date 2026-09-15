"""Shared job filtering helpers."""

from __future__ import annotations

from src.crawler.location import (
    ParsedLocation,
    candidate_matches_location,
    is_worldwide as _is_worldwide,
    parse_job_location,
)
from src.keywords import is_relevant as _is_relevant
from src.settings import get_config


def is_relevant(title: str, tags: str = "", description: str = "") -> bool:
    return _is_relevant(title, tags, description)


def is_worldwide(location: str, description: str = "") -> bool:
    return _is_worldwide(location, description)


def candidate_country_code(resume: dict | None) -> str:
    if not resume:
        return ""
    loc = (resume.get("basics") or {}).get("location") or {}
    return (loc.get("countryCode") or "").upper()


def is_job_eligible(
    job: dict,
    resume: dict | None = None,
) -> tuple[bool, str]:
    location = job.get("location") or ""
    description = job.get("description_full") or job.get("description_short") or ""
    parsed = parse_job_location(location, description)
    cc = candidate_country_code(resume)

    if parsed.allowed_country_codes:
        return candidate_matches_location(parsed, cc)

    if not parsed.is_worldwide:
        return False, f"Location restricted: {location or description[:80]}"

    if not cc:
        return True, ""

    loc_lower = location.lower()
    desc_lower = description.lower()
    us_markers = ("usa", "us only", "united states", "u.s. citizen", "authorized to work in the us")
    if cc != "US" and any(m in loc_lower or m in desc_lower for m in us_markers):
        if not _has_worldwide_in_location(loc_lower):
            return False, "US-only role; candidate not in US"

    return True, ""


def _has_worldwide_in_location(loc_lower: str) -> bool:
    parsed = parse_job_location(loc_lower, "")
    return parsed.is_worldwide and parsed.allowed_country_codes is None


def should_ingest_job(location: str, description: str = "") -> bool:
    cfg = get_config().get("filters", {})
    if not cfg.get("require_worldwide", True):
        return True
    return is_worldwide(location, description)


def normalize_job(
    source: str,
    title: str,
    company: str,
    url: str,
    location: str = "Worldwide Remote",
    salary: str = "",
    tags: str = "",
    date_posted: str = "",
    description: str = "",
    source_id: str = "",
) -> dict | None:
    if not url or not title:
        return None
    if not should_ingest_job(location, description):
        return None
    return {
        "source": source,
        "source_id": source_id,
        "title": title,
        "company": company,
        "location": location or "Worldwide Remote",
        "salary": salary,
        "tags": tags,
        "date_posted": date_posted,
        "url": url,
        "description": description,
    }


__all__ = [
    "ParsedLocation",
    "candidate_country_code",
    "is_job_eligible",
    "is_relevant",
    "is_worldwide",
    "normalize_job",
    "parse_job_location",
    "should_ingest_job",
]
