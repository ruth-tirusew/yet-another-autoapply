"""Apply dispatcher with safety limits."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from src.apply.base import ApplyResult, detect_adapter, resolve_cv_path
from src.apply.email import EmailApplyAdapter
from src.apply.greenhouse import GreenhouseApplyAdapter
from src.apply.lever import LeverApplyAdapter
from src.apply.otp_mail import build_imap_config, fetch_greenhouse_otp
from src.db import (
    count_applications_today,
    get_application,
    get_imap_password,
    get_job,
    list_applications,
    log_application_event,
    save_application,
    set_job_status,
)
from src.settings import applications_dir, get_config

ADAPTERS = {
    "greenhouse": GreenhouseApplyAdapter(),
    "lever": LeverApplyAdapter(),
    "email": EmailApplyAdapter(),
}


def _read_cover_letter(app: dict) -> str:
    if app.get("cover_letter_text"):
        return app["cover_letter_text"]
    path = app.get("cover_letter_path", "")
    if path and Path(path).exists():
        return Path(path).read_text(encoding="utf-8")
    return ""


def _apply_result_payload(result: ApplyResult) -> str:
    return json.dumps(
        {
            "success": result.success,
            "message": result.message,
            "needs_verification": result.needs_verification,
        }
    )


def _status_for_result(result: ApplyResult) -> str:
    if result.needs_verification:
        return "needs_verification"
    if result.manual_required:
        # Nothing was actually submitted (e.g. email draft written to disk) —
        # leave the job approved rather than falsely marking it applied.
        return "approved"
    if result.success:
        return "applied"
    return "failed"


def _event_for_result(result: ApplyResult) -> str:
    if result.needs_verification:
        return "apply_needs_verification"
    if result.manual_required:
        return "apply_draft_saved"
    if result.success:
        return "apply_succeeded"
    return "apply_failed"


def _load_otp_requested_at(job_id: int) -> datetime:
    path = applications_dir() / str(job_id) / "apply_session.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            raw = data.get("otp_requested_at") or data.get("saved_at")
            if raw:
                return datetime.fromisoformat(str(raw))
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            pass
    return datetime.now(timezone.utc)


def _imap_config_for_fetch() -> dict | None:
    cfg = get_config()
    return build_imap_config(cfg, get_imap_password())


def _fetch_otp_for_job(job_id: int) -> str | None:
    imap_config = _imap_config_for_fetch()
    if not imap_config:
        return None
    since = _load_otp_requested_at(job_id)
    return fetch_greenhouse_otp(imap_config, since=since)


def _finalize_apply_result(job_id: int, result: ApplyResult, app: dict) -> dict:
    set_job_status(job_id, _status_for_result(result))
    log_application_event(
        job_id,
        _event_for_result(result),
        {"method": result.method, "message": result.message},
    )
    save_application(
        job_id,
        tailored_cv_path=app.get("tailored_cv_path", ""),
        cover_letter_path=app.get("cover_letter_path", ""),
        cover_letter_text=_read_cover_letter(app),
        tailored_resume_json=app.get("tailored_resume_json"),
        apply_method=result.method,
        apply_result=_apply_result_payload(result),
    )
    return {
        "success": result.success,
        "method": result.method,
        "message": result.message,
        "screenshot": result.screenshot_path,
        "needs_verification": result.needs_verification,
    }


def _maybe_auto_fetch_otp(job_id: int, result: ApplyResult, app: dict) -> ApplyResult:
    if not result.needs_verification:
        return result
    cfg = get_config()
    if not cfg.get("otp_auto_fetch"):
        return result
    if not _imap_config_for_fetch():
        return result

    log_application_event(job_id, "otp_auto_fetch_started", {})
    try:
        code = _fetch_otp_for_job(job_id)
    except Exception as exc:
        log_application_event(job_id, "otp_auto_fetch_failed", {"error": str(exc)})
        return ApplyResult(
            False,
            result.method,
            f"Auto-fetch failed: {exc}. Enter the code manually.",
            result.screenshot_path,
            needs_verification=True,
        )

    if not code:
        log_application_event(job_id, "otp_auto_fetch_failed", {"error": "timeout"})
        return ApplyResult(
            False,
            result.method,
            "Auto-fetch timed out — enter the code manually.",
            result.screenshot_path,
            needs_verification=True,
        )

    log_application_event(job_id, "otp_auto_fetch_succeeded", {})
    verify_result = _run_complete_verification(job_id, code, app)
    if verify_result.success:
        return verify_result
    return ApplyResult(
        False,
        verify_result.method,
        verify_result.message,
        verify_result.screenshot_path,
        needs_verification=verify_result.needs_verification,
    )


def _run_complete_verification(job_id: int, otp_code: str, app: dict | None = None) -> ApplyResult:
    job = get_job(job_id)
    if not job:
        return ApplyResult(False, "none", "Job not found")
    if app is None:
        app = get_application(job_id) or {}
    cv_path = resolve_cv_path(app.get("tailored_cv_path", ""))
    if not app or not cv_path or not Path(cv_path).exists():
        return ApplyResult(False, "none", "No application materials — run generate first")

    url = job.get("url", "")
    method = detect_adapter(url)
    adapter = ADAPTERS.get(method)
    if method != "greenhouse" or not isinstance(adapter, GreenhouseApplyAdapter):
        return ApplyResult(False, method, "Verification is only supported for Greenhouse")

    return adapter.complete_verification(job, cv_path, _read_cover_letter(app), otp_code)


def apply_to_job(job_id: int, force: bool = False) -> dict:
    cfg = get_config()
    allowlist = cfg.get("auto_apply_sources", ["greenhouse", "lever"])
    max_per_day = cfg.get("max_applications_per_day", 5)

    if count_applications_today() >= max_per_day and not force:
        return {"success": False, "method": "none", "message": f"Daily cap ({max_per_day}) reached"}

    job = get_job(job_id)
    if not job:
        return {"success": False, "method": "none", "message": "Job not found"}

    app = get_application(job_id)
    cv_path = resolve_cv_path((app or {}).get("tailored_cv_path", ""))
    if not app or not cv_path or not Path(cv_path).exists():
        return {"success": False, "method": "none", "message": "No application materials — run generate first"}

    url = job.get("url", "")
    method = detect_adapter(url)
    if method == "generic":
        return {
            "success": False,
            "method": "generic",
            "message": f"No auto-apply adapter for {url}. Apply manually.",
        }

    if method not in allowlist and method != "email" and not force:
        return {"success": False, "method": method, "message": f"Adapter {method} not in allowlist"}

    adapter = ADAPTERS.get(method)
    if not adapter or not adapter.can_apply(url):
        return {"success": False, "method": method, "message": f"Adapter {method} cannot handle this URL"}

    result = adapter.apply(job, cv_path, _read_cover_letter(app))
    if result.needs_verification:
        result = _maybe_auto_fetch_otp(job_id, result, app)
    return _finalize_apply_result(job_id, result, app)


def complete_verification(job_id: int, otp_code: str) -> dict:
    job = get_job(job_id)
    if not job:
        return {"success": False, "method": "none", "message": "Job not found"}
    if job.get("status") != "needs_verification":
        return {
            "success": False,
            "method": "none",
            "message": f"Job status is {job.get('status')}, not awaiting verification",
        }

    app = get_application(job_id) or {}
    result = _run_complete_verification(job_id, otp_code, app)
    finalized = _finalize_apply_result(job_id, result, app)
    log_application_event(job_id, "otp_submitted", {"manual": True})
    return finalized


def try_auto_verify(job_id: int) -> dict:
    job = get_job(job_id)
    if not job:
        return {"success": False, "method": "none", "message": "Job not found"}
    if job.get("status") != "needs_verification":
        return {
            "success": False,
            "method": "none",
            "message": f"Job status is {job.get('status')}, not awaiting verification",
        }
    if not _imap_config_for_fetch():
        return {
            "success": False,
            "method": "none",
            "message": "Configure IMAP inbox access in Settings → Auto-apply",
        }

    app = get_application(job_id) or {}
    log_application_event(job_id, "otp_auto_fetch_started", {"manual_trigger": True})
    try:
        code = _fetch_otp_for_job(job_id)
    except Exception as exc:
        log_application_event(job_id, "otp_auto_fetch_failed", {"error": str(exc), "manual_trigger": True})
        return {"success": False, "method": "greenhouse", "message": f"Auto-fetch failed: {exc}"}

    if not code:
        log_application_event(job_id, "otp_auto_fetch_failed", {"error": "timeout", "manual_trigger": True})
        return {
            "success": False,
            "method": "greenhouse",
            "message": "No verification email found yet. Try again in a few seconds.",
            "needs_verification": True,
        }

    log_application_event(job_id, "otp_auto_fetch_succeeded", {"manual_trigger": True})
    result = _run_complete_verification(job_id, code, app)
    return _finalize_apply_result(job_id, result, app)


def retry_application(job_id: int, *, force: bool = True) -> dict:
    job = get_job(job_id)
    if not job:
        return {"job_id": job_id, "success": False, "method": "none", "message": "Job not found"}
    if job.get("status") not in ("failed", "needs_verification"):
        return {
            "job_id": job_id,
            "success": False,
            "method": "none",
            "message": f"Job status is {job.get('status')}, not failed or awaiting verification",
        }
    if job.get("status") == "needs_verification":
        return {
            "job_id": job_id,
            "success": False,
            "method": "none",
            "message": "Enter the email verification code instead of retrying",
        }
    result = apply_to_job(job_id, force=force)
    result["job_id"] = job_id
    return result


def retry_failed_applications(*, force: bool = True, limit: int = 50) -> dict:
    jobs = list_applications(status="failed", tab="history", limit=limit)
    results = [retry_application(job["id"], force=force) for job in jobs]
    succeeded = sum(1 for result in results if result.get("success"))
    return {
        "total": len(results),
        "succeeded": succeeded,
        "failed": len(results) - succeeded,
        "results": results,
    }
