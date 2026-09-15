"""Tests for applicant URL normalization and resume merge."""

import unittest

from src.applicant import merge_applicant_into_resume, normalize_url


class NormalizeUrlTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(normalize_url(""), "")
        self.assertEqual(normalize_url(None), "")

    def test_adds_https_scheme(self):
        self.assertEqual(normalize_url("linkedin.com/in/jane"), "https://linkedin.com/in/jane")

    def test_preserves_existing_scheme(self):
        self.assertEqual(normalize_url("https://github.com/jane"), "https://github.com/jane")

    def test_rejects_invalid(self):
        self.assertEqual(normalize_url("not a url"), "")


class MergeApplicantTests(unittest.TestCase):
    def setUp(self):
        self.resume = {
            "basics": {
                "name": "Parsed Name",
                "email": "parsed@example.com",
                "url": "https://old.dev",
                "profiles": [{"network": "GitHub", "url": "https://github.com/old"}],
            }
        }
        self.applicant = {
            "name": "Jane Doe",
            "email": "jane@example.com",
            "phone": "+1 555 0100",
            "portfolio": "jane.dev",
            "linkedin": "https://linkedin.com/in/jane",
            "github": "github.com/jane",
            "location": "Remote",
        }

    def test_empty_applicant_leaves_resume_unchanged(self):
        merged = merge_applicant_into_resume(self.resume, {})
        self.assertEqual(merged["basics"]["name"], "Parsed Name")
        self.assertEqual(merged["basics"]["url"], "https://old.dev")

    def test_applicant_overrides_contact_and_links(self):
        merged = merge_applicant_into_resume(self.resume, self.applicant)
        basics = merged["basics"]
        self.assertEqual(basics["name"], "Jane Doe")
        self.assertEqual(basics["email"], "jane@example.com")
        self.assertEqual(basics["phone"], "+1 555 0100")
        self.assertEqual(basics["url"], "https://jane.dev")
        self.assertEqual(basics["location"], {"address": "Remote"})
        networks = {p["network"]: p["url"] for p in basics["profiles"]}
        self.assertEqual(networks["LinkedIn"], "https://linkedin.com/in/jane")
        self.assertEqual(networks["GitHub"], "https://github.com/jane")


if __name__ == "__main__":
    unittest.main()
