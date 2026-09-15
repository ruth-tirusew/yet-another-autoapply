"""Shared Playwright browser setup for apply adapters."""

from __future__ import annotations

import os
import subprocess
import sys
from contextlib import contextmanager
from typing import Iterator

_INSTALL_ATTEMPTED = False


def _install_chromium() -> str | None:
    env = os.environ.copy()
    browsers_path = env.get("PLAYWRIGHT_BROWSERS_PATH", "")
    if browsers_path.startswith("/tmp/cursor-sandbox-cache"):
        env.pop("PLAYWRIGHT_BROWSERS_PATH", None)

    result = subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        return detail or "playwright install chromium failed"
    return None


def _launch_chromium(playwright, *, headless: bool = True):
    global _INSTALL_ATTEMPTED

    try:
        return playwright.chromium.launch(headless=headless)
    except Exception as exc:
        message = str(exc)
        if "Executable doesn't exist" not in message or _INSTALL_ATTEMPTED:
            raise

        _INSTALL_ATTEMPTED = True
        install_error = _install_chromium()
        if install_error:
            raise RuntimeError(
                f"{message}\n\nRun: {sys.executable} -m playwright install chromium\n"
                f"Auto-install failed: {install_error}"
            ) from exc

        return playwright.chromium.launch(headless=headless)


@contextmanager
def chromium_browser(*, headless: bool = True) -> Iterator:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = _launch_chromium(playwright, headless=headless)
        try:
            yield browser
        finally:
            browser.close()


@contextmanager
def chromium_context(
    *,
    headless: bool = True,
    storage_state: str | None = None,
) -> Iterator:
    from pathlib import Path

    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = _launch_chromium(playwright, headless=headless)
        state_path = Path(storage_state) if storage_state else None
        if state_path and state_path.exists():
            context = browser.new_context(storage_state=str(state_path))
        else:
            context = browser.new_context()
        try:
            yield context
        finally:
            context.close()
            browser.close()
