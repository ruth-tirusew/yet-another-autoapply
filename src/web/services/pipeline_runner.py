"""Background pipeline execution with shared state."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from src.config import get_config
from src.cover_letter import CoverLetterSkipped, generate_cover_letter
from src.crawler.export import export_excel
from src.crawler.orchestrator import crawl_all
from src.catalog_sync import sync_user_jobs
from src.db import get_jobs_by_status, init_db, save_pipeline_run
from src.enrich import enrich_jobs
from src.matcher import match_all
from src.tailor import TailorSkipped, tailor_cv
from src.tenant import resolve_user_id, set_tenant_user_id

PLATFORM_STAGES = ["crawl", "enrich", "embed"]
USER_STAGES = ["sync", "prefilter", "match", "generate", "export"]
STAGES = PLATFORM_STAGES + USER_STAGES

STAGE_DESCRIPTIONS = {
    "crawl": "Crawl platform job catalog from enabled sources",
    "enrich": "Fetch full job descriptions (catalog)",
    "embed": "Compute embeddings for catalog jobs",
    "sync": "Sync new catalog listings to your queue",
    "prefilter": "Vector pre-filter before LLM matching",
    "match": "Score jobs against your profile (ATS)",
    "generate": "Tailor CVs and cover letters for queued jobs",
    "export": "Write jobs_results.xlsx",
}


@dataclass
class PipelineRun:
    id: str
    user_id: int
    stage: str = "pending"
    stages_requested: list[str] = field(default_factory=list)
    log: list[str] = field(default_factory=list)
    summary: dict[str, dict[str, Any]] = field(default_factory=dict)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None

    @property
    def running(self) -> bool:
        return self.started_at is not None and self.finished_at is None

    @property
    def done(self) -> bool:
        return self.finished_at is not None


_lock = threading.Lock()
_runs: dict[int, PipelineRun] = {}


def get_current_run(user_id: int | None = None) -> PipelineRun | None:
    uid = resolve_user_id(user_id)
    return _runs.get(uid)


def is_busy(user_id: int | None = None) -> bool:
    run = get_current_run(user_id)
    return run is not None and run.running


def _log(user_id: int, msg: str) -> None:
    run = _runs.get(user_id)
    if run:
        ts = datetime.utcnow().strftime("%H:%M:%S")
        run.log.append(f"[{ts}] {msg}")
        if len(run.log) > 500:
            run.log = run.log[-500:]


def _stage_summary(user_id: int, stage: str, **kwargs: Any) -> None:
    run = _runs.get(user_id)
    if run:
        run.summary[stage] = kwargs


def _run_stage(stage: str, user_id: int) -> None:
    set_tenant_user_id(user_id)
    cfg = get_config()
    init_db()
    if stage == "crawl":
        _log(user_id, "Starting platform crawl…")
        before = len(get_jobs_by_status("new", limit=10000, user_id=user_id))
        crawl_all(persist=True)
        _stage_summary(user_id, "crawl", message="Platform catalog updated")
        _log(user_id, "Crawl complete.")
    elif stage == "enrich":
        limit = cfg.get("pipeline", {}).get("enrich_limit", 200)
        _log(user_id, f"Fetching descriptions for up to {limit} catalog jobs…")
        enriched = enrich_jobs(limit=limit)
        _stage_summary(user_id, "enrich", processed=enriched, message=f"Enriched {enriched}")
        _log(user_id, "Fetch descriptions complete.")
    elif stage == "embed":
        from src.embeddings import embed_catalog_jobs

        limit = cfg.get("pipeline", {}).get("embed_limit", 200)
        _log(user_id, f"Embedding up to {limit} catalog jobs…")
        embedded = embed_catalog_jobs(limit=limit)
        _stage_summary(user_id, "embed", processed=embedded, message=f"Embedded {embedded}")
        _log(user_id, "Embed complete.")
    elif stage == "sync":
        limit = cfg.get("pipeline", {}).get("sync_limit", 500)
        _log(user_id, f"Syncing up to {limit} catalog jobs…")
        before = len(get_jobs_by_status("new", limit=10000, user_id=user_id))
        synced = sync_user_jobs(user_id=user_id, limit=limit)
        after = len(get_jobs_by_status("new", limit=10000, user_id=user_id))
        _stage_summary(
            user_id,
            "sync",
            processed=synced,
            message=f"Synced {synced} · {max(after - before, 0)} new in queue",
        )
        _log(user_id, "Sync complete.")
    elif stage == "prefilter":
        from src.prefilter import prefilter_all

        _log(user_id, "Running vector prefilter…")
        result = prefilter_all(user_id=user_id)
        _stage_summary(
            user_id,
            "prefilter",
            processed=result.get("scored", 0),
            skipped=result.get("skipped", 0),
            message=f"Scored {result.get('scored', 0)} · skipped {result.get('skipped', 0)}",
        )
        _log(user_id, "Prefilter complete.")
    elif stage == "match":
        limit = cfg.get("pipeline", {}).get("match_limit", 100)
        _log(user_id, f"Matching up to {limit} jobs…")
        result = match_all(limit=limit, user_id=user_id, log_fn=lambda msg: _log(user_id, msg))
        queued = len(get_jobs_by_status("queued", limit=10000, user_id=user_id))
        scored = len(get_jobs_by_status("scored", limit=10000, user_id=user_id))
        _stage_summary(
            user_id,
            "match",
            processed=result.get("processed", 0),
            scored=result.get("scored", 0),
            queued=queued,
            message=(
                f"Matched {result.get('processed', 0)} "
                f"({result.get('scored', 0)} scored, {result.get('queued', 0)} queued) "
                f"· {scored} scored total"
            ),
        )
        _log(user_id, "Match complete.")
    elif stage == "generate":
        from src.ats import parse_match_details, should_queue

        gen_limit = cfg.get("pipeline", {}).get("generate_limit", 50)
        pipeline = cfg.get("pipeline", {})
        threshold = cfg["match_threshold"]
        role_fit_min = int(pipeline.get("role_fit_min", 15))
        maybe_boost = int(pipeline.get("maybe_score_boost", 10))
        generated = 0
        skipped = 0
        errors = 0
        _log(user_id, f"Generating materials for up to {gen_limit} queued jobs…")
        for job in get_jobs_by_status("queued", limit=gen_limit, user_id=user_id):
            m = parse_match_details(job)
            if m:
                ok, reason = should_queue(
                    m, threshold, role_fit_min=role_fit_min, maybe_score_boost=maybe_boost
                )
                if not ok:
                    _log(user_id, f"  [generate] skip job {job['id']}: {reason}")
                    skipped += 1
                    continue
            try:
                tailor_cv(job["id"], force=True, user_id=user_id)
                generate_cover_letter(job["id"], force=True, user_id=user_id)
                generated += 1
                _log(user_id, f"  Generated for job {job['id']}: {(job.get('title') or '')[:50]}")
            except (TailorSkipped, CoverLetterSkipped) as e:
                _log(user_id, f"  [generate] skip job {job['id']}: {e}")
                skipped += 1
            except Exception as e:
                _log(user_id, f"  [generate] job {job['id']}: {e}")
                errors += 1
        _stage_summary(
            user_id,
            "generate",
            processed=generated,
            skipped=skipped,
            errors=errors,
            message=f"Generated {generated} · skipped {skipped}",
        )
        _log(user_id, "Generate complete.")
    elif stage == "export":
        _log(user_id, "Exporting Excel…")
        export_excel(user_id=user_id)
        _stage_summary(user_id, "export", message="Excel exported")
        _log(user_id, "Export complete.")
    else:
        raise ValueError(f"Unknown stage: {stage}")


def _worker(stages: list[str], user_id: int) -> None:
    set_tenant_user_id(user_id)
    run = _runs.get(user_id)
    try:
        for stage in stages:
            if run:
                run.stage = stage
            _run_stage(stage, user_id)
        if run:
            run.stage = "done"
            _log(user_id, "Pipeline finished.")
    except Exception as e:
        if run:
            run.stage = "error"
            run.error = str(e)
        _log(user_id, f"ERROR: {e}")
    finally:
        if run:
            run.finished_at = datetime.utcnow()
            save_pipeline_run(
                run.id,
                run.stages_requested,
                run.summary,
                run.log,
                run.error,
                run.started_at.isoformat() if run.started_at else "",
                run.finished_at.isoformat(),
                user_id=user_id,
            )


def start_run(stages: list[str] | None = None, user_id: int | None = None) -> PipelineRun:
    uid = resolve_user_id(user_id)
    with _lock:
        existing = _runs.get(uid)
        if existing and existing.running:
            raise RuntimeError("Pipeline already running")
        requested = stages or STAGES
        valid = [s for s in requested if s in STAGES]
        if not valid:
            valid = STAGES
        run = PipelineRun(
            id=uuid.uuid4().hex[:8],
            user_id=uid,
            stages_requested=valid,
            started_at=datetime.utcnow(),
        )
        _runs[uid] = run
        thread = threading.Thread(target=_worker, args=(valid, uid), daemon=True)
        thread.start()
        return run


def run_state_dict(user_id: int | None = None) -> dict[str, Any]:
    uid = resolve_user_id(user_id)
    run = get_current_run(uid)
    if not run:
        from src.db import get_last_pipeline_run

        last = get_last_pipeline_run(uid)
        return {
            "idle": True,
            "stage": "idle",
            "log": [],
            "running": False,
            "last_run": last,
        }
    return {
        "idle": False,
        "id": run.id,
        "stage": run.stage,
        "stages_requested": run.stages_requested,
        "log": run.log[-50:],
        "summary": run.summary,
        "running": run.running,
        "done": run.done,
        "error": run.error,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }
