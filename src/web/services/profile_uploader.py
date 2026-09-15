"""Background CV upload/extraction for the web UI.

upload_cv() runs PDF extraction, GitHub enrichment, hiring_agent evaluation, and
generalized-resume building synchronously — often a minute or more. Running it
inline in the request handler leaves the browser hanging with no feedback, so it
runs on a worker thread here instead, polled the same way job scoring/generation
already are (src/web/services/job_scorer.py, job_generator.py).
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

from src.tenant import set_tenant_user_id

_lock = threading.Lock()
_active: dict[int, dict[str, Any]] = {}
_last_result: dict[int, dict[str, Any]] = {}


def is_profile_uploading(user_id: int) -> bool:
    return user_id in _active


def get_profile_upload_state(user_id: int) -> dict[str, Any] | None:
    return _active.get(user_id)


def consume_profile_upload_result(user_id: int) -> dict[str, Any] | None:
    with _lock:
        return _last_result.pop(user_id, None)


def start_profile_upload(user_id: int, pdf_path: str) -> bool:
    with _lock:
        if user_id in _active:
            return False
        _last_result.pop(user_id, None)
        _active[user_id] = {
            "started_at": datetime.utcnow().isoformat(),
            "error": None,
        }
    thread = threading.Thread(target=_worker, args=(user_id, pdf_path), daemon=True)
    thread.start()
    return True


def _worker(user_id: int, pdf_path: str) -> None:
    set_tenant_user_id(user_id)
    try:
        from src.profile import upload_cv

        result = upload_cv(pdf_path, user_id=user_id)
        with _lock:
            _last_result[user_id] = {"error": None, "warnings": result.get("warnings") or []}
    except Exception as e:
        with _lock:
            _last_result[user_id] = {"error": str(e), "warnings": []}
    finally:
        with _lock:
            _active.pop(user_id, None)
