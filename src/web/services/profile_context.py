"""Profile + hiring_agent evaluation context for templates."""

from __future__ import annotations

from typing import Any

from src.db import get_profile
from src.hiring_agent_bridge import evaluation_total_score

EVAL_CATEGORIES: list[tuple[str, str]] = [
    ("open_source", "Open source"),
    ("self_projects", "Self projects"),
    ("production", "Production"),
    ("technical_skills", "Technical skills"),
]


def get_profile_summary() -> dict[str, Any]:
    profile = get_profile()
    if not profile:
        return {
            "loaded": False,
            "evaluated": False,
            "score": None,
            "evaluation": None,
            "github_loaded": False,
            "name": "",
            "categories": [],
            "strengths": [],
            "improvements": [],
            "updated_at": None,
        }

    resume = profile.get("resume_json") or {}
    basics = resume.get("basics") or {}
    evaluation = profile.get("evaluation_json")
    categories: list[dict[str, Any]] = []

    if evaluation:
        scores = evaluation.get("scores") or {}
        for key, label in EVAL_CATEGORIES:
            block = scores.get(key) or {}
            max_val = block.get("max") or 1
            score = block.get("score") or 0
            categories.append(
                {
                    "key": key,
                    "label": label,
                    "score": score,
                    "max": max_val,
                    "pct": int(score / max_val * 100) if max_val else 0,
                    "evidence": block.get("evidence") or "",
                }
            )

    github = profile.get("github_json") or {}
    github_loaded = bool(github and (github.get("username") or github.get("repos")))

    return {
        "loaded": True,
        "evaluated": bool(evaluation),
        "score": evaluation_total_score(evaluation) if evaluation else None,
        "evaluation": evaluation,
        "github_loaded": github_loaded,
        "name": basics.get("name") or "",
        "categories": categories,
        "strengths": (evaluation or {}).get("key_strengths") or [],
        "improvements": (evaluation or {}).get("areas_for_improvement") or [],
        "updated_at": profile.get("updated_at"),
    }
