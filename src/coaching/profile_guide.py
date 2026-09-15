"""Personalized profile improvement guide (CV, GitHub, OSS) with file cache."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from src.coaching.skills_gap import aggregate_gaps
from src.config import get_config
from src.db import get_profile
from src.llm import chat_json, render_template
from src.settings import user_profile_dir
from src.tenant import resolve_user_id
from src.web.services.profile_context import EVAL_CATEGORIES


class GuideTip(BaseModel):
    title: str
    detail: str


class OSSSuggestion(BaseModel):
    name: str
    url: str
    related_project: str
    why: str
    first_step: str


class ProfileGuideReport(BaseModel):
    summary: str = ""
    cv_tips: list[GuideTip] = Field(default_factory=list)
    github_tips: list[GuideTip] = Field(default_factory=list)
    oss_projects: list[OSSSuggestion] = Field(default_factory=list)


class CachedProfileGuide(BaseModel):
    cache_key: str
    generated_at: str
    report: ProfileGuideReport


def _cache_path(user_id: int) -> Path:
    return user_profile_dir(user_id) / "guide.json"


def _gap_hash(freq: Counter[str]) -> str:
    if not freq:
        return "no-gaps"
    top = tuple(freq.most_common(12))
    return hashlib.sha256(repr(top).encode()).hexdigest()[:16]


def cache_key(profile: dict, freq: Counter[str]) -> str:
    updated = profile.get("updated_at") or ""
    return hashlib.sha256(f"{updated}:{_gap_hash(freq)}".encode()).hexdigest()[:32]


def _norm_project_key(name: str, url: str) -> str:
    key = (url or name or "").strip().lower().rstrip("/")
    if key.startswith("https://github.com/"):
        parts = key.replace("https://github.com/", "").split("/")
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"
    return key or name.strip().lower()


def collect_user_projects(profile: dict) -> list[dict[str, Any]]:
    """Merge CV and GitHub projects, deduped by name or URL."""
    resume = profile.get("resume_json") or {}
    github = profile.get("github_json") or {}
    seen: set[str] = set()
    projects: list[dict[str, Any]] = []

    def add(
        name: str,
        description: str,
        url: str,
        technologies: list[str],
        source: str,
        project_type: str = "",
    ) -> None:
        name = (name or "").strip()
        if not name:
            return
        key = _norm_project_key(name, url)
        if key in seen:
            return
        seen.add(key)
        projects.append(
            {
                "name": name,
                "description": (description or "")[:300],
                "url": url or "",
                "technologies": technologies[:12],
                "source": source,
                "project_type": project_type or "",
            }
        )

    for p in resume.get("projects") or []:
        if not isinstance(p, dict):
            continue
        techs = p.get("technologies") or []
        if isinstance(techs, str):
            techs = [techs]
        add(
            p.get("name") or "",
            p.get("description") or "",
            p.get("url") or p.get("github_url") or "",
            [str(t) for t in techs],
            "cv",
        )

    for w in resume.get("work") or []:
        if not isinstance(w, dict):
            continue
        if not (w.get("summary") or w.get("highlights")):
            continue
        desc = w.get("summary") or ""
        highlights = w.get("highlights") or []
        if highlights:
            desc = f"{desc} {'; '.join(str(h) for h in highlights[:3])}".strip()
        add(
            w.get("name") or w.get("position") or "",
            desc,
            w.get("url") or "",
            [],
            "cv",
            project_type="work",
        )

    gh_projects = github.get("projects") or github.get("repos") or []
    for p in gh_projects:
        if not isinstance(p, dict):
            continue
        techs = p.get("technologies") or []
        add(
            p.get("name") or "",
            p.get("description") or "",
            p.get("github_url") or p.get("url") or "",
            [str(t) for t in techs],
            "github",
            project_type=p.get("project_type") or "",
        )

    return projects[:25]


def extract_stack(profile: dict) -> list[str]:
    """Flatten resume skills and GitHub repo languages into a deduped list."""
    resume = profile.get("resume_json") or {}
    seen: set[str] = set()
    stack: list[str] = []

    def add(item: str) -> None:
        key = item.strip().lower()
        if key and key not in seen:
            seen.add(key)
            stack.append(item.strip())

    for group in resume.get("skills") or []:
        if isinstance(group, dict):
            for kw in group.get("keywords") or []:
                add(str(kw))
            if group.get("name"):
                add(str(group["name"]))
        elif isinstance(group, str):
            add(group)

    github = profile.get("github_json") or {}
    projects = github.get("projects") or github.get("repos") or []
    for proj in projects:
        if not isinstance(proj, dict):
            continue
        for tech in proj.get("technologies") or []:
            add(str(tech))
        details = proj.get("github_details") or {}
        for lang in details.get("languages") or []:
            add(str(lang))

    return stack[:40]


def weak_categories(evaluation: dict | None) -> list[dict[str, Any]]:
    """Return evaluation categories scoring below 50% of max."""
    if not evaluation:
        return []
    scores = evaluation.get("scores") or {}
    weak: list[dict[str, Any]] = []
    for key, label in EVAL_CATEGORIES:
        block = scores.get(key) or {}
        max_val = block.get("max") or 1
        score = block.get("score") or 0
        pct = score / max_val if max_val else 0
        if pct < 0.5:
            weak.append(
                {
                    "key": key,
                    "label": label,
                    "score": score,
                    "max": max_val,
                    "pct": int(pct * 100),
                    "evidence": block.get("evidence") or "",
                }
            )
    return weak


def _profile_context(profile: dict, freq: Counter[str]) -> dict[str, Any]:
    resume = profile.get("resume_json") or {}
    basics = resume.get("basics") or {}
    github = profile.get("github_json") or {}
    evaluation = profile.get("evaluation_json")
    projects = resume.get("projects") or []
    work = resume.get("work") or []

    projects_with_url = sum(
        1
        for p in projects
        if isinstance(p, dict) and (p.get("url") or p.get("github_url"))
    )

    gh_profile = github.get("profile") or github
    gh_projects = github.get("projects") or github.get("repos") or []
    self_owned = sum(
        1
        for p in gh_projects
        if isinstance(p, dict) and p.get("project_type") == "self_project"
    )
    username = gh_profile.get("username") or github.get("username") or ""

    user_projects = collect_user_projects(profile)

    return {
        "name": basics.get("name") or "",
        "label": basics.get("label") or "",
        "summary_snippet": (basics.get("summary") or "")[:400],
        "stack": extract_stack(profile),
        "user_projects": user_projects,
        "work_count": len(work),
        "project_count": len(projects),
        "projects_with_url": projects_with_url,
        "github_username": username,
        "github_repo_count": len(gh_projects) or github.get("total_projects") or 0,
        "github_self_owned_ratio": (
            f"{self_owned}/{len(gh_projects)}" if gh_projects else "unknown"
        ),
        "evaluation_categories": weak_categories(evaluation),
        "areas_for_improvement": (evaluation or {}).get("areas_for_improvement") or [],
        "key_strengths": (evaluation or {}).get("key_strengths") or [],
        "top_gaps": [g for g, _ in freq.most_common(10)],
    }


def load_cached_guide(user_id: int | None = None) -> CachedProfileGuide | None:
    uid = resolve_user_id(user_id)
    path = _cache_path(uid)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return CachedProfileGuide(**data)
    except (json.JSONDecodeError, OSError, ValueError):
        return None


def save_cached_guide(
    user_id: int,
    key: str,
    report: ProfileGuideReport,
) -> CachedProfileGuide:
    cached = CachedProfileGuide(
        cache_key=key,
        generated_at=datetime.now(timezone.utc).isoformat(),
        report=report,
    )
    path = _cache_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(cached.model_dump_json(indent=2), encoding="utf-8")
    return cached


def guide_cache_status(user_id: int | None = None) -> dict[str, Any]:
    """Return cached guide and whether it is stale relative to current profile/gaps."""
    uid = resolve_user_id(user_id)
    profile = get_profile(uid)
    if not profile:
        return {
            "profile_loaded": False,
            "guide": None,
            "guide_stale": False,
            "generated_at": None,
        }

    freq = aggregate_gaps(uid)
    key = cache_key(profile, freq)
    cached = load_cached_guide(uid)
    valid = cached is not None and cached.cache_key == key

    return {
        "profile_loaded": True,
        "guide": cached.report if valid else None,
        "guide_stale": cached is not None and cached.cache_key != key,
        "generated_at": cached.generated_at if cached else None,
        "has_cached_stale": cached is not None and cached.cache_key != key,
    }


def build_profile_guide(
    user_id: int | None = None,
    *,
    use_llm: bool = True,
    force: bool = False,
) -> ProfileGuideReport:
    uid = resolve_user_id(user_id)
    profile = get_profile(uid)
    if not profile:
        return ProfileGuideReport(summary="Upload a CV on the Profile page to generate a guide.")

    freq = aggregate_gaps(uid)
    key = cache_key(profile, freq)

    if not force:
        cached = load_cached_guide(uid)
        if cached and cached.cache_key == key:
            return cached.report

    ctx = _profile_context(profile, freq)
    base = ProfileGuideReport(
        summary="Personalized profile improvement guide based on your CV, GitHub, and job match gaps.",
    )

    if not use_llm:
        return base

    cfg = get_config()
    model = cfg["quality_model"]
    try:
        system = render_template("profile_guide_system.jinja")
        user_prompt = render_template("profile_guide_criteria.jinja", **ctx)
        data = chat_json(user_prompt, system=system, model=model, schema=ProfileGuideReport)
        report = ProfileGuideReport(**data)
        save_cached_guide(uid, key, report)
        return report
    except Exception as e:
        stale = load_cached_guide(uid)
        if stale and not force:
            stale.report.summary = f"{stale.report.summary} (Refresh failed: {e})"
            return stale.report
        base.summary = f"Guide generation failed: {e}"
        return base
