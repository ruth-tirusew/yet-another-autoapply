"""Retrieval-grounded ATS matching.

Two stages:

1. **Extract** — a posting is parsed once into structured requirements
   (:mod:`src.matching.requirements`). The result is cached on the shared
   ``catalog_jobs`` row, so the cost is paid once per posting and amortized
   across every user whose queue contains it.
2. **Judge** — each requirement is scored against the resume chunks that
   retrieval actually surfaced for it (:mod:`src.matching.retrieval`,
   :mod:`src.matching.judge`), then category and overall scores are
   *recomputed* deterministically from those verdicts
   (:mod:`src.matching.scoring`).

The LLM never reports a final score; it only judges individual
requirement/evidence pairs. Every number in the result is reproducible from
the stored verdicts.
"""

from __future__ import annotations

from src.matching.models import (
    Evidence,
    Requirement,
    RequirementSet,
    RequirementVerdict,
    ResumeChunk,
)

__all__ = [
    "Evidence",
    "Requirement",
    "RequirementSet",
    "RequirementVerdict",
    "ResumeChunk",
    "grounded_match",
]


def grounded_match(*args, **kwargs):
    """Deferred re-export — this one is a real cycle, confirmed the hard way.

    ``src.ats`` does ``from src.matching.models import RequirementVerdict``.
    Importing any submodule of a package runs that package's ``__init__.py``
    first, so merely importing ``src.matching.models`` — a leaf with nothing
    to do with scoring — would force this file to run. If this file then
    imported :mod:`src.matching.engine` at its own top level, and
    ``engine.py`` needs ``src.ats.JobMatchResult``, that's ``src.ats`` (still
    mid-import, having not yet defined ``JobMatchResult``) being asked to
    hand back a name it doesn't have yet — reproduced directly by hoisting
    this import: ``python -c "import src.matcher"`` then raises
    ``ImportError: cannot import name 'JobMatchResult' from partially
    initialized module 'src.ats'``. A prior pass "fixed" this deferral away
    on the strength of a hoist test that only checked ``import
    src.matching`` in isolation — it missed that ``src.ats`` is the far more
    common real entry point, which is exactly the gap the direct
    reproduction above is meant to close.
    """
    from src.matching.engine import grounded_match as _impl

    return _impl(*args, **kwargs)
