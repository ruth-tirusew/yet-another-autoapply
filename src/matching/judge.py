"""Stage 2 — judge each requirement against the evidence retrieval surfaced.

One call per job covers every requirement, and the model is asked for a
verdict plus the chunk ids it relied on — not for a score. Citations are
validated against the chunks that were actually shown, so a "met" verdict
with no real evidence is downgraded rather than trusted.
"""

from __future__ import annotations

import logging
import re
from typing import Sequence

from pydantic import BaseModel, Field

from src.config_store import get_merged_config
from src.llm import chat_json, render_template
from src.matching.models import Evidence, Requirement, RequirementVerdict, ResumeChunk

logger = logging.getLogger(__name__)

PROMPT_VERSION = "judge-v1"
EVIDENCE_CHARS = 700


class _JudgedEvidence(BaseModel):
    chunk_id: str = ""
    quote: str = ""


class _Judgement(BaseModel):
    id: str = ""
    verdict: str = "missing"
    confidence: float = 0.0
    evidence: list[_JudgedEvidence] = Field(default_factory=list)
    note: str = ""


class _JudgeResponse(BaseModel):
    verdicts: list[_Judgement] = Field(default_factory=list)


def _normalize_id(raw: str) -> str:
    """Strip stray punctuation models sometimes wrap ids in (e.g. ':r12').

    Requirement ids are always plain alphanumeric (``r1``, ``pc3``), so
    dropping every non-alphanumeric character is safe and makes the
    verdict-to-requirement join robust to model formatting quirks instead of
    silently failing an exact-string match.
    """
    return re.sub(r"[^a-zA-Z0-9]", "", raw or "")


def _clip(text: str, limit: int = EVIDENCE_CHARS) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def build_items(
    requirements: Sequence[Requirement],
    retrieved: dict[str, list[tuple[ResumeChunk, float]]],
) -> list[dict]:
    items: list[dict] = []
    for requirement in requirements:
        chunks = retrieved.get(requirement.id, [])
        items.append(
            {
                "id": requirement.id,
                "text": requirement.text,
                "kind": requirement.kind,
                "chunks": [
                    {"id": chunk.id, "ref": chunk.ref, "text": _clip(chunk.text)}
                    for chunk, _ in chunks
                ],
            }
        )
    return items


def lexical_verdicts(
    requirements: Sequence[Requirement],
    retrieved: dict[str, list[tuple[ResumeChunk, float]]],
    *,
    met_at: float = 0.55,
    partial_at: float = 0.25,
) -> list[RequirementVerdict]:
    """Model-free verdicts from retrieval scores alone.

    Used when no model is reachable so the pipeline degrades to a weaker but
    still grounded result instead of failing outright.
    """
    out: list[RequirementVerdict] = []
    for requirement in requirements:
        scored = retrieved.get(requirement.id, [])
        top_score = scored[0][1] if scored else 0.0
        if top_score >= met_at:
            verdict = "met"
        elif top_score >= partial_at:
            verdict = "partial"
        else:
            verdict = "missing"
        evidence = (
            [Evidence(chunk_id=scored[0][0].id, ref=scored[0][0].ref, quote=_clip(scored[0][0].text, 160))]
            if scored and verdict != "missing"
            else []
        )
        out.append(
            RequirementVerdict(
                id=requirement.id,
                text=requirement.text,
                kind=requirement.kind,
                category=requirement.category,
                verdict=verdict,  # type: ignore[arg-type]
                confidence=round(min(1.0, top_score), 3),
                evidence=evidence,
                note="retrieval-only verdict (no model available)",
            )
        )
    return out


JUDGE_BATCH_SIZE = 6


def judge_requirements(
    requirements: Sequence[Requirement],
    retrieved: dict[str, list[tuple[ResumeChunk, float]]],
    *,
    candidate_summary: str = "",
    model: str | None = None,
) -> list[RequirementVerdict]:
    """Ask the model for one verdict per requirement, then validate the citations.

    Requirements are judged in small batches rather than one call for the
    whole posting. A single call covering everything (up to
    :data:`~src.matching.requirements.MAX_REQUIREMENTS`, 18) is fine for a
    strong hosted model, but a small local model routinely drops most of a
    long list — returning verdicts for only the last few ids and defaulting
    the rest to "missing" with no judgement at all. Batching keeps each call
    within what a small model can actually attend to, at the cost of more
    calls per job.
    """
    if not requirements:
        return []

    cfg = get_merged_config()
    model = model or cfg["quality_model"]
    out: list[RequirementVerdict] = []
    for start in range(0, len(requirements), JUDGE_BATCH_SIZE):
        batch = list(requirements[start : start + JUDGE_BATCH_SIZE])
        out.extend(
            _judge_batch(batch, retrieved, candidate_summary=candidate_summary, model=model)
        )
    return out


def _judge_batch(
    requirements: Sequence[Requirement],
    retrieved: dict[str, list[tuple[ResumeChunk, float]]],
    *,
    candidate_summary: str,
    model: str,
) -> list[RequirementVerdict]:
    items = build_items(requirements, retrieved)
    prompt = render_template(
        "requirement_judge.jinja",
        items=items,
        candidate_summary=_clip(candidate_summary, 600),
    )
    data = chat_json(
        prompt,
        system=render_template("requirement_judge_system.jinja"),
        model=model,
        schema=_JudgeResponse,
        temperature=0.0,
    )
    parsed = _JudgeResponse(**data)
    by_id = {_normalize_id(j.id): j for j in parsed.verdicts if _normalize_id(j.id)}

    out: list[RequirementVerdict] = []
    for requirement in requirements:
        shown = {chunk.id: chunk for chunk, _ in retrieved.get(requirement.id, [])}
        judgement = by_id.get(_normalize_id(requirement.id))
        if judgement is None:
            out.append(
                RequirementVerdict(
                    id=requirement.id,
                    text=requirement.text,
                    kind=requirement.kind,
                    category=requirement.category,
                    verdict="missing",
                    note="no verdict returned for this requirement",
                )
            )
            continue

        verdict = str(judgement.verdict or "missing").strip().lower()
        if verdict not in ("met", "partial", "missing"):
            verdict = "missing"

        evidence = [
            Evidence(
                chunk_id=cited.chunk_id,
                ref=shown[cited.chunk_id].ref,
                quote=_clip(cited.quote or shown[cited.chunk_id].text, 200),
            )
            for cited in judgement.evidence
            if cited.chunk_id in shown
        ]
        note = judgement.note or ""
        if verdict == "met" and not evidence:
            # A "met" the model could not tie to any chunk it was shown is not
            # evidence — downgrade rather than trust it.
            verdict = "partial" if shown else "missing"
            note = (note + " (downgraded: no valid citation)").strip()

        out.append(
            RequirementVerdict(
                id=requirement.id,
                text=requirement.text,
                kind=requirement.kind,
                category=requirement.category,
                verdict=verdict,  # type: ignore[arg-type]
                confidence=round(max(0.0, min(1.0, float(judgement.confidence or 0.0))), 3),
                evidence=evidence,
                note=note[:240],
            )
        )
    return out
