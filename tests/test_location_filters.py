"""Tests for job location parsing and eligibility."""

import unittest

from src.crawler.filters import candidate_country_code, is_job_eligible, is_worldwide, normalize_job
from src.crawler.location import candidate_matches_location, parse_job_location


class LocationParsingTests(unittest.TestCase):
    def test_remote_country_not_worldwide(self):
        for loc in (
            "Remote - Argentina",
            "Remote - Colombia",
            "Remote - Costa Rica",
            "Remote - Mexico",
        ):
            with self.subTest(location=loc):
                parsed = parse_job_location(loc)
                self.assertFalse(parsed.is_worldwide)
                self.assertIsNotNone(parsed.allowed_country_codes)
                self.assertFalse(is_worldwide(loc))

    def test_worldwide_remote_still_passes(self):
        for loc in ("Worldwide Remote", "Remote - Worldwide", "Anywhere"):
            with self.subTest(location=loc):
                self.assertTrue(is_worldwide(loc))

    def test_bare_remote_is_worldwide(self):
        self.assertTrue(is_worldwide("Remote"))
        self.assertTrue(is_worldwide("Fully Remote"))

    def test_candidate_country_match(self):
        parsed = parse_job_location("Remote - Mexico")
        ok, _ = candidate_matches_location(parsed, "MX")
        self.assertTrue(ok)
        ok, reason = candidate_matches_location(parsed, "ET")
        self.assertFalse(ok)
        self.assertIn("Restricted", reason)

    def test_multi_country_remote(self):
        parsed = parse_job_location("Remote - Mexico, Colombia")
        self.assertEqual(parsed.allowed_country_codes, frozenset({"MX", "CO"}))
        self.assertTrue(candidate_matches_location(parsed, "MX")[0])
        self.assertFalse(candidate_matches_location(parsed, "ET")[0])

    def test_is_job_eligible_blocks_wrong_country(self):
        job = {
            "location": "Remote - Argentina",
            "description_full": "Backend engineer role.",
        }
        resume = {"basics": {"location": {"countryCode": "ET"}}}
        eligible, reason = is_job_eligible(job, resume)
        self.assertFalse(eligible)
        self.assertIn("Restricted", reason)

    def test_is_job_eligible_allows_matching_country(self):
        job = {"location": "Remote - Argentina", "description_full": ""}
        resume = {"basics": {"location": {"countryCode": "AR"}}}
        eligible, _ = is_job_eligible(job, resume)
        self.assertTrue(eligible)

    def test_normalize_job_drops_country_restricted(self):
        job = normalize_job(
            source="test",
            title="Backend Engineer",
            company="Acme",
            url="https://example.com/jobs/1",
            location="Remote - Colombia",
        )
        self.assertIsNone(job)

    def test_candidate_country_code(self):
        resume = {"basics": {"location": {"countryCode": "et"}}}
        self.assertEqual(candidate_country_code(resume), "ET")

    def test_remote_region_expands_to_country_codes(self):
        cases = {
            "Remote (North America)": {"US", "CA", "MX"},
            "Remote - EMEA": {"ET", "GB", "DE"},
            "Remote (APAC)": {"IN", "AU", "JP"},
        }
        for loc, expected_subset in cases.items():
            with self.subTest(location=loc):
                parsed = parse_job_location(loc)
                self.assertFalse(parsed.is_worldwide)
                self.assertTrue(expected_subset.issubset(parsed.allowed_country_codes))

    def test_ethiopian_candidate_matches_emea_remote(self):
        parsed = parse_job_location("Remote (EMEA)")
        ok, reason = candidate_matches_location(parsed, "ET")
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_ethiopian_candidate_does_not_match_apac_or_north_america(self):
        for loc in ("Remote (APAC)", "Remote (North America)", "Remote - Asia-Pacific"):
            with self.subTest(location=loc):
                parsed = parse_job_location(loc)
                ok, reason = candidate_matches_location(parsed, "ET")
                self.assertFalse(ok)
                self.assertIn("Restricted", reason)


if __name__ == "__main__":
    unittest.main()
