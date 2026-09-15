"""Requirement judging: citation validation, fallbacks, and lexical verdicts."""

from __future__ import annotations

import unittest
from unittest import mock

from src.matching.judge import judge_requirements, lexical_verdicts
from src.matching.models import Requirement, ResumeChunk

REQUIREMENTS = [
    Requirement(id="r1", text="Experience with Kubernetes", kind="must"),
    Requirement(id="r2", text="Go programming", kind="nice"),
]

CHUNK = ResumeChunk(id="work-0", kind="work", ref="Backend @ Acme", text="Ran Kubernetes clusters")
RETRIEVED = {"r1": [(CHUNK, 0.8)], "r2": []}


def _response(verdicts):
    return {"verdicts": verdicts}


class JudgeTests(unittest.TestCase):
    def _judge(self, response):
        # Patched where judge.py looks the names up (it imports them at
        # module level), not where src.llm/src.config_store define them —
        # see the mock.patch docs on "where to patch".
        with mock.patch("src.matching.judge.chat_json", return_value=response), mock.patch(
            "src.matching.judge.get_merged_config", return_value={"quality_model": "test-model"}
        ):
            return judge_requirements(REQUIREMENTS, RETRIEVED)

    def test_cited_verdict_is_kept_with_its_evidence(self):
        verdicts = self._judge(
            _response(
                [
                    {
                        "id": "r1",
                        "verdict": "met",
                        "confidence": 0.9,
                        "evidence": [{"chunk_id": "work-0", "quote": "Ran Kubernetes clusters"}],
                        "note": "direct match",
                    },
                    {"id": "r2", "verdict": "missing", "confidence": 0.9, "evidence": []},
                ]
            )
        )
        by_id = {v.id: v for v in verdicts}
        self.assertEqual(by_id["r1"].verdict, "met")
        self.assertEqual(by_id["r1"].evidence[0].ref, "Backend @ Acme")
        self.assertEqual(by_id["r2"].verdict, "missing")

    def test_met_without_a_valid_citation_is_downgraded(self):
        verdicts = self._judge(
            _response([{"id": "r1", "verdict": "met", "confidence": 1.0, "evidence": []}])
        )
        by_id = {v.id: v for v in verdicts}
        self.assertEqual(by_id["r1"].verdict, "partial")
        self.assertIn("downgraded", by_id["r1"].note)

    def test_citation_of_a_chunk_that_was_never_shown_is_dropped(self):
        verdicts = self._judge(
            _response(
                [
                    {
                        "id": "r1",
                        "verdict": "met",
                        "evidence": [{"chunk_id": "work-99", "quote": "invented"}],
                    }
                ]
            )
        )
        by_id = {v.id: v for v in verdicts}
        self.assertEqual(by_id["r1"].evidence, [])
        self.assertEqual(by_id["r1"].verdict, "partial")

    def test_requirement_with_no_verdict_returned_defaults_to_missing(self):
        verdicts = self._judge(_response([{"id": "r1", "verdict": "met",
                                           "evidence": [{"chunk_id": "work-0", "quote": "k8s"}]}]))
        by_id = {v.id: v for v in verdicts}
        self.assertEqual(by_id["r2"].verdict, "missing")
        self.assertIn("no verdict", by_id["r2"].note)

    def test_unknown_verdict_string_is_treated_as_missing(self):
        verdicts = self._judge(_response([{"id": "r1", "verdict": "probably?"}]))
        self.assertEqual({v.id: v for v in verdicts}["r1"].verdict, "missing")

    def test_every_requirement_is_accounted_for(self):
        self.assertEqual(len(self._judge(_response([]))), len(REQUIREMENTS))


class LexicalVerdictTests(unittest.TestCase):
    def test_strong_retrieval_reads_as_met(self):
        verdicts = lexical_verdicts(REQUIREMENTS, {"r1": [(CHUNK, 0.9)], "r2": []})
        self.assertEqual(verdicts[0].verdict, "met")
        self.assertEqual(verdicts[0].evidence[0].chunk_id, "work-0")

    def test_weak_retrieval_reads_as_partial(self):
        self.assertEqual(lexical_verdicts(REQUIREMENTS, {"r1": [(CHUNK, 0.3)]})[0].verdict, "partial")

    def test_no_evidence_reads_as_missing(self):
        verdicts = lexical_verdicts(REQUIREMENTS, {"r1": [], "r2": []})
        self.assertTrue(all(v.verdict == "missing" for v in verdicts))
        self.assertTrue(all(v.evidence == [] for v in verdicts))


if __name__ == "__main__":
    unittest.main()
