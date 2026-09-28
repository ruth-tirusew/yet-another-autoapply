"""Shared job filtering helpers."""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from src.crawler.location import (
    ParsedLocation,
    candidate_matches_location,
    is_worldwide as _is_worldwide,
    parse_job_location,
)
from src.keywords import is_relevant as _is_relevant
from src.settings import get_config

# Query params that vary between otherwise-identical links to the same
# posting (campaign/referral tracking) rather than identifying the posting
# itself. Stripped before hashing so a shared/re-crawled link with a
# different utm_source doesn't read as a brand new job. Deliberately a
# narrow, explicit list rather than "drop all query params" — several ATS
# boards (Ashby, SmartRecruiters) put real identity in query params, not
# just the path, and dropping those would collide unrelated postings.
#
# gh_jid, source and ref are deliberately NOT on this list even though they
# look tracking-shaped: a Greenhouse board embedded on a company's own
# domain (as opposed to boards.greenhouse.io/<slug>) identifies the posting
# *only* via ?gh_jid=<id> on an otherwise-identical path, so stripping it
# collided every job at that company onto one url_hash and silently lost
# all but the last one crawled. "source" and "ref" are common real query
# params on other boards for the same reason — plausible-looking tracking
# names are not a safe signal on their own.
_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gh_src", "lever-source", "lever-origin", "ref_",
    "fbclid", "gclid", "mc_cid", "mc_eid", "_ga", "igshid",
}


def normalize_url(url: str) -> str:
    """Canonicalize a job URL so cosmetic differences don't create a new identity.

    Lowercases scheme and host (case-insensitive per RFC), drops a default
    port, strips known tracking query params (sorting what's left for a
    deterministic order), drops the fragment, and removes a trailing slash.
    The path itself is left case-sensitive — ATS job slugs routinely are.
    """
    url = (url or "").strip()
    if not url:
        return ""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    if (scheme == "http" and netloc.endswith(":80")) or (
        scheme == "https" and netloc.endswith(":443")
    ):
        netloc = netloc.rsplit(":", 1)[0]
    path = parts.path.rstrip("/") or "/"
    query_pairs = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in _TRACKING_PARAMS
    ]
    query = urlencode(sorted(query_pairs))
    return urlunsplit((scheme, netloc, path, query, ""))


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
    source_tier: str = "aggregator",
    company_slug: str = "",
) -> dict | None:
    """Build the normalized job dict every adapter returns.

    ``source_tier`` distinguishes a posting fetched directly from a
    company's own ATS API ("native": Greenhouse, Lever, Ashby, Workable,
    SmartRecruiters) from one relayed through a third-party aggregator. A
    native fetch is authoritative for that posting — its date, description
    and open/closed status take precedence over an aggregator's copy of the
    same URL (see upsert_catalog_job). ``company_slug`` is the exact ATS
    slug the fetch used (only known to a native-adapter caller, which
    already iterates ``(slug, display)`` pairs), used to scope "this
    company's postings are no longer listed" expiry checks precisely,
    rather than matching on a free-text display name.
    """
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
        "source_tier": source_tier,
        "company_slug": company_slug,
    }


__all__ = [
    "ParsedLocation",
    "candidate_country_code",
    "is_job_eligible",
    "is_relevant",
    "is_worldwide",
    "normalize_job",
    "normalize_url",
    "parse_job_location",
    "should_ingest_job",
]
