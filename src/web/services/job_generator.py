"""Background CV/cover-letter generation for the web UI."""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any, Literal

from src.tenant import set_tenant_user_id

GenerateMode = Literal["full", "tailor"]

_lock = threading.Lock()
_active: dict[int, dict[str, Any]] = {}
_last_result: dict[int, dict[str, Any]] = {}


def is_job_generating(job_id: int) -> bool:
    return job_id in _active


def get_job_generate_state(job_id: int) -> dict[str, Any] | None:
    return _active.get(job_id)


def consume_generate_result(job_id: int) -> dict[str, Any] | None:
    with _lock:
        return _last_result.pop(job_id, None)


def _set_stage(job_id: int, stage: str) -> None:
    with _lock:
        if job_id in _active:
            _active[job_id]["stage"] = stage


def start_job_generate(job_id: int, user_id: int, *, mode: GenerateMode = "full") -> bool:
    with _lock:
        if job_id in _active:
            return False
        _last_result.pop(job_id, None)
        _active[job_id] = {
            "user_id": user_id,
            "mode": mode,
            "stage": "starting",
            "started_at": datetime.utcnow().isoformat(),
            "error": None,
            "skipped": None,
        }
    thread = threading.Thread(target=_worker, args=(job_id, user_id, mode), daemon=True)
    thread.start()
    return True


def _worker(job_id: int, user_id: int, mode: GenerateMode) -> None:
    set_tenant_user_id(user_id)
    try:
        from src.cover_letter import CoverLetterSkipped, generate_cover_letter
        from src.tailor import TailorSkipped, tailor_cv

        if mode in ("full", "tailor"):
            _set_stage(job_id, "tailoring")
            tailor_cv(job_id, force=True)
        if mode == "full":
            _set_stage(job_id, "cover_letter")
            generate_cover_letter(job_id, force=True)
    except (TailorSkipped, CoverLetterSkipped) as e:
        with _lock:
            _last_result[job_id] = {"skipped": str(e)}
    except Exception as e:
        with _lock:
            _last_result[job_id] = {"error": str(e)}
    finally:
        with _lock:
            _active.pop(job_id, None)
