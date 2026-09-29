"""Bridge to vendored hiring_agent (PDF extract, resume text, profile evaluation)."""

from __future__ import annotations

import logging
import os
import re
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from src.niche_terms import (
    partition_strengths,
    posting_mentions_niche_domain,
    text_mentions_niche_domain,
)
from src.profile_format import normalize_skills, soften_niche_phrasing
from src.config_store import get_merged_config
from src.settings import VENDOR_HIRING_AGENT

logger = logging.getLogger(__name__)

EVALUATION_MODEL_PARAMS = {"temperature": 0.0, "top_p": 1.0}

# Mirrors the category limits in resume_evaluation_criteria.jinja. The grader
# doesn't reliably respect them (it has returned 40/35), so they're enforced here.
CATEGORY_MAX = {
    "open_source": 20,
    "self_projects": 25,
    "production": 35,
    "technical_skills": 20,
}
# The rubric's cap for candidates whose GitHub repos are all their own.
SELF_ONLY_OPEN_SOURCE_MAX = 8

_MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?"
# A bullet that opens with bold text and carries a date range is a job header
# pymupdf4llm mis-rendered as a list item, e.g.
# "- **Backend Developer · Rahove** Sep 2023 – Mar 2024".
_BULLETED_HEADER = re.compile(
    rf"^[ \t]*[-*•][ \t]+(\*\*[^*\n]+\*\*.*?\b{_MONTH}[ \t]+\d{{4}}[ \t]*[–—-].*)$",
    re.MULTILINE,
)

_ha_configured = False
_ha_lock = threading.RLock()


class HiringAgentError(Exception):
    """Raised when a hiring_agent PDF extraction or profile evaluation call fails.

    Previously these failures were caught and turned into a falsy return
    (None), which let profile upload/evaluation appear to succeed silently
    even when Ollama was unreachable, an API key was missing or wrong, or a
    required package wasn't installed. Raising instead lets callers decide
    whether to fall back, warn, or surface the real cause to the user.
    """


def configure_hiring_agent_env(user_id: int | None = None) -> None:
    """Set env vars before any hiring_agent module is imported.

    Provider selection and API keys are no longer passed via env vars —
    hiring_agent's initialize_llm_provider() delegates to
    src.llm.factory.get_provider(), which reads the user's configured
    provider and encrypted keys straight from the database via the
    ambient tenant context. Only OLLAMA_HOST/DEFAULT_MODEL remain here,
    since vendor/hiring_agent/prompt.py still reads DEFAULT_MODEL directly.
    """
    global _ha_configured
    from src.config_store import get_merged_config
    from src.tenant import get_tenant_user_id

    uid = user_id if user_id is not None else get_tenant_user_id()
    cfg = get_merged_config(uid)
    os.environ["OLLAMA_HOST"] = cfg["ollama_host"]
    os.environ["DEFAULT_MODEL"] = cfg["quality_model"]
    if not _ha_configured:
        ha = str(VENDOR_HIRING_AGENT)
        if ha not in sys.path:
            sys.path.insert(0, ha)
        _ha_configured = True


@contextmanager
def hiring_agent_context() -> Iterator[None]:
    configure_hiring_agent_env()
    with _ha_lock:
        prev = os.getcwd()
        os.chdir(VENDOR_HIRING_AGENT)
        try:
            yield
        finally:
            os.chdir(prev)


def extract_resume_from_pdf(pdf_path: str) -> dict:
    """Full section-by-section PDF → JSONResume via hiring_agent PDFHandler.

    Raises HiringAgentError on any failure, including the extractor
    returning nothing.
    """
    try:
        with hiring_agent_context():
            from pdf import PDFHandler  # type: ignore

            handler = PDFHandler()
            text = handler.extract_text_from_pdf(str(Path(pdf_path).resolve()))
            if not text:
                raise HiringAgentError("PDF text extraction returned no text")
            resume = handler.extract_json_from_text(unbullet_job_headers(text))
            if resume is None:
                raise HiringAgentError("PDF extraction returned no data")
            return resume.model_dump()
    except HiringAgentError:
        raise
    except Exception as e:
        logger.warning("[hiring_agent] PDF extraction failed: %s", e)
        raise HiringAgentError(f"PDF extraction failed: {e}") from e


def unbullet_job_headers(markdown: str) -> str:
    """Turn job header lines rendered as bullets back into plain lines.

    Otherwise the work extractor treats the header as a bullet of the job
    above it, and that job absorbs the next job's bullets as well.
    """
    return _BULLETED_HEADER.sub(r"\1", markdown)


def clamp_evaluation(evaluation: dict, github_json: dict | None = None) -> dict:
    """Keep category scores within the rubric's limits."""
    projects = (github_json or {}).get("projects") or []
    self_only = bool(projects) and all(
        isinstance(p, dict) and p.get("project_type") == "self_project" for p in projects
    )
    for name, block in (evaluation.get("scores") or {}).items():
        if not isinstance(block, dict) or name not in CATEGORY_MAX:
            continue
        cap = CATEGORY_MAX[name]
        if name == "open_source" and self_only:
            cap = SELF_ONLY_OPEN_SOURCE_MAX
        block["max"] = CATEGORY_MAX[name]
        block["score"] = max(0.0, min(float(block.get("score") or 0), cap))
    return evaluation


def resume_to_text(resume: dict, github_json: dict | None = None) -> tuple[str, str]:
    """Convert JSONResume + optional GitHub data to plain text."""
    with hiring_agent_context():
        from models import JSONResume  # type: ignore
        from transform import convert_github_data_to_text, convert_json_resume_to_text  # type: ignore

        if resume.get("skills") is not None:
            resume = {**resume, "skills": normalize_skills(resume.get("skills"))}
        text = convert_json_resume_to_text(JSONResume(**resume))
        github_text = ""
        if github_json:
            github_text = convert_github_data_to_text(github_json)
        return text, github_text


def fetch_github_for_resume(resume: dict) -> dict:
    try:
        with hiring_agent_context():
            from github import fetch_and_display_github_info  # type: ignore

            for p in (resume.get("basics") or {}).get("profiles") or []:
                if (p.get("network") or "").lower() == "github" and p.get("url"):
                    return fetch_and_display_github_info(p["url"]) or {}
    except Exception as e:
        logger.info("[hiring_agent] GitHub enrichment skipped: %s", e)
    return {}


def evaluate_profile(resume: dict, github_json: dict | None = None) -> dict:
    """Run hiring_agent ResumeEvaluator on the stored profile.

    Raises HiringAgentError on any failure (unreachable LLM, bad/missing
    API key, malformed resume, etc.) instead of returning None.
    """
    try:
        resume_text, github_text = resume_to_text(resume, github_json)
        eval_input = resume_text
        if github_text:
            eval_input = f"{resume_text}\n\n=== GITHUB DATA ===\n{github_text}"

        with hiring_agent_context():
            from evaluator import ResumeEvaluator  # type: ignore

            cfg = get_merged_config()
            target_role = (cfg.get("hiring_agent") or {}).get("target_role") or (
                (resume.get("basics") or {}).get("label") or ""
            )
            # Deterministic sampling: the grade is compared across uploads, so
            # run-to-run variance would read as a real change in the CV.
            evaluator = ResumeEvaluator(
                model_name=cfg["quality_model"],
                model_params=EVALUATION_MODEL_PARAMS,
                target_role=target_role,
            )
            result = evaluator.evaluate_resume(eval_input)
            return clamp_evaluation(result.model_dump(), github_json)
    except Exception as e:
        logger.warning("[hiring_agent] Profile evaluation failed: %s", e)
        raise HiringAgentError(f"Profile evaluation failed: {e}") from e


def evaluation_total_score(evaluation: dict) -> int:
    scores = evaluation.get("scores") or {}
    total = sum((s.get("score") or 0) for s in scores.values())
    bonus = (evaluation.get("bonus_points") or {}).get("total") or 0
    deductions = (evaluation.get("deductions") or {}).get("total") or 0
    return int(max(-20, min(120, total + bonus - deductions)))


def format_evaluation_for_match(
    evaluation: dict | None,
    *,
    job_title: str = "",
    job_description: str = "",
    for_generation: bool = False,
    user_id: int | None = None,
    niche_terms: list[str] | None = None,
) -> str:
    if not evaluation:
        return ""
    lines = [
        f"Profile quality score: {evaluation_total_score(evaluation)}/100",
    ]
    scores = evaluation.get("scores") or {}
    for name, data in scores.items():
        if isinstance(data, dict):
            evidence = data.get("evidence", "")
            if for_generation and not posting_mentions_niche_domain(
                job_title, job_description, user_id=user_id, niche_terms=niche_terms
            ):
                if text_mentions_niche_domain(
                    evidence, user_id=user_id, niche_terms=niche_terms
                ):
                    evidence = soften_niche_phrasing(evidence)
            lines.append(
                f"- {name}: {data.get('score', 0)}/{data.get('max', 0)} — {evidence}"
            )
    strengths = evaluation.get("key_strengths") or []
    if strengths:
        if for_generation and (job_title or job_description):
            primary, background = partition_strengths(
                strengths,
                job_title=job_title,
                job_description=job_description,
                user_id=user_id,
                niche_terms=niche_terms,
            )
            if primary:
                lines.append("Key strengths for this job:")
                lines.extend(f"  • {s}" for s in primary[:5])
            if background:
                lines.append("Background strengths (do not lead with these):")
                lines.extend(f"  • {s}" for s in background[:3])
        elif not for_generation and (job_title or job_description):
            primary, background = partition_strengths(
                strengths,
                job_title=job_title,
                job_description=job_description,
                user_id=user_id,
                niche_terms=niche_terms,
            )
            if primary:
                lines.append("Key strengths for this job:")
                lines.extend(f"  • {s}" for s in primary[:5])
            if background:
                lines.append("Background strengths (lower relevance for this posting):")
                lines.extend(f"  • {s}" for s in background[:3])
        else:
            lines.append("Key strengths:")
            lines.extend(f"  • {s}" for s in strengths[:5])
    gaps = evaluation.get("areas_for_improvement") or []
    if gaps:
        lines.append("Areas for improvement (do not overstate):")
        lines.extend(f"  • {g}" for g in gaps[:5])
    return "\n".join(lines)
