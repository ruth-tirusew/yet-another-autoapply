"""Tests for src/source_discovery.py (awesome-job-boards probing + merge)."""

from __future__ import annotations

import unittest
from unittest import mock

from src.source_discovery import classify_url, merge_discovered_sources


class ClassifyUrlTests(unittest.TestCase):
    def test_greenhouse_url_extracts_slug(self):
        result = classify_url("https://boards.greenhouse.io/acme")
        self.assertEqual(result, {"adapter": "greenhouse", "company_slug": "acme", "enabled": False})

    def test_lever_url(self):
        result = classify_url("https://jobs.lever.co/acme")
        self.assertEqual(result["adapter"], "lever")

    def test_rss_url(self):
        result = classify_url("https://example.com/feed.rss")
        self.assertEqual(result["adapter"], "rss")

    def test_unrelated_url_falls_back_to_manual_entry(self):
        result = classify_url("https://example.com/careers")
        self.assertEqual(result["adapter"], "json_api")
        self.assertIn("note", result)


class MergeDiscoveredSourcesTests(unittest.TestCase):
    def test_returns_static_unchanged_when_auto_enable_off(self):
        with mock.patch(
            "src.source_discovery.get_config",
            return_value={"source_discovery": {"auto_enable_discovered": False}},
        ), mock.patch("src.source_discovery.fetch_awesome_list") as mocked_fetch:
            static = [{"id": "existing"}]
            result = merge_discovered_sources(static)

        self.assertEqual(result, static)
        mocked_fetch.assert_not_called()

    def test_merges_crawlable_greenhouse_item_and_dedups(self):
        discovered = [
            {
                "id": "acme",
                "name": "Acme",
                "adapter": "greenhouse",
                "company_slug": "acme",
                "seed_url": "https://boards.greenhouse.io/acme",
            },
            {
                "id": "not-crawlable",
                "name": "NoSlug",
                "adapter": "greenhouse",
                "company_slug": "",
                "seed_url": "https://boards.greenhouse.io/noslug",
            },
        ]
        with mock.patch(
            "src.source_discovery.get_config",
            return_value={
                "source_discovery": {"auto_enable_discovered": True, "max_discovered": 10}
            },
        ), mock.patch("src.source_discovery.fetch_awesome_list", return_value=discovered):
            result = merge_discovered_sources([])

        ids = [s["id"] for s in result]
        self.assertEqual(ids, ["discovered_acme"])

    def test_respects_max_discovered_cap(self):
        discovered = [
            {
                "id": f"c{i}",
                "name": f"Company {i}",
                "adapter": "greenhouse",
                "company_slug": f"c{i}",
                "seed_url": f"https://boards.greenhouse.io/c{i}",
            }
            for i in range(5)
        ]
        with mock.patch(
            "src.source_discovery.get_config",
            return_value={
                "source_discovery": {"auto_enable_discovered": True, "max_discovered": 2}
            },
        ), mock.patch("src.source_discovery.fetch_awesome_list", return_value=discovered):
            result = merge_discovered_sources([])

        self.assertEqual(len(result), 2)

    def test_skips_items_already_present_by_slug(self):
        static = [{"id": "existing", "company_slug": "acme"}]
        discovered = [
            {
                "id": "acme-dup",
                "name": "Acme",
                "adapter": "greenhouse",
                "company_slug": "acme",
                "seed_url": "https://boards.greenhouse.io/acme",
            }
        ]
        with mock.patch(
            "src.source_discovery.get_config",
            return_value={
                "source_discovery": {"auto_enable_discovered": True, "max_discovered": 10}
            },
        ), mock.patch("src.source_discovery.fetch_awesome_list", return_value=discovered):
            result = merge_discovered_sources(static)

        self.assertEqual(result, static)


class FetchAwesomeListTests(unittest.TestCase):
    def test_only_relevant_sections_and_skip_fragments(self):
        markdown = """\
## Remote
* [Acme](https://boards.greenhouse.io/acme)
* [Skip Me](https://github.com/tramcar/awesome-job-boards)

## Irrelevant Section
* [Nope](https://example.com/nope)
"""
        fake_response = mock.Mock(text=markdown)
        with mock.patch(
            "src.source_discovery.get_config", return_value={"source_discovery": {}}
        ), mock.patch("src.source_discovery.requests.get", return_value=fake_response):
            from src.source_discovery import fetch_awesome_list

            results = fetch_awesome_list()

        names = [r["name"] for r in results]
        self.assertEqual(names, ["Acme"])

    def test_network_failure_returns_empty_list(self):
        with mock.patch(
            "src.source_discovery.get_config", return_value={"source_discovery": {}}
        ), mock.patch("src.source_discovery.requests.get", side_effect=ConnectionError("boom")):
            from src.source_discovery import fetch_awesome_list

            self.assertEqual(fetch_awesome_list(), [])


if __name__ == "__main__":
    unittest.main()
