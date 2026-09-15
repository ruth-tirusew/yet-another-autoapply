"""Public entry point for the vector prefilter.

The implementation lives in :mod:`src.matching.prefilter` (chunked
retrieval, shared with the grounded matcher's vector cache); this module
just re-exports the stable names other code imports (``prefilter_job``,
``prefilter_all``) so callers and configuration keep working unchanged.
"""

from __future__ import annotations

from src.matching.prefilter import PrefilterBatch, prefilter_all, prefilter_job

__all__ = ["PrefilterBatch", "prefilter_all", "prefilter_job"]
