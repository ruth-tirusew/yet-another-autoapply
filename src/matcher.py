"""Job-resume fit scoring."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from src.ats import CategoryScore, JobMatchResult, LegacyMatchResponse, should_queue
from src.config import get_config
from src.crawler.filters import candidate_country_code, is_job_eligible, parse_job_location
from src.crawler.location import candidate_matches_location
from src.db import connect, get_job, get_jobs_by_status, get_profile, log_application_event, update_job
from src.hiring_agent_bridge import format_evaluation_for_match, resume_to_text
from src.llm import chat_json, render_template
from src.matching.engine import MatchContext, grounded_match
from src.profile import resolve_resume_variant_for_job
from src.rule_engine import UNCLASSIFIED_MARKER, score_job_rules
from src.tenant import resolve_user_id


# Re-export for callers that import from matcher
__all__ = ["CategoryScore", "JobMatchResult", "match_job", "match_all", "match_new_jobs", "count_matchable_new_jobs"]

_MATCH_RESUME_CHARS = 6000
_MATCH_GITHUB_CHARS = 2000
_MATCH_JOB_DESC_CHARS = 5000


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 20].rstrip() + "\n… [truncated]"


def _record_match_failure(job_id: int, uid: int, error: str, cfg: dict[str, Any]) -> int:
    """Count a failed scoring attempt and retire the job once it keeps failing.

    Without this a job whose posting breaks extraction or JSON parsing stays
    ``new`` and is retried on every run forever, burning a model call each
    time.
    """
    job = get_job(job_id, user_id=uid) or {}
    attempts = int(job.get("match_attempts") or 0) + 1
    max_attempts = int((cfg.get("matching", {}) or {}).get("max_match_attempts", 3))
    fields: dict[str, Any] = {
        "match_attempts": attempts,
        "match_summary": f"MATCH ERROR ({attempts}/{max_attempts}) — {error}"[:500],
    }
    if attempts >= max_attempts:
        fields["status"] = "skipped"
        fields["match_summary"] = f"SKIP — scoring failed {attempts} times: {error}"[:500]
    else:
        fields["status"] = "new"
    update_job(job_id, user_id=uid, **fields)
    log_application_event(
        job_id,
        "match_failed",
        {"attempts": attempts, "max_attempts": max_attempts, "error": error[:300]},
        user_id=uid,
    )
    return attempts


def match_job(
    job_id: int,
    user_id: int | None = None,
    *,
    context_cache: dict[str, MatchContext] | None = None,
) -> JobMatchResult | None:
    """Score one job.

    ``context_cache`` lets a batch reuse the per-resume-variant match
    context (chunks, vectors, derived years/seniority) across jobs.
    """
    uid = resolve_user_id(user_id)
    job = get_job(job_id, user_id=uid)
    if not job:
        return None
    resume, resume_variant = resolve_resume_variant_for_job(job, uid)
    if not resume:
        raise RuntimeError("No profile loaded. Run: python cli.py upload-cv <pdf>")

    profile = get_profile(uid)
    job_title = job.get("title", "") or ""
    job_description = job.get("description_full") or job.get("description_short") or ""

    eligible, reason = is_job_eligible(job, resume)
    if not eligible:
        update_job(
            job_id,
            user_id=uid,
            match_score=0,
            match_summary=f"SKIP — {reason}",
            match_details=json.dumps({"recommendation": "skip", "reason": reason}),
            status="skipped",
        )
        return None

    cc = candidate_country_code(resume)
    candidate_location = ""
    if resume.get("basics", {}).get("location"):
        loc = resume["basics"]["location"]
        candidate_location = ", ".join(
            p for p in [loc.get("city"), loc.get("region"), loc.get("countryCode")] if p
        )

    cfg = get_config()
    pipeline = cfg.get("pipeline", {})
    resume_limit = int(pipeline.get("match_resume_chars", _MATCH_RESUME_CHARS))
    github_limit = int(pipeline.get("match_github_chars", _MATCH_GITHUB_CHARS))
    desc_limit = int(pipeline.get("match_job_desc_chars", _MATCH_JOB_DESC_CHARS))

    threshold = cfg["match_threshold"]
    role_fit_min = int(pipeline.get("role_fit_min", 15))
    matching_cfg = cfg.get("matching", {})
    scoring_mode = matching_cfg.get("scoring_mode", "llm")
    rules_cfg = matching_cfg.get("rules", {})

    result: JobMatchResult | None = None

    if scoring_mode == "grounded":
        if context_cache is None:
            context = MatchContext.build(resume, user_id=uid, cfg=cfg)
        else:
            context = context_cache.get(resume_variant)
            if context is None:
                context = MatchContext.build(resume, user_id=uid, cfg=cfg)
                context_cache[resume_variant] = context
        result = grounded_match(job, resume, user_id=uid, cfg=cfg, context=context)

    if result is None and scoring_mode in ("rules", "hybrid"):
        rules_result = score_job_rules(
            resume, job, rules_cfg, threshold=threshold, role_fit_min=role_fit_min
        )
        if scoring_mode == "rules":
            result = rules_result
        else:
            hybrid_band = int(rules_cfg.get("hybrid_band", 10))
            unclassified = UNCLASSIFIED_MARKER in rules_result.role_fit.evidence
            near_threshold = abs(rules_result.overall_score - threshold) <= hybrid_band
            if not (unclassified or near_threshold):
                result = rules_result

    if result is None:
        resume_text, github_text = resume_to_text(
            resume, profile.get("github_json") if profile else None
        )
        profile_evaluation = format_evaluation_for_match(
            profile.get("evaluation_json") if profile else None,
            job_title=job_title,
            job_description=job_description,
            user_id=uid,
        )
        system = render_template("job_match_system.jinja")
        user = render_template(
            "job_match_criteria.jinja",
            resume_text=_clip(resume_text, resume_limit),
            github_text=_clip(github_text, github_limit),
            profile_evaluation=profile_evaluation,
            job_title=job_title,
            job_company=job.get("company", ""),
            job_location=job.get("location", ""),
            job_description=_clip(job_description, desc_limit),
            candidate_location=candidate_location,
            candidate_country=cc,
        )

        model = cfg["quality_model"]
        try:
            data = chat_json(
                user,
                system=system,
                model=model,
                schema=LegacyMatchResponse,
                temperature=0.0,
            )
            result = JobMatchResult(**LegacyMatchResponse(**data).model_dump(), engine="llm")
        except (ValueError, json.JSONDecodeError) as e:
            _record_match_failure(job_id, uid, str(e), cfg)
            print(f"  [match] job {job_id}: {e}")
            return None

    parsed = parse_job_location(
        job.get("location", ""),
        job.get("description_full") or job.get("description_short") or "",
    )
    if parsed.allowed_country_codes:
        loc_ok, loc_reason = candidate_matches_location(parsed, cc)
        if not loc_ok:
            capped = min(result.overall_score, 30)
            result = result.model_copy(
                update={
                    "overall_score": capped,
                    "recommendation": "skip",
                    "gaps": result.gaps + [loc_reason],
                }
            )

    maybe_boost = int(pipeline.get("maybe_score_boost", 10))
    queue_ok, queue_reason = should_queue(
        result,
        threshold,
        role_fit_min=role_fit_min,
        maybe_score_boost=maybe_boost,
    )

    summary = (
        f"{result.recommendation.upper()} ({result.overall_score}/100). "
        + "; ".join(result.strengths[:3])
    )
    if not queue_ok:
        summary += f" — not queued: {queue_reason}"
    status = "queued" if queue_ok else "scored"

    update_job(
        job_id,
        user_id=uid,
        match_score=result.overall_score,
        match_summary=summary,
        match_details=json.dumps(result.model_dump()),
        status=status,
    )
    log_application_event(
        job_id,
        "matched",
        {
            "score": result.overall_score,
            "recommendation": result.recommendation,
            "queued": queue_ok,
            "reason": queue_reason if not queue_ok else "",
        },
        user_id=uid,
    )
    return result


def match_all(
    limit: int | None = None,
    user_id: int | None = None,
    *,
    log_fn: Callable[[str], None] | None = None,
) -> dict[str, int]:
    cfg = get_config()
    if limit is None:
        limit = int(cfg.get("pipeline", {}).get("match_limit", 50))
    matching = cfg.get("matching", {})
    prefilter_on = matching.get("vector_prefilter_enabled", True)
    vector_llm_min = float(matching.get("vector_llm_min_score", 0.50))

    jobs = get_jobs_by_status(
        "new",
        limit=limit,
        user_id=user_id,
        vector_llm_min=vector_llm_min if prefilter_on else None,
        prefilter_enabled=prefilter_on,
        require_description=True,
    )
    context_cache: dict[str, MatchContext] = {}
    stats = {
        "processed": 0,
        "scored": 0,
        "queued": 0,
        "skipped_no_desc": 0,
        "skipped_vector": 0,
        "errors": 0,
    }
    batch_size = len(jobs)

    def _emit(msg: str) -> None:
        print(msg)
        if log_fn:
            log_fn(msg)

    for index, job in enumerate(jobs, start=1):
        if prefilter_on:
            vs = job.get("vector_score")
            if vs is not None and vs < vector_llm_min:
                stats["skipped_vector"] += 1
                continue
        if not (job.get("description_full") or job.get("description_short")):
            stats["skipped_no_desc"] += 1
            continue
        title = (job.get("title") or "Untitled")[:50]
        _emit(f"  [match] {index}/{batch_size} job {job['id']}: {title}")
        try:
            match_job(job["id"], user_id=user_id, context_cache=context_cache)
            updated = get_job(job["id"], user_id=user_id)
            if not updated:
                continue
            stats["processed"] += 1
            if updated.get("status") == "queued":
                stats["queued"] += 1
            elif updated.get("status") == "scored":
                stats["scored"] += 1
        except Exception as e:
            stats["errors"] += 1
            try:
                _record_match_failure(job["id"], resolve_user_id(user_id), str(e), cfg)
            except Exception:  # never let bookkeeping mask the original error
                pass
            _emit(f"  [match] job {job['id']}: {e}")
    _emit(
        f"  Matched {stats['processed']} jobs "
        f"({stats['scored']} scored, {stats['queued']} queued)"
    )
    if stats["skipped_no_desc"]:
        _emit(f"  Skipped {stats['skipped_no_desc']} without descriptions")
    if stats["skipped_vector"]:
        _emit(f"  Skipped {stats['skipped_vector']} below vector threshold")
    return stats


def count_matchable_new_jobs(user_id: int | None = None) -> int:
    from src.tenant import resolve_user_id

    cfg = get_config()
    matching = cfg.get("matching", {})
    prefilter_on = matching.get("vector_prefilter_enabled", True)
    vector_llm_min = float(matching.get("vector_llm_min_score", 0.50))
    uid = resolve_user_id(user_id)
    extra = ""
    params: list[Any] = [uid]
    if prefilter_on:
        extra = " AND (uj.vector_score IS NULL OR uj.vector_score >= ?)"
        params.append(vector_llm_min)
    with connect() as conn:
        row = conn.execute(
            f"""
            SELECT COUNT(*) FROM user_jobs uj
            JOIN catalog_jobs cj ON cj.id = uj.catalog_job_id
            WHERE uj.user_id = ? AND uj.status = 'new'
              AND (
                (cj.description_full IS NOT NULL AND cj.description_full != '')
                OR (cj.description_short IS NOT NULL AND cj.description_short != '')
              ){extra}
            """,
            params,
        ).fetchone()
    return int(row[0]) if row else 0


def match_new_jobs(
    limit: int | None = None,
    user_id: int | None = None,
    *,
    all_remaining: bool = False,
) -> dict[str, int]:
    """Score new jobs via LLM, moving them to scored or queued."""
    totals = {
        "processed": 0,
        "scored": 0,
        "queued": 0,
        "skipped_no_desc": 0,
        "skipped_vector": 0,
        "errors": 0,
        "batches": 0,
    }
    while True:
        batch = match_all(limit=limit, user_id=user_id)
        totals["batches"] += 1
        for key in ("processed", "scored", "queued", "skipped_no_desc", "skipped_vector", "errors"):
            totals[key] += batch.get(key, 0)
        if not all_remaining or batch.get("processed", 0) == 0:
            break
    return totals
