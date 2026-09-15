"""Tests for niche-domain text matching and its use in profile_format."""

import unittest

from src.niche_terms import (
    partition_strengths,
    posting_mentions_niche_domain,
    posting_mentions_user_niche,
    text_mentions_niche_terms,
)
from src.profile_format import format_experience_text, soften_niche_phrasing, summary_for_job

FIXTURE_TERMS = ["murabaha", "sharia", "islamic finance", "halal finance", "islamic banking"]


class NicheDomainTests(unittest.TestCase):
    def test_posting_mentions_niche_only_for_profile_terms(self):
        assert posting_mentions_user_niche(
            "Islamic Finance Engineer", "", niche_terms=FIXTURE_TERMS
        )
        assert posting_mentions_user_niche(
            "", "We need Murabaha product experience", niche_terms=FIXTURE_TERMS
        )
        assert not posting_mentions_user_niche(
            "Backend Engineer", "fintech startup building payments", niche_terms=FIXTURE_TERMS
        )
        assert not posting_mentions_user_niche(
            "Software Engineer, iOS Core Product",
            "Scalable Capital\nBanking\nEngineering",
            niche_terms=FIXTURE_TERMS,
        )

    def test_posting_mentions_niche_domain_wrapper(self):
        assert posting_mentions_niche_domain(
            "Murabaha Engineer", "", niche_terms=FIXTURE_TERMS
        )
        assert not posting_mentions_niche_domain(
            "Backend Engineer", "payments API", niche_terms=FIXTURE_TERMS
        )

    def test_text_mentions_niche_terms(self):
        assert text_mentions_niche_terms("Sharia-compliant products", FIXTURE_TERMS)
        assert not text_mentions_niche_terms("Python microservices", FIXTURE_TERMS)

    def test_partition_moves_sharia_strengths_to_background(self):
        strengths = [
            "Sharia-Compliant Financing Solutions",
            "Microservices Architecture",
            "API Design",
        ]
        primary, background = partition_strengths(
            strengths,
            job_title="Software Engineer, iOS",
            job_description="Build SwiftUI features for our reading app.",
            niche_terms=FIXTURE_TERMS,
        )
        self.assertIn("Microservices Architecture", primary)
        self.assertIn("Sharia-Compliant Financing Solutions", background)
        self.assertNotIn("Sharia-Compliant Financing Solutions", primary)

    def test_partition_keeps_sharia_primary_when_posting_mentions_it(self):
        strengths = ["Sharia-Compliant Financing Solutions", "Microservices Architecture"]
        primary, background = partition_strengths(
            strengths,
            job_title="Murabaha Product Engineer",
            job_description="Islamic finance lending platform.",
            niche_terms=FIXTURE_TERMS,
        )
        self.assertIn("Sharia-Compliant Financing Solutions", primary)
        self.assertEqual(background, [])

    def test_summary_softens_niche_for_general_jobs(self):
        summary = (
            "Backend Developer with experience in Sharia-compliant financing solutions "
            "and Apache Fineract."
        )
        result = summary_for_job(
            summary,
            job_title="Backend Engineer",
            job_description="APIs and data",
            niche_terms=FIXTURE_TERMS,
        )
        self.assertNotIn("Sharia", result)
        self.assertIn("Fineract", result)

    def test_summary_unchanged_for_niche_posting(self):
        summary = "Backend Developer with Sharia-compliant financing solutions."
        result = summary_for_job(
            summary,
            job_title="Islamic Finance Developer",
            job_description="Murabaha products",
            niche_terms=FIXTURE_TERMS,
        )
        self.assertEqual(result, summary)

    def test_format_experience_deprioritizes_niche_projects(self):
        resume = {
            "work": [],
            "projects": [
                {
                    "name": "Apache Fineract Customization",
                    "description": "Customized Fineract for Sharia-compliant financial products.",
                    "technologies": ["Fineract"],
                },
                {
                    "name": "Data Pipelines & ETL",
                    "description": "Built ETL pipelines using Prefect for analytics.",
                    "technologies": ["Prefect"],
                },
            ],
        }
        text = format_experience_text(
            resume,
            job_title="Backend Engineer",
            job_description="APIs",
            niche_terms=FIXTURE_TERMS,
        )
        self.assertLess(text.index("Data Pipelines"), text.index("Fineract Customization"))

    def test_soften_niche_phrasing(self):
        self.assertIn(
            "lending-platform",
            soften_niche_phrasing("Sharia-compliant financing solutions"),
        )


if __name__ == "__main__":
    unittest.main()
