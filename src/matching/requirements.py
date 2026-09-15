"""Stage 1 — extract structured requirements from a posting, once per posting.

The extraction is cached on the shared ``catalog_jobs`` row and keyed by a
hash of the text it was derived from, so a posting whose description is later
enriched is re-extracted automatically while every user who already has that
job in their queue reuses the same requirement objects.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from src.catalog_db import update_catalog_job
from src.config_store import get_merged_config
from src.db import connect
from src.llm import chat_json, render_template
from src.matching.chunking import heuristic_requirements, requirement_section
from src.matching.models import Requirement, RequirementSet
from src.matching.scoring import extract_required_years

logger = logging.getLogger(__name__)

PROMPT_VERSION = "req-v1"
MAX_REQUIREMENTS = 18
EXTRACT_CHARS = 6000

_SENIORITY_WORDS = (
    "intern", "junior", "mid", "senior", "staff", "principal",
    "lead", "manager", "director", "vp",
)


class _ExtractedRequirement(BaseModel):
    text: str = ""
    kind: str = "must"
    category: str = "skill"
    terms: list[str] = Field(default_factory=list)


class _ExtractionResponse(BaseModel):
    seniority: str = ""
    years_required: int | None = None
    requirements: list[_ExtractedRequirement] = Field(default_factory=list)


def source_text(job: dict[str, Any]) -> str:
    """The posting text requirements are derived from: title + requirements section."""
    description = job.get("description_full") or job.get("description_short") or ""
    section = requirement_section(description)
    return f"{job.get('title') or ''}\n\n{section}".strip()


def source_hash(job: dict[str, Any]) -> str:
    return hashlib.sha256(source_text(job).encode("utf-8")).hexdigest()[:32]


def _normalize(items: list[_ExtractedRequirement]) -> list[Requirement]:
    out: list[Requirement] = []
    seen: set[str] = set()
    for item in items:
        text = (item.text or "").strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        kind = "nice" if str(item.kind).strip().lower().startswith("nice") else "must"
        category = str(item.category or "skill").strip().lower()
        if category not in ("skill", "experience", "responsibility", "education", "other"):
            category = "other"
        out.append(
            Requirement(
                id=f"r{len(out) + 1}",
                text=text,
                kind=kind,
                category=category,  # type: ignore[arg-type]
                terms=[str(t).strip().lower() for t in item.terms if str(t).strip()],
            )
        )
        if len(out) >= MAX_REQUIREMENTS:
            break
    return out


def _seniority_from_title(title: str) -> str:
    lowered = (title or "").lower()
    for word in _SENIORITY_WORDS:
        if word in lowered:
            return word
    return ""


def heuristic_extract(job: dict[str, Any]) -> RequirementSet:
    """LLM-free extraction: bullet lines from the requirements section."""
    description = job.get("description_full") or job.get("description_short") or ""
    pairs = heuristic_requirements(description, limit=MAX_REQUIREMENTS)
    requirements = _normalize(
        [_ExtractedRequirement(text=text, kind=kind, category="other") for text, kind in pairs]
    )
    return RequirementSet(
        requirements=requirements,
        years_required=extract_required_years(requirement_section(description)),
        seniority=_seniority_from_title(job.get("title", "")),
        source_hash=source_hash(job),
        model="",
        engine="heuristic",
        extracted_at=datetime.now(timezone.utc).isoformat(),
    )


def llm_extract(job: dict[str, Any], *, model: str | None = None) -> RequirementSet:
    """Extract requirements with the tenant's configured model."""
    cfg = get_merged_config()
    model = model or cfg["quality_model"]
    text = source_text(job)[:EXTRACT_CHARS]
    prompt = render_template(
        "requirements_extract.jinja",
        job_title=job.get("title", "") or "",
        job_company=job.get("company", "") or "",
        job_text=text,
        max_requirements=MAX_REQUIREMENTS,
    )
    data = chat_json(
        prompt,
        system=render_template("requirements_system.jinja"),
        model=model,
        schema=_ExtractionResponse,
        temperature=0.0,
    )
    parsed = _ExtractionResponse(**data)
    seniority = (parsed.seniority or "").strip().lower()
    if seniority not in _SENIORITY_WORDS:
        seniority = _seniority_from_title(job.get("title", ""))
    return RequirementSet(
        requirements=_normalize(parsed.requirements),
        years_required=parsed.years_required,
        seniority=seniority,
        source_hash=source_hash(job),
        model=model,
        engine="llm",
        extracted_at=datetime.now(timezone.utc).isoformat(),
    )


def load_cached(catalog_job_id: int, expected_hash: str) -> RequirementSet | None:
    """Return the cached requirement set when it was built from the current text."""
    with connect() as conn:
        row = conn.execute(
            "SELECT requirements_json, requirements_hash FROM catalog_jobs WHERE id = ?",
            (catalog_job_id,),
        ).fetchone()
    if not row or not row["requirements_json"]:
        return None
    if row["requirements_hash"] != expected_hash:
        return None  # description changed since extraction (e.g. enrichment filled it in)
    try:
        return RequirementSet(**json.loads(row["requirements_json"]))
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def save_cached(catalog_job_id: int, req_set: RequirementSet) -> None:
    update_catalog_job(
        catalog_job_id,
        requirements_json=req_set.model_dump_json(),
        requirements_hash=req_set.source_hash,
        requirements_model=req_set.model,
        requirements_at=req_set.extracted_at,
    )


def extract_requirements(
    job: dict[str, Any],
    *,
    force: bool = False,
    allow_llm: bool = True,
) -> RequirementSet:
    """Return the posting's requirements, extracting and caching them if needed.

    Heuristic results are deliberately not cached: they are a degraded
    fallback, and the next run with a reachable model should upgrade them.
    """
    expected = source_hash(job)
    catalog_job_id = job.get("catalog_job_id") or job.get("id")

    if not force and catalog_job_id:
        cached = load_cached(int(catalog_job_id), expected)
        if cached and cached.requirements:
            return cached

    if allow_llm:
        try:
            req_set = llm_extract(job)
            if req_set.requirements:
                if catalog_job_id:
                    save_cached(int(catalog_job_id), req_set)
                return req_set
            logger.info("[requirements] job %s: model returned no requirements", catalog_job_id)
        except Exception as e:
            logger.warning("[requirements] job %s: extraction failed (%s)", catalog_job_id, e)

    return heuristic_extract(job)
