"""Template helpers for status colors, scores, and pipeline visualization."""

from __future__ import annotations

import json
from typing import Any

STAGE_LABELS: dict[str, str] = {
    "crawl": "Crawl catalog",
    "enrich": "Fetch descriptions",
    "embed": "Embed jobs",
    "sync": "Sync to you",
    "prefilter": "Vector prefilter",
    "match": "Match",
    "generate": "Generate",
    "export": "Export",
    "done": "Done",
    "error": "Error",
    "idle": "Idle",
    "pending": "Pending",
}


def stage_label(stage_id: str) -> str:
    return STAGE_LABELS.get(stage_id, stage_id.replace("_", " ").title())


def score_tier(score: int | float | None) -> dict[str, str]:
    if score is None:
        return {
            "label": "Unscored",
            "text_class": "text-slate-500 dark:text-slate-400",
            "bar_class": "bg-slate-300 dark:bg-slate-600",
            "ring_class": "border-slate-300 dark:border-slate-600",
            "bg_class": "bg-slate-50 dark:bg-slate-800/50",
        }
    s = int(score)
    if s >= 80:
        return {
            "label": "Strong Match" if s < 90 else "Excellent",
            "text_class": "text-green-700 dark:text-green-400",
            "bar_class": "bg-green-500",
            "ring_class": "border-green-500",
            "bg_class": "bg-green-50 dark:bg-green-950/30",
        }
    if s >= 65:
        return {
            "label": "Moderate",
            "text_class": "text-amber-700 dark:text-amber-400",
            "bar_class": "bg-amber-500",
            "ring_class": "border-amber-500",
            "bg_class": "bg-amber-50 dark:bg-amber-950/30",
        }
    return {
        "label": "Weak",
        "text_class": "text-red-700 dark:text-red-400",
        "bar_class": "bg-red-500",
        "ring_class": "border-red-500",
        "bg_class": "bg-red-50 dark:bg-red-950/30",
    }


def status_badge_classes(status: str) -> str:
    base = "px-2 py-0.5 rounded-full text-xs font-medium"
    mapping = {
        "applied": f"{base} bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300",
        "approved": f"{base} bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300",
        "queued": f"{base} bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300",
        "skipped": f"{base} bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400",
        "failed": f"{base} bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300",
        "needs_verification": f"{base} bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300",
        "rejected": f"{base} bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300",
        "scored": f"{base} bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-300",
        "new": f"{base} bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400",
        "running": f"{base} bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-300 animate-pulse",
    }
    return mapping.get(status, f"{base} bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300")


def status_count_classes(status: str) -> str:
    base = "px-3 py-1.5 rounded-lg text-sm font-medium transition-colors"
    mapping = {
        "applied": f"{base} bg-green-100 text-green-800 hover:bg-green-200 dark:bg-green-900/40 dark:text-green-300 dark:hover:bg-green-900/60",
        "approved": f"{base} bg-green-100 text-green-800 hover:bg-green-200 dark:bg-green-900/40 dark:text-green-300 dark:hover:bg-green-900/60",
        "queued": f"{base} bg-amber-100 text-amber-800 hover:bg-amber-200 dark:bg-amber-900/40 dark:text-amber-300 dark:hover:bg-amber-900/60",
        "skipped": f"{base} bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-400 dark:hover:bg-slate-700",
        "failed": f"{base} bg-red-100 text-red-800 hover:bg-red-200 dark:bg-red-900/40 dark:text-red-300 dark:hover:bg-red-900/60",
        "needs_verification": f"{base} bg-amber-100 text-amber-800 hover:bg-amber-200 dark:bg-amber-900/40 dark:text-amber-300 dark:hover:bg-amber-900/60",
        "scored": f"{base} bg-blue-100 text-blue-800 hover:bg-blue-200 dark:bg-blue-900/40 dark:text-blue-300 dark:hover:bg-blue-900/60",
        "new": f"{base} bg-slate-100 text-slate-700 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700",
        "rejected": f"{base} bg-red-100 text-red-800 hover:bg-red-200 dark:bg-red-900/40 dark:text-red-300 dark:hover:bg-red-900/60",
    }
    return mapping.get(status, f"{base} bg-slate-100 text-slate-700 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700")


def parse_match_details(job: dict[str, Any]) -> dict[str, Any] | None:
    raw = job.get("match_details")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def enrich_review_job(job: dict[str, Any]) -> dict[str, Any]:
    match = parse_match_details(job) or {}
    job = dict(job)
    job["match_strengths"] = match.get("strengths", [])[:3]
    job["match_gaps"] = match.get("gaps", [])[:2]
    return job


def pipeline_stages(readiness: dict[str, Any]) -> list[dict[str, Any]]:
    counts = readiness.get("status_counts") or {}
    total = sum(counts.values())
    matched = (
        readiness.get("jobs_scored", 0)
        + readiness.get("jobs_queued", 0)
        + readiness.get("jobs_approved", 0)
        + readiness.get("jobs_applied", 0)
    )
    generated = (
        readiness.get("jobs_queued", 0)
        + readiness.get("jobs_approved", 0)
        + readiness.get("jobs_applied", 0)
    )
    enriched = max(total - readiness.get("jobs_needing_enrich", 0), 0)

    stage_defs = [
        ("crawl", "Crawl catalog", total, True, "Platform sources"),
        ("enrich", stage_label("enrich"), readiness.get("jobs_needing_enrich", 0), True, "Catalog descriptions"),
        ("embed", "Embed", total, True, "Vector index"),
        ("sync", "Sync", counts.get("new", 0), True, "New in your queue"),
        ("prefilter", "Prefilter", counts.get("new", 0), True, "Vector gate"),
        ("match", "Match", matched, True, "ATS scoring"),
        ("generate", "Generate", readiness.get("jobs_queued", 0), True, "Tailor + cover letter"),
        ("export", "Export", 1 if total else 0, True, "Excel export"),
    ]
    stages: list[dict[str, Any]] = []
    for stage_id, label, count, runnable, hint in stage_defs:
        complete = False
        if stage_id == "crawl":
            complete = total > 0
        elif stage_id == "enrich":
            complete = readiness.get("jobs_needing_enrich", 0) == 0 and total > 0
        elif stage_id in ("embed", "sync", "prefilter"):
            complete = total > 0
        elif stage_id == "match":
            complete = matched > 0
        elif stage_id == "generate":
            complete = generated > 0
        elif stage_id == "export":
            complete = False
        elif stage_id == "apply":
            complete = readiness.get("jobs_applied", 0) > 0
        stages.append(
            {
                "id": stage_id,
                "label": label,
                "count": count,
                "hint": hint,
                "complete": complete,
                "runnable": runnable,
                "status": "complete" if complete else "pending",
            }
        )
    return stages


def pipeline_health(readiness: dict[str, Any], run_state: dict[str, Any] | None = None) -> dict[str, Any]:
    stages = pipeline_stages(readiness)
    running_stage = None
    if run_state and run_state.get("running"):
        running_stage = run_state.get("stage")
        for stage in stages:
            if stage["id"] == running_stage:
                stage["status"] = "running"
            elif not stage["complete"]:
                stage["status"] = "pending"

    completed = sum(1 for s in stages if s["complete"])
    pct = int(completed / len(stages) * 100) if stages else 0
    return {"stages": stages, "percent": pct, "running_stage": running_stage}


def recent_activity(readiness: dict[str, Any]) -> list[dict[str, str]]:
    counts = readiness.get("status_counts") or {}
    total = sum(counts.values())
    matched = (
        readiness.get("jobs_scored", 0)
        + readiness.get("jobs_queued", 0)
        + readiness.get("jobs_approved", 0)
    )
    generated = readiness.get("jobs_queued", 0) + readiness.get("jobs_approved", 0)
    applied = readiness.get("jobs_applied", 0)

    items: list[dict[str, str]] = []
    if total:
        items.append({"kind": "success", "text": f"Crawled {total} jobs"})
    if matched:
        items.append({"kind": "success", "text": f"Matched {matched} jobs"})
    if generated:
        items.append({"kind": "success", "text": f"Generated {generated} cover letters"})
    if applied:
        items.append({"kind": "success", "text": f"Applied to {applied} jobs"})
    if readiness.get("jobs_needing_enrich", 0):
        items.append(
            {
                "kind": "warning",
                "text": f"{readiness['jobs_needing_enrich']} jobs need full descriptions",
            }
        )
    if counts.get("failed"):
        items.append({"kind": "error", "text": f"{counts['failed']} applications failed"})
    return items


def parse_apply_result(raw: str | None) -> str:
    if not raw:
        return ""
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return str(data.get("message") or raw)
    except (json.JSONDecodeError, TypeError):
        pass
    return str(raw)


_HISTORY_STATUSES = frozenset({"applied", "rejected", "failed"})


def applications_url(
    status: str | None = None,
    tab: str | None = None,
    min_score: str | float | None = None,
) -> str:
    params: list[str] = []
    resolved_tab = tab
    if resolved_tab not in ("active", "history"):
        resolved_tab = "history" if status in _HISTORY_STATUSES else "active"
    params.append(f"tab={resolved_tab}")
    if status:
        params.append(f"status={status}")
    if min_score not in (None, ""):
        params.append(f"min_score={min_score}")
    return f"/applications?{'&'.join(params)}"
