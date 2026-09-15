"""HTTP helpers for crawlers."""

from __future__ import annotations

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "en-US,en;q=0.9",
}


def get(url: str, **kwargs) -> requests.Response:
    kwargs.setdefault("timeout", 15)
    return requests.get(url, headers=HEADERS, **kwargs)


def clean(text: str, limit: int = 350) -> str:
    return BeautifulSoup(str(text), "lxml").get_text(separator=" ").strip()[:limit]
