"""Applicant config helpers: URL normalization and resume merge."""

from __future__ import annotations

import copy
import re
from typing import Any
from urllib.parse import urlparse

from src.settings import get_config

_SCHEME_RE = re.compile(r"^https?://", re.I)


def normalize_url(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    candidate = raw if _SCHEME_RE.match(raw) else f"https://{raw}"
    parsed = urlparse(candidate)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return ""
    host = parsed.netloc.split("@")[-1].split(":")[0]
    if " " in host or (host != "localhost" and "." not in host):
        return ""
    return candidate


def get_applicant() -> dict[str, Any]:
    return get_config().get("applicant") or {}


def _upsert_profile(profiles: list[dict[str, Any]], network: str, url: str) -> list[dict[str, Any]]:
    if not url:
        return profiles
    updated = [p for p in profiles if (p.get("network") or "").lower() != network.lower()]
    updated.append({"network": network, "url": url})
    return updated


def merge_applicant_into_resume(resume: dict[str, Any], applicant: dict[str, Any] | None = None) -> dict[str, Any]:
    """Overlay applicant contact/link fields onto a resume copy. Applicant wins when set."""
    applicant = applicant if applicant is not None else get_applicant()
    merged = copy.deepcopy(resume)
    basics = merged.setdefault("basics", {})

    if applicant.get("name"):
        basics["name"] = applicant["name"].strip()
    if applicant.get("email"):
        basics["email"] = applicant["email"].strip()
    if applicant.get("phone"):
        basics["phone"] = applicant["phone"].strip()
    if applicant.get("location"):
        basics["location"] = {"address": applicant["location"].strip()}

    portfolio = normalize_url(applicant.get("portfolio"))
    if portfolio:
        basics["url"] = portfolio

    profiles = list(basics.get("profiles") or [])
    linkedin = normalize_url(applicant.get("linkedin"))
    github = normalize_url(applicant.get("github"))
    if linkedin:
        profiles = _upsert_profile(profiles, "LinkedIn", linkedin)
    if github:
        profiles = _upsert_profile(profiles, "GitHub", github)
    if profiles:
        basics["profiles"] = profiles

    return merged
