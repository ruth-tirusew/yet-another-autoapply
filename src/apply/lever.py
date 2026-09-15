"""Lever apply adapter via Playwright."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.apply.base import ApplyResult, BaseApplyAdapter, is_lever_url
from src.apply.form_fill import fill_url_fields
from src.apply.playwright_runtime import chromium_browser
from src.settings import applications_dir, get_config


class LeverApplyAdapter(BaseApplyAdapter):
    def can_apply(self, url: str) -> bool:
        return is_lever_url(url)

    def apply(self, job: dict[str, Any], cv_path: str, cover_letter: str) -> ApplyResult:
        cfg = get_config()
        applicant = cfg.get("applicant", {})
        url = job.get("url", "")
        out_dir = applications_dir() / str(job["id"])
        out_dir.mkdir(parents=True, exist_ok=True)
        screenshot = out_dir / "apply_screenshot.png"

        try:
            headless = bool(cfg.get("auto_apply_headless", True))
            with chromium_browser(headless=headless) as browser:
                page = browser.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=60000)

                if applicant.get("name"):
                    for sel in ["input[name='name']", "#name"]:
                        el = page.query_selector(sel)
                        if el:
                            el.fill(applicant["name"])
                            break
                if applicant.get("email"):
                    for sel in ["input[name='email']", "#email"]:
                        el = page.query_selector(sel)
                        if el:
                            el.fill(applicant["email"])
                            break
                if applicant.get("phone"):
                    for sel in ["input[name='phone']", "#phone"]:
                        el = page.query_selector(sel)
                        if el:
                            el.fill(applicant["phone"])
                            break

                fill_url_fields(page, applicant)

                if cover_letter:
                    ta = page.query_selector("textarea")
                    if ta:
                        ta.fill(cover_letter[:5000])

                if Path(cv_path).exists():
                    fi = page.query_selector("input[type='file']")
                    if fi:
                        fi.set_input_files(cv_path)

                page.screenshot(path=str(screenshot))

            return ApplyResult(
                True,
                "lever",
                "Form filled and screenshot saved. Review before final submit.",
                str(screenshot),
            )
        except Exception as e:
            return ApplyResult(False, "lever", str(e), str(screenshot) if screenshot.exists() else None)
