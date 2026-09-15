"""Guards against reintroducing avoidable inline (function-local) imports.

An inline ``from src.x import y`` inside a function is only ever justified
by a genuine circular dependency between modules — never by convenience or
habit. Every inline import in the matching/niche-term rebuild was checked by
actually hoisting it to module level and attempting the import; all but two
turned out to have no real cycle at all and were moved to the top of the
file. This suite (a) asserts those modules stay free of inline ``src.*``
imports, and (b) pins down the two genuine cycles as explicit facts about
the import graph, so a change that breaks either cycle is something this
test will prompt you to *act on* (hoist the now-safe import) rather than
something that silently leaves a needless inline import in place.
"""

from __future__ import annotations

import importlib
import inspect
import re
import unittest

_INLINE_SRC_IMPORT = re.compile(r"^\s+(?:from src\.|import src\.)")


def _inline_import_lines(module_name: str) -> list[str]:
    module = importlib.import_module(module_name)
    source = open(module.__file__, encoding="utf-8").read()
    return [line for line in source.splitlines() if _INLINE_SRC_IMPORT.match(line)]


def _top_level_src_imports(module_name: str) -> set[str]:
    """Module names this module imports *at its own top level* (not inside a
    function). Used to assert facts about the dependency graph directly,
    rather than re-deriving them by attempting risky in-process imports."""
    module = importlib.import_module(module_name)
    source = open(module.__file__, encoding="utf-8").read()
    found = set()
    for line in source.splitlines():
        if line.startswith(("from src.", "import src.")):
            m = re.match(r"^(?:from|import)\s+(src(?:\.\w+)*)", line)
            if m:
                found.add(m.group(1))
    return found


class NoAvoidableInlineImportsTests(unittest.TestCase):
    """Confirmed by actually hoisting each inline import to module level and
    importing the module: every one of these had no real circular
    dependency at all. Any inline ``src.*`` import reappearing here is a
    regression — check for a real cycle (as in the class below) before
    reintroducing one rather than deferring out of habit."""

    CLEAN_MODULES = [
        # src.niche_terms is intentionally excluded: get_user_niche_terms
        # keeps one deliberate deferred import — see RealCyclesAreDocumentedTests.
        "src.matching.models",
        "src.matching.chunking",
        "src.matching.vectors",
        "src.matching.retrieval",
        "src.matching.requirements",
        "src.matching.judge",
        "src.matching.scoring",
        "src.matching.engine",
        "src.matching.prefilter",
        "src.embeddings",
        "src.prefilter",
    ]

    def test_no_inline_src_imports(self):
        for module_name in self.CLEAN_MODULES:
            with self.subTest(module=module_name):
                self.assertEqual(
                    _inline_import_lines(module_name),
                    [],
                    f"{module_name} has an inline src.* import — if it's newly "
                    "needed, confirm it's a real cycle (try hoisting it and "
                    "importing the module) before adding it, rather than "
                    "deferring out of habit.",
                )


class RealCyclesAreDocumentedTests(unittest.TestCase):
    """The only two genuine import cycles found in the codebase, pinned down
    as facts about the dependency graph rather than merely asserted. If
    either edge below disappears, the corresponding deferred import (named
    in each test) has become unnecessary and should be hoisted."""

    def test_ats_to_generalize_profile_cycle_via_profile_and_hiring_agent_bridge(self):
        """ats -> coaching.generalize_profile -> profile ->
        hiring_agent_bridge -> ats(/niche_terms) is why
        src.niche_terms.get_user_niche_terms keeps its import of
        src.coaching.generalize_profile deferred to call time — reproduced
        directly: `python -c "from src.coaching.generalize_profile import
        load_niche_terms"` after moving that import to the top of
        src/ats.py raises `ImportError: cannot import name
        'posting_mentions_niche_domain' from partially initialized module
        'src.ats'`.
        """
        generalize_profile_deps = _top_level_src_imports("src.coaching.generalize_profile")
        profile_deps = _top_level_src_imports("src.profile")
        hiring_agent_bridge_deps = _top_level_src_imports("src.hiring_agent_bridge")

        self.assertIn(
            "src.profile",
            generalize_profile_deps,
            "generalize_profile no longer imports profile at module level — "
            "the ats/niche_terms cycle through this path may be gone",
        )
        self.assertIn(
            "src.hiring_agent_bridge",
            profile_deps,
            "profile no longer imports hiring_agent_bridge at module level — "
            "the ats/niche_terms cycle through this path may be gone",
        )
        self.assertTrue(
            "src.ats" in hiring_agent_bridge_deps or "src.niche_terms" in hiring_agent_bridge_deps,
            "hiring_agent_bridge no longer closes the loop back to ats/niche_terms",
        )

        source = inspect.getsource(importlib.import_module("src.niche_terms").get_user_niche_terms)
        self.assertIn(
            "from src.coaching.generalize_profile import load_niche_terms",
            source,
            "get_user_niche_terms no longer defers this import — if the cycle "
            "above is genuinely gone, hoist it to module level instead of "
            "leaving it deferred out of habit",
        )

    def test_profile_and_generalize_profile_are_directly_mutually_dependent(self):
        """A direct two-node cycle: src.profile needs
        src.coaching.generalize_profile (for the general resume variant) and
        src.coaching.generalize_profile needs src.profile (to load the base
        resume) — reproduced directly: hoisting
        resolve_resume_variant_for_job's import to module level raises
        `ImportError: cannot import name 'load_resume' from partially
        initialized module 'src.profile'`.
        """
        generalize_profile_deps = _top_level_src_imports("src.coaching.generalize_profile")
        self.assertIn(
            "src.profile",
            generalize_profile_deps,
            "generalize_profile no longer imports profile at module level — "
            "the direct cycle may be gone",
        )

        source = inspect.getsource(
            importlib.import_module("src.profile").resolve_resume_variant_for_job
        )
        self.assertIn(
            "from src.coaching.generalize_profile import load_general_resume",
            source,
            "resolve_resume_variant_for_job no longer defers this import — if "
            "the cycle above is genuinely gone, hoist it to module level "
            "instead of leaving it deferred out of habit",
        )

    def test_ats_and_matching_package_are_mutually_dependent(self):
        """A subtler direct cycle, found only by testing the realistic entry
        point rather than the module in isolation: src.ats does ``from
        src.matching.models import RequirementVerdict``, and importing *any*
        submodule of a package runs that package's ``__init__.py`` first —
        so merely needing the leaf ``matching.models`` forces
        ``src/matching/__init__.py`` to execute. src.matching.engine (which
        that ``__init__.py`` re-exports ``grounded_match`` from) needs
        ``src.ats.JobMatchResult`` — asking ``src.ats`` for a name it hasn't
        defined yet, since it's still mid-import at that point.

        This was caught by testing ``import src.matcher`` (a realistic entry
        point) after a change that hoisted the re-export, which raised
        ``ImportError: cannot import name 'JobMatchResult' from partially
        initialized module 'src.ats'`` — a plain ``import src.matching`` in
        isolation does not surface it, because that path never routes
        through src.ats first. Hoist-testing a module only by importing it
        directly is not sufficient; the realistic entry point matters.
        """
        ats_deps = _top_level_src_imports("src.ats")
        self.assertIn(
            "src.matching.models",
            ats_deps,
            "ats no longer imports matching.models at module level — the "
            "cycle through src.matching's __init__ may be gone",
        )

        engine_deps = _top_level_src_imports("src.matching.engine")
        self.assertIn(
            "src.ats",
            engine_deps,
            "matching.engine no longer imports src.ats at module level — "
            "the cycle may be gone",
        )

        source = inspect.getsource(importlib.import_module("src.matching").grounded_match)
        self.assertIn(
            "from src.matching.engine import grounded_match as _impl",
            source,
            "src.matching.grounded_match no longer defers this import — if "
            "the cycle above is genuinely gone, hoist it to module level "
            "instead of leaving it deferred out of habit (but re-verify via "
            "`python -c \"import src.matcher\"`, not just `import "
            "src.matching`, before doing so)",
        )


if __name__ == "__main__":
    unittest.main()
