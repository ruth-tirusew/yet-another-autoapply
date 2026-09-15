"""Deterministic aggregation: coverage, years, seniority, recommendation."""

from __future__ import annotations

import unittest
from datetime import date

from src.matching.models import Evidence, Requirement, RequirementSet, RequirementVerdict
from src.matching.scoring import (
    build_result,
    compute_experience_match,
    compute_role_fit,
    compute_skill_match,
    coverage,
    derive_recommendation,
    extract_required_years,
    years_of_experience,
)


def verdict(id_: str, kind: str, result: str, category: str = "skill") -> RequirementVerdict:
    return RequirementVerdict(
        id=id_,
        text=f"requirement {id_}",
        kind=kind,
        category=category,
        verdict=result,
        evidence=[Evidence(chunk_id="work-0", ref="Eng @ Acme")] if result != "missing" else [],
    )


class RequiredYearsTests(unittest.TestCase):
    def test_ignores_years_stated_about_the_company(self):
        text = "We have been in business for 20 years. You need 5+ years of Python experience."
        self.assertEqual(extract_required_years(text), 5)

    def test_ignores_benefits_copy(self):
        self.assertIsNone(extract_required_years("Sabbatical after 10 years with us"))

    def test_range_reads_as_the_entry_bar(self):
        self.assertEqual(extract_required_years("3-5 years of professional experience"), 3)

    def test_nice_to_have_years_do_not_lower_the_bar(self):
        text = "- 7+ years of hands-on backend experience\n- 2 years of Go is a plus"
        self.assertEqual(extract_required_years(text), 7)

    def test_no_statement_returns_none(self):
        self.assertIsNone(extract_required_years("We want a great engineer."))


class YearsOfExperienceTests(unittest.TestCase):
    def test_overlapping_roles_are_not_double_counted(self):
        work = [
            {"startDate": "2018-01", "endDate": "2020-01"},
            {"startDate": "2019-01", "endDate": "2021-01"},
        ]
        self.assertAlmostEqual(years_of_experience(work), 3.0, places=1)

    def test_career_gaps_are_excluded(self):
        work = [
            {"startDate": "2010-01", "endDate": "2012-01"},
            {"startDate": "2020-01", "endDate": "2022-01"},
        ]
        self.assertAlmostEqual(years_of_experience(work), 4.0, places=1)

    def test_ongoing_role_runs_to_today(self):
        work = [{"startDate": "2020-01", "endDate": ""}]
        today = date(2024, 1, 1)
        self.assertAlmostEqual(years_of_experience(work, today=today), 4.0, places=1)

    def test_no_work_history(self):
        self.assertEqual(years_of_experience([]), 0.0)
        self.assertEqual(years_of_experience(None), 0.0)


class CoverageTests(unittest.TestCase):
    def test_coverage_is_weighted_towards_must_haves(self):
        verdicts = [verdict("r1", "must", "missing"), verdict("r2", "nice", "met")]
        # nice-to-haves carry 0.4 weight: 0.4 earned of 1.4 total
        self.assertAlmostEqual(coverage(verdicts), 0.4 / 1.4, places=3)

    def test_partial_earns_half_credit(self):
        self.assertAlmostEqual(coverage([verdict("r1", "must", "partial")]), 0.5, places=3)

    def test_no_requirements_is_zero(self):
        self.assertEqual(coverage([]), 0.0)

    def test_skill_score_measures_requirements_covered_not_resume_terms(self):
        # A resume with many extra skills must not be penalised: only the
        # posting's own requirements count.
        verdicts = [verdict(f"r{i}", "must", "met") for i in range(4)]
        score, cov = compute_skill_match(verdicts)
        self.assertEqual(score.score, 40)
        self.assertEqual(cov, 1.0)

    def test_missing_must_haves_are_named_in_the_evidence(self):
        verdicts = [verdict("r1", "must", "met"), verdict("r2", "must", "missing")]
        score, _ = compute_skill_match(verdicts)
        self.assertIn("missing", score.evidence)
        self.assertIn("requirement r2", score.evidence)


class ExperienceAndRoleFitTests(unittest.TestCase):
    def test_meeting_the_year_requirement_scores_full(self):
        score = compute_experience_match(years_required=5, candidate_years=6.0)
        self.assertEqual(score.score, 35)

    def test_large_shortfall_scores_low(self):
        score = compute_experience_match(years_required=10, candidate_years=2.0)
        self.assertLess(score.score, 10)

    def test_unstated_requirement_is_neutral_not_zero(self):
        score = compute_experience_match(years_required=None, candidate_years=3.0)
        self.assertGreater(score.score, 25)

    def test_same_level_scores_full_role_fit(self):
        score = compute_role_fit(posting_seniority="senior", candidate_seniority="senior")
        self.assertEqual(score.score, 25)

    def test_distant_level_scores_low(self):
        score = compute_role_fit(posting_seniority="director", candidate_seniority="junior")
        self.assertLessEqual(score.score, 5)

    def test_unknown_seniority_is_neutral(self):
        score = compute_role_fit(posting_seniority="", candidate_seniority="senior")
        self.assertEqual(score.score, 15)


class RecommendationTests(unittest.TestCase):
    def test_low_role_fit_skips_regardless_of_score(self):
        rec, reason = derive_recommendation(
            overall=95,
            verdicts=[verdict("r1", "must", "met")],
            role_fit=compute_role_fit(posting_seniority="director", candidate_seniority="junior"),
            threshold=70,
            role_fit_min=15,
        )
        self.assertEqual(rec, "skip")
        self.assertIn("Role fit", reason)

    def test_mostly_missing_must_haves_skips(self):
        verdicts = [verdict("r1", "must", "missing"), verdict("r2", "must", "missing"), verdict("r3", "must", "met")]
        rec, reason = derive_recommendation(
            overall=80,
            verdicts=verdicts,
            role_fit=compute_role_fit(posting_seniority="senior", candidate_seniority="senior"),
            threshold=70,
            role_fit_min=15,
        )
        self.assertEqual(rec, "skip")
        self.assertIn("required items", reason)

    def test_strong_match_applies(self):
        rec, _ = derive_recommendation(
            overall=85,
            verdicts=[verdict("r1", "must", "met")],
            role_fit=compute_role_fit(posting_seniority="senior", candidate_seniority="senior"),
            threshold=70,
            role_fit_min=15,
        )
        self.assertEqual(rec, "apply")


class BuildResultTests(unittest.TestCase):
    def _result(self, verdicts):
        return build_result(
            verdicts=verdicts,
            req_set=RequirementSet(
                requirements=[Requirement(id=v.id, text=v.text, kind=v.kind) for v in verdicts],
                years_required=3,
                seniority="senior",
            ),
            candidate_years=6.0,
            candidate_seniority="senior",
            job_title="Senior Backend Engineer",
            threshold=70,
            role_fit_min=15,
        )

    def test_overall_is_the_sum_of_its_parts(self):
        result = self._result([verdict("r1", "must", "met"), verdict("r2", "must", "partial")])
        self.assertEqual(
            result.overall_score,
            result.skill_match.score + result.experience_match.score + result.role_fit.score,
        )

    def test_scoring_is_reproducible(self):
        verdicts = [verdict("r1", "must", "met"), verdict("r2", "nice", "missing")]
        self.assertEqual(self._result(verdicts).overall_score, self._result(verdicts).overall_score)

    def test_gaps_and_hints_come_from_verdicts(self):
        result = self._result(
            [
                verdict("r1", "must", "met"),
                verdict("r2", "must", "missing"),
                verdict("r3", "must", "partial"),
            ]
        )
        self.assertEqual(result.engine, "grounded")
        self.assertEqual(result.gaps, ["requirement r2"])
        self.assertTrue(any("requirement r3" in h for h in result.tailoring_hints))
        self.assertTrue(any("requirement r1" in s for s in result.strengths))
        self.assertEqual(len(result.requirements), 3)

    def test_empty_requirements_do_not_crash(self):
        result = self._result([])
        self.assertEqual(result.skill_match.score, 0)
        self.assertLessEqual(result.overall_score, 100)


if __name__ == "__main__":
    unittest.main()
