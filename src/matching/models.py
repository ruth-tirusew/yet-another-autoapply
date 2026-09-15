"""Shared data models for retrieval-grounded matching.

Kept import-free of the rest of ``src`` so every other matching module (and
:mod:`src.ats`) can depend on it without cycles.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

RequirementKind = Literal["must", "nice"]
RequirementCategory = Literal["skill", "experience", "responsibility", "education", "other"]
Verdict = Literal["met", "partial", "missing"]

# Weight applied to a requirement when aggregating coverage.
KIND_WEIGHT: dict[str, float] = {"must": 1.0, "nice": 0.4}

# Credit awarded per verdict.
VERDICT_CREDIT: dict[str, float] = {"met": 1.0, "partial": 0.5, "missing": 0.0}


class Requirement(BaseModel):
    """One requirement extracted from a job posting."""

    id: str
    text: str
    kind: RequirementKind = "must"
    category: RequirementCategory = "skill"
    terms: list[str] = Field(default_factory=list)


class RequirementSet(BaseModel):
    """Everything stage 1 extracts from a posting, cached on the catalog row."""

    requirements: list[Requirement] = Field(default_factory=list)
    years_required: int | None = None
    seniority: str = ""
    source_hash: str = ""
    model: str = ""
    engine: Literal["llm", "heuristic"] = "llm"
    extracted_at: str = ""

    @property
    def musts(self) -> list[Requirement]:
        return [r for r in self.requirements if r.kind == "must"]


class ResumeChunk(BaseModel):
    """A retrievable slice of the candidate's resume."""

    id: str
    kind: Literal["summary", "work", "project", "skills", "education", "other"] = "other"
    ref: str = ""
    text: str = ""


class Evidence(BaseModel):
    """A resume chunk the judge cited for a requirement."""

    chunk_id: str = ""
    ref: str = ""
    quote: str = ""


class RequirementVerdict(BaseModel):
    """Stage 2 output for a single requirement."""

    id: str
    text: str
    kind: RequirementKind = "must"
    category: RequirementCategory = "skill"
    verdict: Verdict = "missing"
    confidence: float = 0.0
    evidence: list[Evidence] = Field(default_factory=list)
    note: str = ""

    @property
    def weight(self) -> float:
        return KIND_WEIGHT.get(self.kind, 1.0)

    @property
    def credit(self) -> float:
        return VERDICT_CREDIT.get(self.verdict, 0.0)
