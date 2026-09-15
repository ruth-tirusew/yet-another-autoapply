"""Detect ATS platform from job URL."""

from __future__ import annotations

import re

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("greenhouse", re.compile(r"greenhouse\.io|boards\.greenhouse|grnh\.se|gh_jid=", re.I)),
    ("lever", re.compile(r"jobs\.lever\.co|lever\.co", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com|ashbyhq\.com", re.I)),
    ("workable", re.compile(r"apply\.workable\.com|workable\.com", re.I)),
    ("smartrecruiters", re.compile(r"jobs\.smartrecruiters\.com|smartrecruiters\.com", re.I)),
    ("remotive", re.compile(r"remotive\.com", re.I)),
    ("remoteok", re.compile(r"remoteok\.com", re.I)),
]


def detect_ats_type(url: str) -> str:
    if not url:
        return ""
    for name, pattern in _PATTERNS:
        if pattern.search(url):
            return name
    return ""
