"""Format resume data for prompts."""

from __future__ import annotations

from src.niche_terms import (
    posting_mentions_niche_domain,
    text_mentions_niche_domain,
)


def normalize_skills(skills: list | dict | str | None) -> list[dict]:
    """Coerce skills into JSON Resume skill objects for rendering and previews."""
    if not skills:
        return []
    if isinstance(skills, dict):
        skills = [skills]
    elif isinstance(skills, str):
        text = skills.strip()
        return [{"name": text}] if text else []
    elif not isinstance(skills, list):
        return []
    out: list[dict] = []
    for item in skills:
        if isinstance(item, str):
            text = item.strip()
            if text:
                out.append({"name": text})
            continue
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        keywords = [
            str(kw).strip()
            for kw in (item.get("keywords") or [])
            if str(kw).strip()
        ]
        if name:
            entry: dict = {"name": name}
            if keywords:
                entry["keywords"] = keywords
            out.append(entry)
        elif keywords:
            out.extend({"name": kw} for kw in keywords)
    return out

_NICHE_SUMMARY_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("Sharia-compliant financing solutions", "lending-platform backend systems"),
    ("Sharia-compliant financial products", "core lending products"),
    ("Sharia-compliant finance", "backend systems for lending platforms"),
    ("Murabaha", "structured lending"),
)


def soften_niche_phrasing(text: str) -> str:
    """Rephrase niche finance terms for general engineering contexts."""
    result = text
    for old, new in _NICHE_SUMMARY_REPLACEMENTS:
        result = result.replace(old, new)
    return result


def summary_for_job(
    summary: str,
    *,
    job_title: str = "",
    job_description: str = "",
    user_id: int | None = None,
    niche_terms: list[str] | None = None,
) -> str:
    if not summary:
        return summary
    if posting_mentions_niche_domain(
        job_title, job_description, user_id=user_id, niche_terms=niche_terms
    ):
        return summary
    if text_mentions_niche_domain(summary, user_id=user_id, niche_terms=niche_terms):
        return soften_niche_phrasing(summary)
    return summary


def _project_sort_key(project: dict, *, user_id: int | None = None, niche_terms: list[str] | None = None) -> tuple[int, str]:
    desc = project.get("description") or ""
    name = project.get("name") or ""
    niche = text_mentions_niche_domain(
        f"{name} {desc}", user_id=user_id, niche_terms=niche_terms
    )
    return (1 if niche else 0, name.lower())


def format_experience_text(
    resume: dict,
    limit: int = 5,
    *,
    job_title: str = "",
    job_description: str = "",
    user_id: int | None = None,
    niche_terms: list[str] | None = None,
) -> str:
    niche_ok = posting_mentions_niche_domain(
        job_title, job_description, user_id=user_id, niche_terms=niche_terms
    )
    parts: list[str] = []
    for w in (resume.get("work") or [])[:limit]:
        summary = (w.get("summary") or "")[:220]
        if not niche_ok and text_mentions_niche_domain(
            summary, user_id=user_id, niche_terms=niche_terms
        ):
            summary = soften_niche_phrasing(summary)
        parts.append(f"{w.get('position', '')} at {w.get('name', '')}: {summary}")

    remaining = max(0, limit - len(parts))
    projects = list(resume.get("projects") or [])
    if not niche_ok:
        projects = sorted(
            projects,
            key=lambda p: _project_sort_key(p, user_id=user_id, niche_terms=niche_terms),
        )

    for p in projects[:remaining]:
        tech = ", ".join((p.get("technologies") or p.get("skills") or [])[:6])
        desc = (p.get("description") or "")[:220]
        if not niche_ok and text_mentions_niche_domain(
            desc, user_id=user_id, niche_terms=niche_terms
        ):
            desc = soften_niche_phrasing(desc)
        suffix = f" [{tech}]" if tech else ""
        parts.append(f"{p.get('name', 'Project')}: {desc}{suffix}")
    return "\n".join(parts) or "(no structured experience — use summary and skills only)"
