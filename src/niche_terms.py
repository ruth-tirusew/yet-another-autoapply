"""Niche-domain text matching — a pure, dependency-free leaf module.

These functions used to live in ``src/ats.py`` alongside ``JobMatchResult``.
That placement created a real import cycle: ``get_user_niche_terms`` loads a
user's cached niche terms via ``src.coaching.generalize_profile``, which
depends on ``src.profile``, which depends on ``src.hiring_agent_bridge`` —
and ``hiring_agent_bridge`` needs these same predicate functions to decide
whether to soften niche phrasing. Importing them from ``src.ats`` at module
load time meant ``ats -> generalize_profile -> profile ->
hiring_agent_bridge -> ats``, a genuine cycle (confirmed by attempting it —
``ImportError: cannot import name '...' from partially initialized module
'src.ats'``), not merely a style preference for deferred imports.

The fix is structural, not a deferred import: everything here except
``get_user_niche_terms`` is a pure function of ``(text, terms)`` with no
dependency on the profile-loading stack, so it belongs in its own leaf
module that ``ats.py``, ``hiring_agent_bridge.py``, ``profile.py``,
``profile_format.py`` and ``src.matching`` can all import at the top of the
file. Only ``get_user_niche_terms`` — the one function that actually reaches
up into ``src.coaching.generalize_profile`` to load a user's terms — keeps a
function-local import, because that edge is real: it crosses from this
foundational text-matching layer into the higher orchestration layer that
itself depends on this module, so it must be deferred to call time (by
which point both modules have already finished initializing).
"""

from __future__ import annotations

import re
from functools import lru_cache

_POSTING_BODY_SCAN_CHARS = 2500


def get_user_niche_terms(user_id: int | None = None) -> list[str]:
    # Deferred: src.coaching.generalize_profile -> src.profile ->
    # src.hiring_agent_bridge -> this module. See the module docstring.
    from src.coaching.generalize_profile import load_niche_terms

    return load_niche_terms(user_id)


@lru_cache(maxsize=512)
def _niche_term_pattern(term: str) -> re.Pattern[str]:
    """Compile a term into a word-boundary-anchored pattern, cached per term.

    A plain substring check means a niche term like "go" matches "going",
    "ai" matches "said", and "ml" matches "html" — silently mis-tagging
    ordinary postings and resume strengths as niche-domain. Plain ``\\b``
    doesn't fully fix this for terms ending in punctuation (e.g. "c++"), so
    this anchors on alphanumeric adjacency directly: the match must not be
    immediately preceded or followed by a letter or digit.
    """
    return re.compile(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])")


def text_mentions_niche_terms(text: str, terms: list[str]) -> bool:
    if not text or not terms:
        return False
    lower = text.lower()
    for term in terms:
        term = (term or "").strip().lower()
        if term and _niche_term_pattern(term).search(lower):
            return True
    return False


def text_mentions_niche_domain(
    text: str,
    *,
    niche_terms: list[str] | None = None,
    user_id: int | None = None,
) -> bool:
    terms = niche_terms if niche_terms is not None else get_user_niche_terms(user_id)
    return text_mentions_niche_terms(text, terms)


def posting_mentions_user_niche(
    job_title: str = "",
    job_description: str = "",
    user_id: int | None = None,
    *,
    niche_terms: list[str] | None = None,
) -> bool:
    terms = niche_terms if niche_terms is not None else get_user_niche_terms(user_id)
    if not terms:
        return False
    title = (job_title or "").lower()
    if text_mentions_niche_terms(title, terms):
        return True
    body = (job_description or "")[:_POSTING_BODY_SCAN_CHARS].lower()
    return text_mentions_niche_terms(body, terms)


def posting_mentions_niche_domain(
    job_title: str = "",
    job_description: str = "",
    *,
    user_id: int | None = None,
    niche_terms: list[str] | None = None,
) -> bool:
    """True when the posting mentions profile-derived niche domain terms."""
    return posting_mentions_user_niche(
        job_title,
        job_description,
        user_id,
        niche_terms=niche_terms,
    )


def _strength_is_niche(strength: str, terms: list[str]) -> bool:
    return text_mentions_niche_terms(strength, terms)


def partition_strengths(
    strengths: list[str],
    *,
    job_title: str = "",
    job_description: str = "",
    user_id: int | None = None,
    niche_terms: list[str] | None = None,
) -> tuple[list[str], list[str]]:
    """Split strengths into job-relevant vs niche background (de-emphasize for generation)."""
    terms = niche_terms if niche_terms is not None else get_user_niche_terms(user_id)
    niche_ok = posting_mentions_user_niche(
        job_title, job_description, user_id, niche_terms=terms
    )
    primary: list[str] = []
    background: list[str] = []
    for strength in strengths:
        if _strength_is_niche(strength, terms) and not niche_ok:
            background.append(strength)
        else:
            primary.append(strength)
    return primary, background
