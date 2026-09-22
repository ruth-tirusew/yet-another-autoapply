"""Embedding backend and a model-tagged vector cache.

Problems with the previous embedding layer fixed here:

* **Untagged vectors.** Stored blobs recorded neither the model that produced
  them nor the text they were built from, so switching ``embedding_model``
  silently compared vectors from different spaces until someone remembered to
  run ``embeddings reset`` by hand. Every row here carries ``(model, dim,
  text_hash)``; a lookup that disagrees on model or text_hash is a cache
  miss, and a dim disagreement within an otherwise-matching batch (e.g. an
  Ollama model re-pulled under the same name with different weights) is
  logged and treated as stale rather than served.
* **Silent disablement.** Embeddings were hard-wired to Ollama, so a Groq or
  Gemini user got no vectors, no prefilter and no warning. The backend now
  follows the configured provider and reports when it is unavailable instead
  of failing open.
* **Symmetric embedding of an asymmetric task.** Requirements were embedded
  the same way as the resume chunks they're matched against, even though the
  supported backends (mxbai-embed-large, Gemini's text-embedding-004) are
  trained for asymmetric retrieval and expect the query side (here,
  requirements) and the document side (resume chunks) to be told apart.
  ``embed_with_cache`` now tags each owner kind with a role, and that role is
  folded into the cache key so pre-role vectors are recomputed rather than
  silently reused.
"""

from __future__ import annotations

import logging
import math
import struct
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

import numpy as np

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

# Embedding models known to belong to a particular provider. Used only to
# spot an ``embedding_model`` left over from switching providers: the setting
# isn't namespaced per provider, and the settings UI hides the field for
# hosted providers rather than clearing it. A model in none of these sets is
# taken as a deliberate choice for the active provider, since Ollama users
# pull arbitrary names and hosted providers ship new models faster than this
# table can track them.
PROVIDER_EMBED_MODELS = {
    "ollama": {"mxbai-embed-large", "nomic-embed-text"},
    "gemini": {"text-embedding-004"},
}

# Retrieval role per owner kind: resume chunks are the corpus being searched
# (the "document" side), requirements are what's searched with (the "query"
# side) — see Retriever.retrieve, which calls similarity_matrix(requirement
# vectors, chunk vectors).
OWNER_ROLES = {
    OWNER_RESUME_CHUNK: "document",
    OWNER_REQUIREMENT: "query",
}

# Owner kinds whose vectors belong to one tenant. Everything else (currently
# just requirements, keyed by catalog job + requirement id) is intentionally
# shared across users so a posting is only embedded once regardless of how
# many users' catalogs it appears in.
TENANT_SCOPED_OWNER_KINDS = {OWNER_RESUME_CHUNK}

# Rough context limits (whitespace/char-based token estimate) for models
# whose backend truncates silently past this length instead of erroring.
EMBED_TOKEN_LIMITS = {
    "mxbai-embed-large": 512,
    "nomic-embed-text": 2048,
    "text-embedding-004": 2048,
}

# Providers whose embeddings endpoint caps how many texts fit in one call.
PROVIDER_BATCH_LIMITS = {
    "gemini": 100,
}

# Stay comfortably under SQLite's default 999-variable-per-statement limit.
_SQLITE_IN_BATCH = 400


class EmbeddingUnavailable(RuntimeError):
    """No embedding backend is reachable for the current provider."""


class VectorDimensionMismatch(ValueError):
    """Query and doc vectors were embedded under incompatible models/dims."""


def pack(vec: Sequence[float] | None) -> bytes | None:
    if not vec:
        return None
    return struct.pack(f"{len(vec)}f", *vec)


def unpack(blob: bytes | None) -> list[float] | None:
    if not blob:
        return None
    return list(struct.unpack(f"{len(blob) // 4}f", blob))


def _owning_provider(model: str) -> str:
    """Provider a model name is known to belong to, or "" if unrecognised."""
    base = (model or "").split(":", 1)[0]  # strip an Ollama tag ("model:latest")
    for name, models in PROVIDER_EMBED_MODELS.items():
        if base in models:
            return name
    return ""


def embedding_settings(user_id: int | None = None) -> tuple[str, str]:
    """Return ``(provider, model)`` for embeddings, following the LLM provider."""
    cfg = get_merged_config(resolve_user_id(user_id))
    matching = cfg.get("matching", {}) or {}
    provider = str(matching.get("embedding_provider") or "auto").strip().lower()
    if provider in ("", "auto"):
        provider = str(cfg.get("llm_provider") or "ollama").strip().lower()
    model = str(matching.get("embedding_model") or "").strip()

    # A configured model that is known to belong to a *different* provider is
    # a leftover from switching providers, not a choice for this one, and
    # sending it on (e.g. an Ollama model name to Gemini) just 404s into a
    # silent lexical-only fallback. Anything this table doesn't recognise is
    # respected as the user's choice.
    owner = _owning_provider(model)
    stale = bool(owner) and owner != provider
    if not model or stale:
        model = DEFAULT_EMBED_MODELS.get(provider, "" if stale else model)
    return provider, model or ""


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _model_limit(table: dict[str, int], model: str) -> int | None:
    for name, limit in table.items():
        if model == name or model.startswith(f"{name}:"):
            return limit
    return None


def embed_texts(
    texts: Sequence[str],
    *,
    user_id: int | None = None,
    model: str | None = None,
    role: str = "document",
) -> list[list[float]]:
    """Embed a batch of texts with the tenant's provider.

    ``role`` is ``"query"`` or ``"document"``: mxbai-embed-large and Gemini's
    text-embedding-004 are trained for asymmetric retrieval and expect the
    two sides of a search to be told apart, not embedded identically.

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

    token_limit = _model_limit(EMBED_TOKEN_LIMITS, model)
    if token_limit:
        long_count = sum(1 for t in texts if _approx_tokens(t) > token_limit)
        if long_count:
            logger.warning(
                "[vectors] %d/%d texts exceed the ~%d token context of %r and will be "
                "silently truncated by the backend — chunk them smaller upstream",
                long_count, len(texts), token_limit, model,
            )

    provider = get_provider(user_id)
    embed = getattr(provider, "embed", None)
    if embed is None:
        raise EmbeddingUnavailable(
            f"Provider {provider_name!r} does not support embeddings — "
            "vector retrieval is disabled; matching falls back to lexical retrieval."
        )

    texts_list = list(texts)
    batch_limit = PROVIDER_BATCH_LIMITS.get(provider_name)
    batches = (
        [texts_list[i : i + batch_limit] for i in range(0, len(texts_list), batch_limit)]
        if batch_limit
        else [texts_list]
    )
    vectors: list[list[float]] = []
    for batch in batches:
        try:
            batch_vectors = embed(model, batch, input_type=role)
        except EmbeddingUnavailable:
            raise
        except Exception as e:  # network, auth, missing model
            raise EmbeddingUnavailable(f"Embedding call failed ({provider_name}/{model}): {e}") from e
        if len(batch_vectors) != len(batch):
            raise EmbeddingUnavailable(
                f"Embedding backend returned {len(batch_vectors)} vectors for {len(batch)} inputs"
            )
        vectors.extend(batch_vectors)

    out = [[float(x) for x in v] for v in vectors]
    dims = {len(v) for v in out}
    if len(dims) > 1:
        raise EmbeddingUnavailable(
            f"Embedding backend ({provider_name}/{model}) returned inconsistent dimensions "
            f"{sorted(dims)} within one batch"
        )
    if any(not math.isfinite(x) for v in out for x in v):
        raise EmbeddingUnavailable(f"Embedding backend ({provider_name}/{model}) returned NaN/Inf values")
    return out


# --- model-tagged cache -------------------------------------------------


def get_cached(
    owner_kind: str,
    keys: Iterable[tuple[str, str]],
    model: str,
    *,
    user_id: int | None = None,
) -> dict[str, list[float]]:
    """Return cached vectors for ``(owner_id, text_hash)`` pairs under ``model``.

    ``user_id``, when given, is a real column filter (defense in depth on
    top of any per-tenant prefix already baked into ``owner_id``) — pass it
    for tenant-scoped owner kinds so one tenant's lookup can never be served
    another tenant's row.
    """
    keys = list(keys)
    if not keys:
        return {}
    by_id: dict[str, str] = {}
    for owner_id, h in keys:
        if owner_id in by_id and by_id[owner_id] != h:
            logger.warning(
                "[vectors] duplicate owner_id %r in one batch with different text_hash — keeping the last",
                owner_id,
            )
        by_id[owner_id] = h
    owner_ids = list(by_id.keys())

    rows: list[Any] = []
    with connect() as conn:
        for i in range(0, len(owner_ids), _SQLITE_IN_BATCH):
            chunk = owner_ids[i : i + _SQLITE_IN_BATCH]
            placeholders = ",".join("?" * len(chunk))
            clauses = ["owner_kind = ?", "model = ?", f"owner_id IN ({placeholders})"]
            params: list[Any] = [owner_kind, model, *chunk]
            if user_id is not None:
                clauses.append("user_id = ?")
                params.append(user_id)
            rows.extend(
                conn.execute(
                    f"SELECT owner_id, text_hash, dim, vec FROM vector_store WHERE {' AND '.join(clauses)}",
                    params,
                ).fetchall()
            )
    current = [row for row in rows if row["text_hash"] == by_id.get(row["owner_id"])]
    if not current:
        return {}  # text changed (e.g. description enriched) — recompute

    # No single "expected" dim is known ahead of a lookup, so drift is
    # detected within the batch: rows cached under one model should all agree
    # on dim, and when they don't, the model was re-pulled under that name
    # with different weights. Serving the majority would hand back a ragged
    # mix once the rest is re-embedded, so the whole batch is recomputed.
    dims = {row["dim"] for row in current}
    if len(dims) > 1:
        logger.warning(
            "[vectors] cached %s vectors under model %r disagree on dim %s — discarding "
            "all of them (model was likely re-pulled with different weights)",
            owner_kind, model, sorted(dims),
        )
        return {}

    out: dict[str, list[float]] = {}
    for row in current:
        vec = unpack(row["vec"])
        if vec:
            out[row["owner_id"]] = vec
    return out


def put_many(
    owner_kind: str,
    items: Iterable[tuple[str, str, Sequence[float]]],
    model: str,
    *,
    user_id: int | None = None,
) -> int:
    """Store ``(owner_id, text_hash, vector)`` rows, replacing any prior vector."""
    now = datetime.now(timezone.utc).isoformat()
    rows = [
        (owner_kind, owner_id, model, len(vec), text_hash, pack(vec), user_id, now)
        for owner_id, text_hash, vec in items
        if vec
    ]
    if not rows:
        return 0
    with connect() as conn:
        conn.executemany(
            """
            INSERT INTO vector_store
                (owner_kind, owner_id, model, dim, text_hash, vec, user_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(owner_kind, owner_id, model) DO UPDATE SET
                dim = excluded.dim,
                text_hash = excluded.text_hash,
                vec = excluded.vec,
                user_id = excluded.user_id,
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

    role = OWNER_ROLES.get(owner_kind, "document")
    scoped_user_id = resolve_user_id(user_id) if owner_kind in TENANT_SCOPED_OWNER_KINDS else None

    # Fold the role into the stored/looked-up hash: a query embedding and a
    # document embedding of the same text are different vectors and must not
    # share a cache slot, and this also means vectors cached before roles
    # existed (plain text_hash) miss cleanly instead of being served as-is.
    keyed = [(oid, f"{role}:{h}", text) for oid, h, text in items]

    cached = get_cached(owner_kind, [(oid, h) for oid, h, _ in keyed], model, user_id=scoped_user_id)
    missing = [(oid, h, text) for oid, h, text in keyed if oid not in cached and text.strip()]
    if missing:
        vectors = embed_texts([text for _, _, text in missing], user_id=user_id, model=model, role=role)
        put_many(
            owner_kind,
            [(oid, h, v) for (oid, h, _), v in zip(missing, vectors)],
            model,
            user_id=scoped_user_id,
        )
        cached.update({oid: v for (oid, _, _), v in zip(missing, vectors)})

        # The live model defines the current dimension, so a cached vector
        # that disagrees with it came from a different model under the same
        # name. Drop those rather than returning a ragged mix, which
        # similarity_matrix cannot consume. The cache converges on the next
        # run: get_cached then sees both dims and recomputes the batch.
        fresh_dim = len(vectors[0]) if vectors else 0
        mismatched = [oid for oid, v in cached.items() if len(v) != fresh_dim] if fresh_dim else []
        if mismatched:
            logger.warning(
                "[vectors] %d cached %s vectors under model %r disagree with the live "
                "model's dim %d — dropping them for this run",
                len(mismatched), owner_kind, model, fresh_dim,
            )
            for oid in mismatched:
                del cached[oid]
    return cached


def purge(
    owner_kind: str | None = None,
    *,
    model: str | None = None,
    user_id: int | None = None,
    all_tenants: bool = False,
) -> int:
    """Drop cached vectors, narrowed by owner kind / model / user.

    Wiping every tenant's vectors requires ``all_tenants=True`` explicitly —
    a call with neither ``user_id`` nor ``all_tenants`` is refused, so a
    future per-user caller (e.g. a "reset my embeddings" button) can't
    accidentally erase the whole cache by forgetting to pass a user_id.
    """
    if user_id is None and not all_tenants:
        raise ValueError(
            "purge() needs user_id=... to scope to one tenant, or all_tenants=True "
            "to intentionally wipe every tenant's vectors"
        )
    clauses: list[str] = []
    params: list[Any] = []
    if owner_kind:
        clauses.append("owner_kind = ?")
        params.append(owner_kind)
    if model:
        clauses.append("model = ?")
        params.append(model)
    if user_id is not None:
        clauses.append("user_id = ?")
        params.append(user_id)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    with connect() as conn:
        return conn.execute(f"DELETE FROM vector_store{where}", params).rowcount


# --- similarity ---------------------------------------------------------


def similarity_matrix(queries: Sequence[Sequence[float]], docs: Sequence[Sequence[float]]):
    """Cosine similarity of every query against every doc: ``[len(queries)][len(docs)]``.

    Raises :class:`VectorDimensionMismatch` when query and doc vectors don't
    share a dimension — that means they were embedded under different
    models, which is a configuration error, not "no match": returning zeros
    would silently read as the latter.
    """
    if not queries or not docs:
        return [[0.0] * len(docs) for _ in queries]
    q = np.asarray(queries, dtype="float32")
    d = np.asarray(docs, dtype="float32")
    if q.shape[1] != d.shape[1]:
        raise VectorDimensionMismatch(
            f"query vectors have dim {q.shape[1]}, doc vectors have dim {d.shape[1]}"
        )
    q /= np.linalg.norm(q, axis=1, keepdims=True).clip(min=1e-12)
    d /= np.linalg.norm(d, axis=1, keepdims=True).clip(min=1e-12)
    return (q @ d.T).tolist()


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
