"""Tests for src.crawler.filters.normalize_url and its use in db.url_hash.

A job's identity in the catalog is url_hash(url). Two links that differ only
in a tracking param, a trailing slash, or scheme/host casing point at the
exact same posting and must hash the same — otherwise a re-crawled or
re-shared link for a job already in the catalog reads as a brand new job.
"""

from __future__ import annotations

import unittest

from src.crawler.filters import normalize_url
from src.db import url_hash


class NormalizeUrlTests(unittest.TestCase):
    def test_strips_tracking_params(self):
        base = normalize_url("https://boards.greenhouse.io/acme/jobs/123")
        tracked = normalize_url(
            "https://boards.greenhouse.io/acme/jobs/123?utm_source=linkedin&utm_campaign=q3"
        )
        self.assertEqual(base, tracked)

    def test_keeps_non_tracking_query_params(self):
        # Some ATS boards (Ashby, SmartRecruiters) encode real identity in
        # query params, not just the path — those must not be stripped.
        a = normalize_url("https://jobs.ashbyhq.com/acme/posting?gh_jid=999")
        b = normalize_url("https://jobs.ashbyhq.com/acme/posting?job=999")
        self.assertNotEqual(a, b)

    def test_gh_jid_is_not_stripped_on_a_company_owned_greenhouse_embed(self):
        """Regression: gh_jid was originally treated as a tracking param and
        stripped. On boards.greenhouse.io/<slug>/jobs/<id> the id is in the
        path, so that looked harmless — but a Greenhouse board embedded on
        a company's own domain (careers.acme.com/jobs?gh_jid=<id>) has no
        id in the path at all; gh_jid *is* the posting's identity there.
        Stripping it collapsed every one of that company's postings onto a
        single url_hash, silently overwriting all but the last one crawled.
        """
        a = normalize_url("https://careers.acme.com/jobs?gh_jid=111")
        b = normalize_url("https://careers.acme.com/jobs?gh_jid=222")
        self.assertNotEqual(a, b)

    def test_source_and_ref_are_not_stripped(self):
        """Plausible-looking tracking param names ("source", "ref") are
        real identifying query params on some boards — not stripped just
        because the name looks tracking-shaped."""
        a = normalize_url("https://example.com/jobs?source=123")
        b = normalize_url("https://example.com/jobs?source=456")
        self.assertNotEqual(a, b)

        c = normalize_url("https://example.com/jobs?ref=abc")
        d = normalize_url("https://example.com/jobs?ref=xyz")
        self.assertNotEqual(c, d)

    def test_strips_trailing_slash(self):
        self.assertEqual(
            normalize_url("https://example.com/jobs/123"),
            normalize_url("https://example.com/jobs/123/"),
        )

    def test_strips_fragment(self):
        self.assertEqual(
            normalize_url("https://example.com/jobs/123"),
            normalize_url("https://example.com/jobs/123#apply"),
        )

    def test_lowercases_scheme_and_host_but_not_path(self):
        self.assertEqual(
            normalize_url("HTTPS://Example.COM/jobs/AbC123"),
            "https://example.com/jobs/AbC123",
        )

    def test_distinct_job_paths_stay_distinct(self):
        a = normalize_url("https://boards.greenhouse.io/acme/jobs/123")
        b = normalize_url("https://boards.greenhouse.io/acme/jobs/456")
        self.assertNotEqual(a, b)

    def test_empty_url_is_empty(self):
        self.assertEqual(normalize_url(""), "")
        self.assertEqual(normalize_url("   "), "")


class UrlHashUsesNormalizationTests(unittest.TestCase):
    def test_url_hash_is_identical_across_cosmetic_variants(self):
        canonical = "https://boards.greenhouse.io/acme/jobs/123"
        variants = [
            "https://boards.greenhouse.io/acme/jobs/123/",
            "https://boards.greenhouse.io/acme/jobs/123?utm_source=twitter",
            "https://BOARDS.greenhouse.io/acme/jobs/123",
            "https://boards.greenhouse.io/acme/jobs/123#top",
        ]
        expected = url_hash(canonical)
        for variant in variants:
            self.assertEqual(url_hash(variant), expected, msg=variant)

    def test_url_hash_still_differs_for_a_different_job(self):
        self.assertNotEqual(
            url_hash("https://boards.greenhouse.io/acme/jobs/123"),
            url_hash("https://boards.greenhouse.io/acme/jobs/456"),
        )


if __name__ == "__main__":
    unittest.main()
