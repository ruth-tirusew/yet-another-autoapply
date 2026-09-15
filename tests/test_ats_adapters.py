"""Tests for ATS detection and adapter mapping."""

import unittest
from unittest.mock import MagicMock, patch

from src.crawler.ats_detect import detect_ats_type
from src.crawler.adapters.ashby import AshbyAdapter
from src.crawler.adapters.lever import LeverAdapter
from src.crawler.adapters.smartrecruiters import SmartRecruitersAdapter


class AtsDetectTests(unittest.TestCase):
    def test_detect_greenhouse(self):
        self.assertEqual(detect_ats_type("https://boards.greenhouse.io/acme/jobs/1"), "greenhouse")

    def test_detect_ashby(self):
        self.assertEqual(detect_ats_type("https://jobs.ashbyhq.com/acme/abc"), "ashby")

    def test_detect_lever(self):
        self.assertEqual(detect_ats_type("https://jobs.lever.co/acme/role"), "lever")


class AshbyAdapterTests(unittest.TestCase):
    @patch("src.crawler.adapters.ashby.get")
    def test_fetch_parses_jobs(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "jobs": [
                    {
                        "title": "Senior Python Engineer",
                        "location": "Remote",
                        "isRemote": True,
                        "jobUrl": "https://jobs.ashbyhq.com/acme/1",
                        "descriptionPlain": "Build APIs",
                        "id": "1",
                    }
                ]
            },
        )
        adapter = AshbyAdapter({"company_slug": "acme", "display_name": "Acme"})
        jobs = adapter.fetch()
        self.assertEqual(len(jobs), 1)
        self.assertIn("Python", jobs[0]["title"])


def _sr_posting(n: int) -> dict:
    return {
        "name": f"Senior Python Engineer {n}",
        "location": {"fullLocation": "Remote"},
        "remote": True,
        "refNumber": str(n),
        "releasedDate": "2026-01-01",
    }


class SmartRecruitersAdapterTests(unittest.TestCase):
    @patch("src.crawler.adapters.smartrecruiters.get")
    def test_single_page_stops_after_one_request(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"content": [_sr_posting(1)], "totalFound": 1},
        )
        adapter = SmartRecruitersAdapter({"company_slug": "acme", "display_name": "Acme"})
        jobs = adapter.fetch()
        self.assertEqual(len(jobs), 1)
        mock_get.assert_called_once()

    @patch("src.crawler.adapters.smartrecruiters.time.sleep")
    @patch("src.crawler.adapters.smartrecruiters.get")
    def test_paginates_past_the_first_page_instead_of_truncating(self, mock_get, mock_sleep):
        from src.crawler.adapters.smartrecruiters import PAGE_SIZE

        page1 = MagicMock(
            status_code=200,
            json=lambda: {
                "content": [_sr_posting(n) for n in range(PAGE_SIZE)],
                "totalFound": PAGE_SIZE + 5,
            },
        )
        page2 = MagicMock(
            status_code=200,
            json=lambda: {
                "content": [_sr_posting(n) for n in range(PAGE_SIZE, PAGE_SIZE + 5)],
                "totalFound": PAGE_SIZE + 5,
            },
        )
        mock_get.side_effect = [page1, page2]

        adapter = SmartRecruitersAdapter({"company_slug": "acme", "display_name": "Acme"})
        jobs = adapter.fetch()

        self.assertEqual(len(jobs), PAGE_SIZE + 5)
        self.assertEqual(mock_get.call_count, 2)
        first_call_params = mock_get.call_args_list[0].kwargs["params"]
        second_call_params = mock_get.call_args_list[1].kwargs["params"]
        self.assertEqual(first_call_params, {"limit": PAGE_SIZE, "offset": 0})
        self.assertEqual(second_call_params, {"limit": PAGE_SIZE, "offset": PAGE_SIZE})

    @patch("src.crawler.adapters.smartrecruiters.time.sleep")
    @patch("src.crawler.adapters.smartrecruiters.get")
    def test_stops_at_max_pages_even_without_total_found(self, mock_get, mock_sleep):
        from src.crawler.adapters.smartrecruiters import MAX_PAGES, PAGE_SIZE

        # Every page is "full" and the API never reports totalFound — without a
        # cap this would loop forever.
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"content": [_sr_posting(n) for n in range(PAGE_SIZE)]},
        )
        adapter = SmartRecruitersAdapter({"company_slug": "acme", "display_name": "Acme"})
        adapter.fetch()
        self.assertEqual(mock_get.call_count, MAX_PAGES)


if __name__ == "__main__":
    unittest.main()
