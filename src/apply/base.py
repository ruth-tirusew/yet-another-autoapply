"""Apply automation base and router."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse


@dataclass
class ApplyResult:
    success: bool
    method: str
    message: str
    screenshot_path: str | None = None
    needs_verification: bool = False
    manual_required: bool = False
    """True when nothing was actually submitted to the employer — e.g. the
    email adapter only wrote a local draft. `success` stays True (the
    adapter did what it can do), but the job must not be marked `applied`."""


class BaseApplyAdapter(ABC):
    @abstractmethod
    def can_apply(self, url: str) -> bool:
        ...

    @abstractmethod
    def apply(self, job: dict[str, Any], cv_path: str, cover_letter: str) -> ApplyResult:
        ...


def is_greenhouse_url(url: str) -> bool:
    u = url.lower()
    if "greenhouse.io" in u or "gh_jid=" in u:
        return True
    if "grnh.se" in u:
        return True
    return bool(re.search(r"[?&]gh_jid=\d+", u))


def is_lever_url(url: str) -> bool:
    u = url.lower()
    return "jobs.lever.co" in u or "lever.co" in u


def detect_adapter(url: str) -> str:
    if is_greenhouse_url(url):
        return "greenhouse"
    if is_lever_url(url):
        return "lever"
    if url.startswith("mailto:"):
        return "email"
    return "generic"


def resolve_greenhouse_apply_url(url: str) -> str:
    """Map company career pages with gh_jid to Greenhouse embed apply URL."""
    if "greenhouse.io" in url.lower():
        return url
    m = re.search(r"[?&]gh_jid=(\d+)", url)
    if not m:
        return url
    job_id = m.group(1)
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    slug = host.split(".")[0] if host else ""
    if slug:
        return f"https://boards.greenhouse.io/embed/job_app?for={slug}&token={job_id}"
    return url


def resolve_cv_path(cv_path: str) -> str:
    """Use PDF, or HTML fallback from tailor when WeasyPrint is missing."""
    from pathlib import Path

    p = Path(cv_path)
    if p.exists():
        return str(p)
    html = p.with_suffix(".html")
    if html.exists():
        return str(html)
    return cv_path
