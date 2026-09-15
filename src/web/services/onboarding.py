"""Onboarding checklist state for new users."""

from __future__ import annotations

from src.catalog_db import list_target_companies
from src.db import count_jobs_by_status, get_last_pipeline_run, get_profile


def get_onboarding_state(user_id: int) -> dict:
    profile = get_profile(user_id)
    counts = count_jobs_by_status(user_id)
    targets = list_target_companies(user_id)
    last_run = get_last_pipeline_run(user_id)

    steps = [
        {
            "id": "profile",
            "label": "Upload your CV",
            "done": profile is not None and bool(profile.get("resume_json")),
            "href": "/profile",
        },
        {
            "id": "keywords",
            "label": "Review keywords in Settings",
            "done": True,
            "href": "/settings?tab=keywords",
        },
        {
            "id": "targets",
            "label": "Add target companies (optional)",
            "done": len(targets) > 0,
            "href": "/targets",
        },
        {
            "id": "pipeline",
            "label": "Run your first pipeline",
            "done": last_run is not None,
            "href": "/pipeline",
        },
        {
            "id": "match",
            "label": "Review your first match",
            "done": counts.get("queued", 0) > 0 or counts.get("scored", 0) > 0,
            "href": "/jobs",
        },
    ]
    done_count = sum(1 for s in steps if s["done"])
    return {
        "steps": steps,
        "done_count": done_count,
        "total": len(steps),
        "complete": done_count >= len(steps) - 1,
        "show": done_count < len(steps),
    }
