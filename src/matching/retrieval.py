"""Retrieve the resume chunks that bear on a given requirement.

Vector retrieval is used when an embedding backend is reachable, and a
lexical (BM25-style) retriever otherwise. The fallback is explicit and
reported on the result — the previous design degraded silently, so a user on
a provider without embeddings had no prefilter and no way to know.
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from typing import Sequence

from src.matching.chunking import text_hash
from src.matching.models import Requirement, ResumeChunk
from src.matching.vectors import (
    OWNER_REQUIREMENT,
    OWNER_RESUME_CHUNK,
    EmbeddingUnavailable,
    embed_with_cache,
    similarity_matrix,
)

logger = logging.getLogger(__name__)

TOP_K = 4
_TOKEN_RE = re.compile(r"[a-z0-9+#.]+")
_STOP = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "have",
    "in", "is", "it", "of", "on", "or", "our", "the", "to", "with", "you", "your",
    "we", "will", "that", "this", "experience", "strong", "good", "excellent",
    "ability", "years", "year", "work", "working", "using", "knowledge",
}


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall((text or "").lower()) if t not in _STOP and len(t) > 1]


class LexicalIndex:
    """Small BM25 index over resume chunks — deterministic, no model needed."""

    K1 = 1.4
    B = 0.75

    def __init__(self, chunks: Sequence[ResumeChunk]):
        self.chunks = list(chunks)
        self.docs = [Counter(tokenize(c.text + " " + c.ref)) for c in self.chunks]
        self.lengths = [sum(d.values()) or 1 for d in self.docs]
        self.avg_len = (sum(self.lengths) / len(self.lengths)) if self.lengths else 1.0
        self.df: Counter[str] = Counter()
        for doc in self.docs:
            self.df.update(doc.keys())
        self.n = len(self.docs) or 1

    def scores(self, query: str) -> list[float]:
        terms = tokenize(query)
        if not terms:
            return [0.0] * len(self.docs)
        out: list[float] = []
        for doc, length in zip(self.docs, self.lengths):
            score = 0.0
            for term in terms:
                freq = doc.get(term, 0)
                if not freq:
                    continue
                idf = math.log(1 + (self.n - self.df[term] + 0.5) / (self.df[term] + 0.5))
                denom = freq + self.K1 * (1 - self.B + self.B * length / self.avg_len)
                score += idf * (freq * (self.K1 + 1)) / denom
            out.append(score)
        peak = max(out) if out else 0.0
        return [s / peak for s in out] if peak > 0 else out


class Retriever:
    """Rank resume chunks against requirements, vector-first with lexical fallback."""

    def __init__(
        self,
        chunks: Sequence[ResumeChunk],
        *,
        user_id: int | None = None,
        owner_prefix: str = "",
        allow_vectors: bool = True,
    ):
        self.chunks = list(chunks)
        self.user_id = user_id
        self.owner_prefix = owner_prefix
        self.backend = "none"
        self.warning = ""
        self._lexical = LexicalIndex(self.chunks)
        self._chunk_vectors: list[list[float]] | None = None
        if allow_vectors and self.chunks:
            self._chunk_vectors = self._load_chunk_vectors()
            self.backend = "vector" if self._chunk_vectors else "lexical"
        elif self.chunks:
            self.backend = "lexical"

    def _load_chunk_vectors(self) -> list[list[float]] | None:
        items = [
            (f"{self.owner_prefix}{c.id}", text_hash(c.text), c.text)
            for c in self.chunks
        ]
        try:
            by_id = embed_with_cache(OWNER_RESUME_CHUNK, items, user_id=self.user_id)
        except Exception as e:
            # Retrieval quality is an optimization; never let it fail a match.
            # Anything the embedding path raises degrades to lexical ranking.
            self.warning = (
                str(e) if isinstance(e, EmbeddingUnavailable)
                else f"Embedding backend error, using lexical retrieval: {e}"
            )
            logger.warning("[retrieval] %s", self.warning)
            return None
        vectors = [by_id.get(f"{self.owner_prefix}{c.id}") for c in self.chunks]
        if any(v is None for v in vectors):
            self.warning = "Some resume chunks have no vector; using lexical retrieval."
            return None
        return vectors  # type: ignore[return-value]

    def _requirement_vectors(self, requirements: Sequence[Requirement], job_key: str):
        items = [
            (f"{job_key}:{r.id}", text_hash(r.text), r.text)
            for r in requirements
        ]
        try:
            by_id = embed_with_cache(OWNER_REQUIREMENT, items, user_id=self.user_id)
        except Exception as e:
            self.warning = (
                str(e) if isinstance(e, EmbeddingUnavailable)
                else f"Embedding backend error, using lexical retrieval: {e}"
            )
            logger.warning("[retrieval] %s", self.warning)
            return None
        vectors = [by_id.get(f"{job_key}:{r.id}") for r in requirements]
        return None if any(v is None for v in vectors) else vectors

    def retrieve(
        self,
        requirements: Sequence[Requirement],
        *,
        job_key: str = "",
        top_k: int = TOP_K,
    ) -> dict[str, list[tuple[ResumeChunk, float]]]:
        """Map requirement id → its best-matching resume chunks with scores."""
        if not self.chunks or not requirements:
            return {r.id: [] for r in requirements}

        matrix: list[list[float]] | None = None
        if self._chunk_vectors:
            req_vectors = self._requirement_vectors(requirements, job_key)
            if req_vectors:
                matrix = similarity_matrix(req_vectors, self._chunk_vectors)
                self.backend = "vector"
            else:
                self.backend = "lexical"
        if matrix is None:
            matrix = [self._lexical.scores(r.text) for r in requirements]
            self.backend = "lexical"

        out: dict[str, list[tuple[ResumeChunk, float]]] = {}
        for requirement, row in zip(requirements, matrix):
            ranked = sorted(zip(self.chunks, row), key=lambda p: p[1], reverse=True)
            out[requirement.id] = [(c, float(s)) for c, s in ranked[:top_k] if s > 0]
        return out

    def coverage_hint(
        self,
        retrieved: dict[str, list[tuple[ResumeChunk, float]]],
    ) -> float:
        """Mean best-chunk similarity across requirements — a cheap pre-LLM signal."""
        if not retrieved:
            return 0.0
        tops = [scored[0][1] if scored else 0.0 for scored in retrieved.values()]
        return sum(tops) / len(tops) if tops else 0.0
