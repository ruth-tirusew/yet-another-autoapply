"""Shared Playwright helpers for filling application form URL fields."""

from __future__ import annotations

import re
from typing import Any

from src.applicant import normalize_url

_URL_FIELD_SPECS: dict[str, list[str]] = {
    "linkedin": [
        "input[name*='linkedin' i]",
        "input[id*='linkedin' i]",
        "#linkedin",
        "input[placeholder*='linkedin' i]",
    ],
    "github": [
        "input[name*='github' i]",
        "input[id*='github' i]",
        "#github",
        "input[placeholder*='github' i]",
    ],
    "portfolio": [
        "input[name*='website' i]",
        "input[name*='portfolio' i]",
        "input[id*='website' i]",
        "input[id*='portfolio' i]",
        "input[placeholder*='website' i]",
        "input[placeholder*='portfolio' i]",
        "#website",
        "#portfolio",
    ],
}

_LABEL_PATTERNS: dict[str, list[str]] = {
    "linkedin": [r"linkedin"],
    "github": [r"github"],
    "portfolio": [r"website", r"portfolio", r"personal\s+site"],
}


def _is_fillable(el) -> bool:
    return el.evaluate(
        """el => {
            const tag = el.tagName.toLowerCase();
            if (tag === 'textarea') return true;
            if (tag !== 'input') return false;
            const t = (el.type || 'text').toLowerCase();
            return !['file','checkbox','radio','submit','button','hidden'].includes(t);
        }"""
    )


def _first_fillable(page, selectors: list[str]):
    for sel in selectors:
        el = page.query_selector(sel)
        if el and _is_fillable(el):
            return el
    return None


def _fill_if_empty(el, value: str) -> bool:
    if not value or not el:
        return False
    current = el.input_value() if hasattr(el, "input_value") else ""
    if current and current.strip():
        return False
    el.fill(value)
    return True


def _fill_by_label(page, patterns: list[str], value: str) -> bool:
    if not value:
        return False
    for pattern in patterns:
        try:
            field = page.get_by_label(re.compile(pattern, re.I))
            if field.count():
                target = field.first
                if _fill_if_empty(target, value):
                    return True
        except Exception:
            continue
    return False


def _applicant_urls(applicant: dict[str, Any]) -> dict[str, str]:
    return {
        "linkedin": normalize_url(applicant.get("linkedin")),
        "github": normalize_url(applicant.get("github")),
        "portfolio": normalize_url(applicant.get("portfolio")),
    }


def fill_url_fields(page, applicant: dict[str, Any]) -> None:
    urls = _applicant_urls(applicant)
    for key, value in urls.items():
        if not value:
            continue
        if _fill_by_label(page, _LABEL_PATTERNS.get(key, []), value):
            continue
        el = _first_fillable(page, _URL_FIELD_SPECS.get(key, []))
        if el:
            _fill_if_empty(el, value)
