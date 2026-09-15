"""Aggregate skill gaps from match results and generate coaching report."""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from pydantic import BaseModel, Field

from src.config import get_config
from src.db import connect
from src.llm import chat_json, render_template
from src.tenant import resolve_user_id


class SkillsGapReport(BaseModel):
    top_gaps: list[str] = Field(default_factory=list)
    gap_frequency: dict[str, int] = Field(default_factory=dict)
    learning_paths: list[str] = Field(default_factory=list)
    roles_unlocked: list[str] = Field(default_factory=list)
    summary: str = ""


def aggregate_gaps(user_id: int | None = None, limit: int = 50) -> Counter[str]:
    uid = resolve_user_id(user_id)
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT uj.match_details, uj.match_score
            FROM user_jobs uj
            WHERE uj.user_id = ?
              AND uj.status IN ('queued', 'scored', 'approved')
              AND uj.match_details IS NOT NULL
            ORDER BY COALESCE(uj.match_score, 0) DESC
            LIMIT ?
            """,
            (uid, limit),
        ).fetchall()

    counter: Counter[str] = Counter()
    for row in rows:
        try:
            data = json.loads(row["match_details"] or "{}")
        except json.JSONDecodeError:
            continue
        for gap in data.get("gaps") or []:
            g = str(gap).strip()
            if g:
                counter[g] += 1
    return counter


def build_gap_report(user_id: int | None = None, *, use_llm: bool = True) -> SkillsGapReport:
    uid = resolve_user_id(user_id)
    freq = aggregate_gaps(uid)
    if not freq:
        return SkillsGapReport(summary="No match data yet. Run the pipeline to score jobs first.")

    top_gaps = [g for g, _ in freq.most_common(15)]
    base = SkillsGapReport(
        top_gaps=top_gaps[:8],
        gap_frequency=dict(freq.most_common(15)),
        summary="Aggregated from your highest-scoring job matches.",
    )

    if not use_llm:
        return base

    cfg = get_config()
    model = cfg["quality_model"]
    try:
        system = render_template("skills_gap_system.jinja")
        user = render_template(
            "skills_gap_criteria.jinja",
            gap_frequency=base.gap_frequency,
            top_gaps=base.top_gaps,
        )
        data = chat_json(user, system=system, model=model, schema=SkillsGapReport)
        return SkillsGapReport(**data)
    except Exception as e:
        base.summary = f"{base.summary} (LLM coaching unavailable: {e})"
        return base
