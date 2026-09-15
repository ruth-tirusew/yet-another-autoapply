"""Background single-job ATS scoring for the web UI."""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

from src.tenant import set_tenant_user_id

_lock = threading.Lock()
_active: dict[int, dict[str, Any]] = {}


def is_job_scoring(job_id: int) -> bool:
    return job_id in _active


def get_job_score_state(job_id: int) -> dict[str, Any] | None:
    return _active.get(job_id)


def start_job_score(job_id: int, user_id: int) -> bool:
    with _lock:
        if job_id in _active:
            return False
        _active[job_id] = {
            "user_id": user_id,
            "started_at": datetime.utcnow().isoformat(),
            "error": None,
        }
    thread = threading.Thread(target=_worker, args=(job_id, user_id), daemon=True)
    thread.start()
    return True


def _worker(job_id: int, user_id: int) -> None:
    set_tenant_user_id(user_id)
    try:
        from src.matcher import match_job

        match_job(job_id)
    except Exception as e:
        with _lock:
            if job_id in _active:
                _active[job_id]["error"] = str(e)
    finally:
        with _lock:
            _active.pop(job_id, None)
