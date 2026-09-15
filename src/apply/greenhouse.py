"""Greenhouse apply adapter via Playwright."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.apply.base import ApplyResult, BaseApplyAdapter, is_greenhouse_url, resolve_greenhouse_apply_url
from src.apply.form_fill import fill_url_fields
from src.apply.playwright_runtime import chromium_context
from src.settings import applications_dir, get_config

_COVER_TEXT_SELECTORS = [
    "textarea[name*='cover' i]",
    "textarea[id*='cover' i]",
    "input[name*='cover' i]",
    "input[id*='cover' i]",
]
_COVER_TEXT_FALLBACK = ["#cover_letter", "textarea"]
_COVER_FILE_SELECTORS = [
    "input[type='file'][name*='cover' i]",
    "input[type='file'][id*='cover' i]",
    "input[type='file']#cover_letter",
]
_RESUME_FILE_SELECTORS = [
    "input[type='file'][name*='resume' i]",
    "input[type='file'][name*='cv' i]",
    "input[type='file'][id*='resume' i]",
    "input[type='file'][id*='cv' i]",
    "input[type='file'][name*='attachment' i]",
]
_SUBMIT_SELECTORS = [
    "#submit_app",
    "input[type='submit']",
    "button[type='submit']",
    "button[data-qa='submit-btn']",
]
_VERIFY_SELECTORS = [
    "button[type='submit']",
    "input[type='submit']",
    "#submit_app",
]
_OTP_PHRASES = (
    "verification code",
    "verify your email",
    "one-time",
    "security code",
    "sent a security code",
    "character code",
    "verify you are not a robot",
    "enter the code",
    "confirm your email",
    "check your email",
)
_SUCCESS_PHRASES = (
    "thank you for applying",
    "application received",
    "application submitted",
    "thanks for applying",
    "we received your application",
    "your application has been submitted",
)
_SESSION_FILE = "apply_session.json"
_STATE_FILE = "browser_state.json"


def normalize_otp_code(code: str) -> str:
    return re.sub(r"\D", "", (code or "").strip())


def _headless_from_config() -> bool:
    import os

    if os.environ.get("AUTO_APPLY_HEADED", "").lower() in ("1", "true", "yes"):
        return False
    return bool(get_config().get("auto_apply_headless", True))


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


def _first_match(page, selectors: list[str]):
    for sel in selectors:
        el = page.query_selector(sel)
        if el:
            return el
    return None


def _resume_file_input(page):
    el = _first_match(page, _RESUME_FILE_SELECTORS)
    if el:
        return el
    for candidate in page.query_selector_all("input[type='file']"):
        label = candidate.evaluate("el => ((el.name || '') + ' ' + (el.id || '')).toLowerCase()")
        if "cover" not in label:
            return candidate
    return None


def _fill_cover_letter(page, cover_letter: str, out_dir: Path) -> None:
    text = cover_letter[:5000]
    el = _first_fillable(page, _COVER_TEXT_SELECTORS)
    if el:
        el.fill(text)
        return

    cover_file_input = _first_match(page, _COVER_FILE_SELECTORS)
    if cover_file_input:
        upload_path = out_dir / "cover_letter.md"
        if not upload_path.exists():
            upload_path.write_text(text, encoding="utf-8")
        cover_file_input.set_input_files(str(upload_path))
        return

    el = _first_fillable(page, _COVER_TEXT_FALLBACK)
    if el:
        el.fill(text)


_APPLICANT_FIELD_SPECS: dict[str, list[str]] = {
    "first_name": [
        "#first_name",
        "input[id='first_name']",
        "input[name*='first_name' i]",
        "input[id*='first_name' i]",
        "input[name='first_name']",
    ],
    "last_name": [
        "#last_name",
        "input[id='last_name']",
        "input[name*='last_name' i]",
        "input[id*='last_name' i]",
        "input[name='last_name']",
    ],
    "email": [
        "#email",
        "input[id='email']",
        "input[type='email']",
        "input[name*='email' i]",
        "input[id*='email' i]",
        "input[name='email']",
    ],
    "phone": [
        "#phone",
        "input[id='phone']",
        "input[type='tel']",
        "input[name*='phone' i]",
        "input[id*='phone' i]",
        "input[name='phone']",
    ],
}

_APPLICANT_LABEL_PATTERNS: dict[str, list[str]] = {
    "first_name": [r"first\s*name", r"given\s*name"],
    "last_name": [r"last\s*name", r"family\s*name", r"surname"],
    "email": [r"^email", r"email\s*address"],
    "phone": [r"phone", r"mobile", r"telephone"],
}


def _applicant_fields(applicant: dict[str, Any]) -> dict[str, str]:
    name = applicant.get("name", "") or ""
    parts = name.split()
    return {
        "first_name": parts[0] if parts else "",
        "last_name": " ".join(parts[1:]) if len(parts) > 1 else "",
        "email": applicant.get("email", "") or "",
        "phone": applicant.get("phone", "") or "",
    }


def _fill_by_label(page, patterns: list[str], value: str) -> bool:
    if not value:
        return False
    for pattern in patterns:
        try:
            field = page.get_by_label(re.compile(pattern, re.I))
            if field.count():
                target = field.first
                current = target.input_value()
                if current and current.strip():
                    return False
                target.fill(value)
                return True
        except Exception:
            continue
    return False


def _fill_applicant_field(page, field_key: str, value: str) -> None:
    if not value:
        return
    el = _first_fillable(page, _APPLICANT_FIELD_SPECS.get(field_key, []))
    if el:
        el.fill(value)
        return
    _fill_by_label(page, _APPLICANT_LABEL_PATTERNS.get(field_key, []), value)


def _extract_greenhouse_questions(page) -> list[dict[str, Any]]:
    questions = page.evaluate(
        """() => {
            const loader = window.__remixContext?.state?.loaderData || {};
            const route = loader['routes/$url_token_.jobs_.$job_post_id'];
            if (route?.jobPost?.questions) return route.jobPost.questions;
            if (route?.job?.questions) return route.job.questions;
            const serialized = JSON.stringify(loader);
            const match = serialized.match(/\"questions\":(\\[.*?\\])\\,\"confirm/);
            if (!match) return [];
            try { return JSON.parse(match[1]); } catch (e) { return []; }
        }"""
    )
    return questions if isinstance(questions, list) else []


def _select_react_option(page, field_name: str, option_label: str) -> bool:
    field_id = field_name.removesuffix("[]")
    wrapper = page.locator(f"label[for='{field_id}']").locator(
        "xpath=ancestor::div[contains(@class,'field-wrapper')]"
    )
    if not wrapper.count():
        wrapper = page.locator(f"#{field_id}").locator(
            "xpath=ancestor::div[contains(@class,'field-wrapper')]"
        )
    if not wrapper.count():
        return False
    control = wrapper.locator("[class*='select__control']").first
    control.scroll_into_view_if_needed()
    control.click()
    page.wait_for_timeout(500)
    option = page.locator("[class*='select__option']").filter(has_text=option_label)
    if not option.count():
        option = page.locator("[class*='select__option']").filter(
            has_text=re.compile(re.escape(option_label), re.I)
        )
    if option.count():
        option.first.click()
        page.wait_for_timeout(300)
        return True
    page.keyboard.press("Escape")
    return False


def _pick_relocate_option(values: list[dict[str, Any]], applicant: dict[str, Any]) -> str:
    location = (applicant.get("location") or "").lower()
    labels = [v.get("label", "") for v in values if v.get("label")]
    if location and any(token in location for token in ("austin", "texas")):
        for label in labels:
            if "currently live" in label.lower():
                return label
    for label in labels:
        if "willing to relocate" in label.lower():
            return label
    return labels[0] if labels else ""


def _pick_sponsorship_option(values: list[dict[str, Any]], applicant: dict[str, Any]) -> str:
    auth = (applicant.get("work_authorization") or "").lower()
    yes_markers = ("require sponsorship", "need sponsorship", "will require sponsorship", "visa")
    if any(marker in auth for marker in yes_markers):
        for value in values:
            if value.get("label", "").lower() == "yes":
                return value["label"]
    for value in values:
        if value.get("label", "").lower() == "no":
            return value["label"]
    return values[-1].get("label", "No") if values else "No"


def _answer_custom_question(
    label: str,
    field: dict[str, Any],
    applicant: dict[str, Any],
) -> str | None:
    label_lower = label.lower()
    field_type = field.get("type", "")
    if field_type == "input_text":
        if "hear about" in label_lower:
            return applicant.get("referral_source") or "LinkedIn"
        if "linkedin" in label_lower or "website" in label_lower or "blog" in label_lower:
            return (
                applicant.get("linkedin")
                or applicant.get("portfolio")
                or applicant.get("github")
                or ""
            )
        return None
    if field_type == "multi_value_single_select":
        values = field.get("values") or []
        if "relocate" in label_lower:
            return _pick_relocate_option(values, applicant)
        if "sponsorship" in label_lower or "immigration" in label_lower:
            return _pick_sponsorship_option(values, applicant)
        if values:
            return values[0].get("label", "")
    return None


def _fill_custom_questions(page, applicant: dict[str, Any]) -> None:
    skip_names = {
        "first_name",
        "last_name",
        "email",
        "phone",
        "resume",
        "resume_text",
        "cover_letter",
        "cover_letter_text",
    }
    for question in _extract_greenhouse_questions(page):
        label = question.get("label") or ""
        for field in question.get("fields") or []:
            name = field.get("name") or ""
            field_type = field.get("type") or ""
            if not name or name in skip_names:
                continue
            if field_type == "multi_value_multi_select":
                checkbox = page.locator(f"input[type='checkbox'][name='{name}']").first
                if checkbox.count() and not checkbox.is_checked():
                    checkbox.check(force=True)
                continue
            answer = _answer_custom_question(label, field, applicant)
            if not answer:
                continue
            if field_type == "input_text":
                page.locator(f"#{name}").fill(answer)
            elif field_type == "multi_value_single_select":
                _select_react_option(page, name, answer)


def _fill_greenhouse_form(
    page,
    applicant: dict[str, Any],
    cover_letter: str,
    cv_path: str,
    out_dir: Path,
) -> None:
    for field_key, value in _applicant_fields(applicant).items():
        _fill_applicant_field(page, field_key, value)

    fill_url_fields(page, applicant)

    if cover_letter:
        _fill_cover_letter(page, cover_letter, out_dir)

    if Path(cv_path).exists():
        file_input = _resume_file_input(page)
        if file_input:
            file_input.set_input_files(cv_path)

    _fill_custom_questions(page, applicant)


def _page_text(page) -> str:
    return page.evaluate("() => (document.body?.innerText || '').toLowerCase()")


def _detect_otp_screen(page) -> bool:
    text = _page_text(page)
    if any(phrase in text for phrase in _OTP_PHRASES):
        return True
    return bool(
        page.evaluate(
            """() => {
                const skip = new Set(['hidden','submit','button','file','checkbox','radio']);
                const inputs = Array.from(document.querySelectorAll('input'));
                const otpish = inputs.filter(el => {
                    const t = (el.type || 'text').toLowerCase();
                    if (skip.has(t)) return false;
                    const attrs = ((el.name || '') + ' ' + (el.id || '') + ' ' +
                        (el.placeholder || '') + ' ' + (el.autocomplete || '')).toLowerCase();
                    if (attrs.includes('code') || attrs.includes('token') ||
                        attrs.includes('otp') || attrs.includes('verification')) return true;
                    if (el.autocomplete === 'one-time-code') return true;
                    return false;
                });
                if (otpish.length) return true;
                const boxes = inputs.filter(el => {
                    const t = (el.type || 'text').toLowerCase();
                    if (!['tel', 'text', 'number'].includes(t)) return false;
                    const max = parseInt(el.maxLength || '0', 10);
                    return max === 1;
                });
                return boxes.length >= 4;
            }"""
        )
    )


def _detect_success(page) -> bool:
    text = _page_text(page)
    return any(phrase in text for phrase in _SUCCESS_PHRASES)


def _detect_validation_errors(page) -> bool:
    text = _page_text(page)
    return " is required" in text or "required field" in text


def _click_submit(page) -> bool:
    for sel in _SUBMIT_SELECTORS:
        el = page.query_selector(sel)
        if el and el.is_visible():
            el.click()
            return True
    for label in ("Submit application", "Submit Application", "Submit"):
        button = page.get_by_role("button", name=label, exact=False)
        if button.count():
            button.first.click()
            return True
    return False


def _click_verify(page) -> bool:
    for sel in _VERIFY_SELECTORS:
        el = page.query_selector(sel)
        if el and el.is_visible():
            el.click()
            return True
    for label in ("Verify", "Continue", "Submit", "Confirm"):
        button = page.get_by_role("button", name=label, exact=False)
        if button.count():
            button.first.click()
            return True
    return False


def _fill_otp(page, code: str) -> bool:
    otp = normalize_otp_code(code)
    if not otp:
        return False

    single_selectors = [
        "input[autocomplete='one-time-code']",
        "input[name*='token' i]",
        "input[name*='code' i]",
        "input[id*='code' i]",
        "input[name*='verification' i]",
        "input[placeholder*='code' i]",
    ]
    for sel in single_selectors:
        el = page.query_selector(sel)
        if el and el.is_visible():
            el.fill(otp)
            return True

    boxes = page.query_selector_all(
        "input[type='tel'][maxlength='1'], input[inputmode='numeric'][maxlength='1'], "
        "input[type='text'][maxlength='1']"
    )
    visible_boxes = [box for box in boxes if box.is_visible()]
    if len(visible_boxes) >= len(otp):
        for idx, digit in enumerate(otp[: len(visible_boxes)]):
            visible_boxes[idx].fill(digit)
        return True

    fallback = _first_fillable(page, ["input[type='tel']", "input[type='text']", "input[type='number']"])
    if fallback and fallback.is_visible():
        fallback.fill(otp)
        return True
    return False


def _session_path(out_dir: Path) -> Path:
    return out_dir / _SESSION_FILE


def _state_path(out_dir: Path) -> Path:
    return out_dir / _STATE_FILE


def _save_session(context, page, out_dir: Path, url: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    context.storage_state(path=str(_state_path(out_dir)))
    _session_path(out_dir).write_text(
        json.dumps(
            {
                "url": url,
                "saved_at": datetime.now(timezone.utc).isoformat(),
                "otp_requested_at": datetime.now(timezone.utc).isoformat(),
            }
        ),
        encoding="utf-8",
    )


def _load_session_url(out_dir: Path) -> str | None:
    path = _session_path(out_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    url = data.get("url")
    return url if isinstance(url, str) and url else None


def _run_apply_flow(
    *,
    url: str,
    applicant: dict[str, Any],
    cover_letter: str,
    cv_path: str,
    out_dir: Path,
    screenshot: Path,
    headless: bool,
    otp_code: str | None = None,
    resume_session: bool = False,
) -> ApplyResult:
    state_file = _state_path(out_dir)
    storage_state = str(state_file) if resume_session and state_file.exists() else None
    session_url = _load_session_url(out_dir) if resume_session else None
    target_url = session_url or url

    with chromium_context(headless=headless, storage_state=storage_state) as context:
        page = context.new_page()
        page.goto(target_url, wait_until="networkidle", timeout=90000)
        page.wait_for_selector("#first_name, #email, input[id='first_name']", timeout=30000)
        page.wait_for_function(
            "() => !!window.__remixContext?.state?.loaderData",
            timeout=30000,
        )

        if otp_code:
            if not _detect_otp_screen(page):
                _fill_greenhouse_form(page, applicant, cover_letter, cv_path, out_dir)
                page.wait_for_timeout(1000)
                _click_submit(page)
                page.wait_for_timeout(1500)
            if not _fill_otp(page, otp_code):
                page.screenshot(path=str(screenshot))
                return ApplyResult(
                    False,
                    "greenhouse",
                    "Could not find the verification code field.",
                    str(screenshot),
                )
            _click_verify(page)
            page.wait_for_timeout(2500)
        else:
            _fill_greenhouse_form(page, applicant, cover_letter, cv_path, out_dir)
            page.wait_for_timeout(1000)
            _click_submit(page)
            page.wait_for_timeout(2500)

        page.screenshot(path=str(screenshot))

        if _detect_success(page):
            return ApplyResult(
                True,
                "greenhouse",
                "Application submitted successfully.",
                str(screenshot),
            )

        if _detect_otp_screen(page):
            _save_session(context, page, out_dir, page.url)
            return ApplyResult(
                False,
                "greenhouse",
                "Email verification required. Check your inbox and enter the code below.",
                str(screenshot),
                needs_verification=True,
            )

        if otp_code:
            return ApplyResult(
                False,
                "greenhouse",
                "Verification submitted but success could not be confirmed. Review the screenshot.",
                str(screenshot),
            )

        if _detect_validation_errors(page):
            return ApplyResult(
                False,
                "greenhouse",
                "Form validation failed — required fields may be missing. Review the screenshot.",
                str(screenshot),
            )

        return ApplyResult(
            True,
            "greenhouse",
            "Form filled and screenshot saved. Review before final submit.",
            str(screenshot),
        )


class GreenhouseApplyAdapter(BaseApplyAdapter):
    def can_apply(self, url: str) -> bool:
        return is_greenhouse_url(url)

    def apply(self, job: dict[str, Any], cv_path: str, cover_letter: str) -> ApplyResult:
        cfg = get_config()
        applicant = cfg.get("applicant", {})
        url = resolve_greenhouse_apply_url(job.get("url", ""))
        out_dir = applications_dir() / str(job["id"])
        out_dir.mkdir(parents=True, exist_ok=True)
        screenshot = out_dir / "apply_screenshot.png"

        try:
            return _run_apply_flow(
                url=url,
                applicant=applicant,
                cover_letter=cover_letter,
                cv_path=cv_path,
                out_dir=out_dir,
                screenshot=screenshot,
                headless=_headless_from_config(),
            )
        except Exception as e:
            return ApplyResult(False, "greenhouse", str(e), str(screenshot) if screenshot.exists() else None)

    def complete_verification(
        self,
        job: dict[str, Any],
        cv_path: str,
        cover_letter: str,
        otp_code: str,
    ) -> ApplyResult:
        cfg = get_config()
        applicant = cfg.get("applicant", {})
        url = resolve_greenhouse_apply_url(job.get("url", ""))
        out_dir = applications_dir() / str(job["id"])
        out_dir.mkdir(parents=True, exist_ok=True)
        screenshot = out_dir / "apply_screenshot.png"

        try:
            return _run_apply_flow(
                url=url,
                applicant=applicant,
                cover_letter=cover_letter,
                cv_path=cv_path,
                out_dir=out_dir,
                screenshot=screenshot,
                headless=_headless_from_config(),
                otp_code=otp_code,
                resume_session=True,
            )
        except Exception as e:
            return ApplyResult(False, "greenhouse", str(e), str(screenshot) if screenshot.exists() else None)
