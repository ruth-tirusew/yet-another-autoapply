"""Embedding backend and a model-tagged vector cache.

Two problems with the previous embedding layer are fixed here:

* **Untagged vectors.** Stored blobs recorded neither the model that produced
  them nor the text they were built from, so switching ``embedding_model``
  silently compared vectors from different spaces until someone remembered to
  run ``embeddings reset`` by hand. Every row here carries ``(model, dim,
  text_hash)`` and a lookup that disagrees on any of them is a cache miss.
* **Silent disablement.** Embeddings were hard-wired to Ollama, so a Groq or
  Gemini user got no vectors, no prefilter and no warning. The backend now
  follows the configured provider and reports when it is unavailable instead
  of failing open.
"""

from __future__ import annotations

import logging
import struct
from datetime import datetime, timezone
from typing import Iterable, Sequence

from src.config_store import get_merged_config
from src.db import connect
from src.llm.factory import get_provider
from src.tenant import resolve_user_id

logger = logging.getLogger(__name__)

OWNER_RESUME_CHUNK = "resume_chunk"
OWNER_REQUIREMENT = "requirement"

# Providers that expose an embeddings endpoint, and their default model.
DEFAULT_EMBED_MODELS = {
    "ollama": "mxbai-embed-large",
    "gemini": "text-embedding-004",
}


class EmbeddingUnavailable(RuntimeError):
    """No embedding backend is reachable for the current provider."""


def pack(vec: Sequence[float] | None) -> bytes | None:
    if not vec:
        return None
    return struct.pack(f"{len(vec)}f", *vec)


def unpack(blob: bytes | None) -> list[float] | None:
    if not blob:
        return None
    return list(struct.unpack(f"{len(blob) // 4}f", blob))


def embedding_settings(user_id: int | None = None) -> tuple[str, str]:
    """Return ``(provider, model)`` for embeddings, following the LLM provider."""
    cfg = get_merged_config(resolve_user_id(user_id))
    matching = cfg.get("matching", {}) or {}
    provider = str(matching.get("embedding_provider") or "auto").strip().lower()
    if provider in ("", "auto"):
        provider = str(cfg.get("llm_provider") or "ollama").strip().lower()
    model = str(matching.get("embedding_model") or "").strip()
    if not model or provider != "ollama":
        model = DEFAULT_EMBED_MODELS.get(provider, model)
    return provider, model or ""


def embed_texts(
    texts: Sequence[str],
    *,
    user_id: int | None = None,
    model: str | None = None,
) -> list[list[float]]:
    """Embed a batch of texts with the tenant's provider.

    Raises :class:`EmbeddingUnavailable` when the provider has no embeddings
    endpoint or the call fails, so callers can fall back deliberately rather
    than silently degrading.
    """
    if not texts:
        return []
    provider_name, resolved = embedding_settings(user_id)
    model = model or resolved
    if not model:
        raise EmbeddingUnavailable(f"No embedding model configured for provider {provider_name!r}")

    provider = get_provider(user_id)
    embed = getattr(provider, "embed", None)
    if embed is None:
        raise EmbeddingUnavailable(
            f"Provider {provider_name!r} does not support embeddings — "
            "vector retrieval is disabled; matching falls back to lexical retrieval."
        )
    try:
        vectors = embed(model, list(texts))
    except EmbeddingUnavailable:
        raise
    except Exception as e:  # network, auth, missing model
        raise EmbeddingUnavailable(f"Embedding call failed ({provider_name}/{model}): {e}") from e
    if len(vectors) != len(texts):
        raise EmbeddingUnavailable(
            f"Embedding backend returned {len(vectors)} vectors for {len(texts)} inputs"
        )
    return [[float(x) for x in v] for v in vectors]


# --- model-tagged cache -------------------------------------------------


def get_cached(
    owner_kind: str,
    keys: Iterable[tuple[str, str]],
    model: str,
) -> dict[str, list[float]]:
    """Return cached vectors for ``(owner_id, text_hash)`` pairs under ``model``."""
    keys = list(keys)
    if not keys:
        return {}
    by_id = dict(keys)
    placeholders = ",".join("?" * len(by_id))
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT owner_id, text_hash, vec FROM vector_store
            WHERE owner_kind = ? AND model = ? AND owner_id IN ({placeholders})
            """,
            (owner_kind, model, *by_id.keys()),
        ).fetchall()
    out: dict[str, list[float]] = {}
    for row in rows:
        if row["text_hash"] != by_id.get(row["owner_id"]):
            continue  # text changed (e.g. description enriched) — recompute
        vec = unpack(row["vec"])
        if vec:
            out[row["owner_id"]] = vec
    return out


def put_many(
    owner_kind: str,
    items: Iterable[tuple[str, str, Sequence[float]]],
    model: str,
) -> int:
    """Store ``(owner_id, text_hash, vector)`` rows, replacing any prior vector."""
    rows = [
        (owner_kind, owner_id, model, len(vec), text_hash, pack(vec), datetime.now(timezone.utc).isoformat())
        for owner_id, text_hash, vec in items
        if vec
    ]
    if not rows:
        return 0
    with connect() as conn:
        conn.executemany(
            """
            INSERT INTO vector_store
                (owner_kind, owner_id, model, dim, text_hash, vec, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(owner_kind, owner_id, model) DO UPDATE SET
                dim = excluded.dim,
                text_hash = excluded.text_hash,
                vec = excluded.vec,
                created_at = excluded.created_at
            """,
            rows,
        )
    return len(rows)


def embed_with_cache(
    owner_kind: str,
    items: Sequence[tuple[str, str, str]],
    *,
    user_id: int | None = None,
    model: str | None = None,
) -> dict[str, list[float]]:
    """Embed ``(owner_id, text_hash, text)`` items, reusing cached vectors.

    Raises :class:`EmbeddingUnavailable` when nothing is cached and no backend
    is reachable.
    """
    if not items:
        return {}
    _, resolved = embedding_settings(user_id)
    model = model or resolved
    if not model:
        raise EmbeddingUnavailable("No embedding model configured")

    cached = get_cached(owner_kind, [(oid, h) for oid, h, _ in items], model)
    missing = [(oid, h, text) for oid, h, text in items if oid not in cached and text.strip()]
    if missing:
        vectors = embed_texts([text for _, _, text in missing], user_id=user_id, model=model)
        put_many(owner_kind, [(oid, h, v) for (oid, h, _), v in zip(missing, vectors)], model)
        cached.update({oid: v for (oid, _, _), v in zip(missing, vectors)})
    return cached


def purge(owner_kind: str | None = None, *, model: str | None = None) -> int:
    """Drop cached vectors (all, or narrowed by owner kind / model)."""
    clauses: list[str] = []
    params: list[str] = []
    if owner_kind:
        clauses.append("owner_kind = ?")
        params.append(owner_kind)
    if model:
        clauses.append("model = ?")
        params.append(model)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    with connect() as conn:
        return conn.execute(f"DELETE FROM vector_store{where}", params).rowcount


# --- similarity ---------------------------------------------------------


def similarity_matrix(queries: Sequence[Sequence[float]], docs: Sequence[Sequence[float]]):
    """Cosine similarity of every query against every doc: ``[len(queries)][len(docs)]``."""
    if not queries or not docs:
        return [[0.0] * len(docs) for _ in queries]
    try:
        import numpy as np

        q = np.asarray(queries, dtype="float32")
        d = np.asarray(docs, dtype="float32")
        if q.shape[1] != d.shape[1]:
            return [[0.0] * len(docs) for _ in queries]
        q /= np.linalg.norm(q, axis=1, keepdims=True).clip(min=1e-12)
        d /= np.linalg.norm(d, axis=1, keepdims=True).clip(min=1e-12)
        return (q @ d.T).tolist()
    except ImportError:
        return [[cosine(qv, dv) for dv in docs] for qv in queries]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
