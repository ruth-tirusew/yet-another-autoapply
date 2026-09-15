"""Tests for job-board-aggregator adapter."""

import gzip
import json
import unittest
from unittest.mock import MagicMock, patch

from src.crawler.adapters.job_board_aggregator import (
    JobBoardAggregatorAdapter,
    _chunk_window,
)
from src.db import url_hash


SAMPLE_JOBS = [
    {
        "company": "acme",
        "title": "Senior Backend Engineer",
        "skill_level": "senior",
        "location": "Remote - Worldwide",
        "remote": True,
        "url": "https://boards.greenhouse.io/acme/jobs/123",
        "scraped_at": "2026-06-15T00:00:00Z",
        "ats": "Greenhouse",
        "salary": None,
        "is_recruiter": False,
    },
    {
        "company": "latamco",
        "title": "Senior Backend Engineer",
        "skill_level": "senior",
        "location": "Remote - Argentina",
        "remote": True,
        "url": "https://boards.greenhouse.io/latamco/jobs/456",
        "scraped_at": "2026-06-15T00:00:00Z",
        "ats": "Greenhouse",
        "salary": None,
        "is_recruiter": False,
    },
    {
        "title": "Senior Python Developer",
        "location": "Remote",
        "remote": True,
        "url": "https://jobs.lever.co/agency/abc",
        "scraped_at": "2026-06-15T00:00:00Z",
        "ats": "Lever",
        "is_recruiter": True,
    },
    {
        "company": "localco",
        "title": "Senior Software Engineer",
        "location": "London",
        "remote": False,
        "url": "https://jobs.lever.co/localco/def",
        "scraped_at": "2026-06-15T00:00:00Z",
        "ats": "Lever",
        "is_recruiter": False,
    },
]


class JobBoardAggregatorTests(unittest.TestCase):
    def test_chunk_window_rotates_without_dropping(self):
        chunks = [f"jobs_chunk_{i}.json.gz" for i in range(10)]
        selected = _chunk_window(chunks, 3, rotate=True)
        self.assertEqual(len(selected), 3)
        self.assertEqual(len(set(selected)), 3)

    def test_fetch_filters_remote_recruiters_and_maps_jobs(self):
        source = {
            "id": "job_board_aggregator",
            "name": "Job Board Aggregator",
            "adapter": "job_board_aggregator",
            "enabled": True,
            "max_chunks": 1,
            "max_jobs": 10,
        }
        adapter = JobBoardAggregatorAdapter(source)
        payload = gzip.compress(json.dumps(SAMPLE_JOBS).encode())

        manifest_resp = MagicMock()
        manifest_resp.json.return_value = {"chunks": ["jobs_chunk_0.json.gz"]}
        chunk_resp = MagicMock()
        chunk_resp.content = payload

        with patch("src.crawler.adapters.job_board_aggregator.get") as mock_get, patch(
            "src.crawler.adapters.job_board_aggregator.get_existing_url_hashes",
            return_value=set(),
        ):
            mock_get.side_effect = [manifest_resp, chunk_resp]
            jobs = adapter.fetch()

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["company"], "Acme")
        self.assertEqual(jobs[0]["url"], "https://boards.greenhouse.io/acme/jobs/123")
        self.assertIn("Greenhouse", jobs[0]["tags"])

    def test_fetch_preserves_country_restricted_location_and_filters_it(self):
        source = {
            "id": "job_board_aggregator",
            "name": "Job Board Aggregator",
            "adapter": "job_board_aggregator",
            "enabled": True,
            "max_chunks": 1,
            "max_jobs": 10,
        }
        adapter = JobBoardAggregatorAdapter(source)
        payload = gzip.compress(json.dumps(SAMPLE_JOBS).encode())

        manifest_resp = MagicMock()
        manifest_resp.json.return_value = {"chunks": ["jobs_chunk_0.json.gz"]}
        chunk_resp = MagicMock()
        chunk_resp.content = payload

        with patch("src.crawler.adapters.job_board_aggregator.get") as mock_get, patch(
            "src.crawler.adapters.job_board_aggregator.get_existing_url_hashes",
            return_value=set(),
        ):
            mock_get.side_effect = [manifest_resp, chunk_resp]
            jobs = adapter.fetch()

        urls = {job["url"] for job in jobs}
        self.assertNotIn("https://boards.greenhouse.io/latamco/jobs/456", urls)

    def test_fetch_skips_jobs_already_in_db(self):
        source = {
            "id": "job_board_aggregator",
            "name": "Job Board Aggregator",
            "adapter": "job_board_aggregator",
            "enabled": True,
            "max_chunks": 1,
            "max_jobs": 10,
        }
        adapter = JobBoardAggregatorAdapter(source)
        payload = gzip.compress(json.dumps(SAMPLE_JOBS).encode())
        existing = {url_hash(SAMPLE_JOBS[0]["url"])}

        manifest_resp = MagicMock()
        manifest_resp.json.return_value = {"chunks": ["jobs_chunk_0.json.gz"]}
        chunk_resp = MagicMock()
        chunk_resp.content = payload

        with patch("src.crawler.adapters.job_board_aggregator.get") as mock_get, patch(
            "src.crawler.adapters.job_board_aggregator.get_existing_url_hashes",
            return_value=existing,
        ):
            mock_get.side_effect = [manifest_resp, chunk_resp]
            jobs = adapter.fetch()

        self.assertEqual(jobs, [])


if __name__ == "__main__":
    unittest.main()
