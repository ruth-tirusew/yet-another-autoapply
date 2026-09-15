"""Tests for src/scheduler.py (APScheduler-driven periodic pipeline runs)."""

from __future__ import annotations

import unittest
from unittest import mock


class SchedulerTests(unittest.TestCase):
    def test_registers_interval_job_with_configured_hours(self):
        from src import scheduler

        with mock.patch(
            "src.scheduler.get_config", return_value={"scheduler": {"crawl_interval_hours": 4}}
        ), mock.patch("src.scheduler.BlockingScheduler") as mocked_cls:
            mocked_instance = mocked_cls.return_value
            scheduler.start_scheduler()

        mocked_instance.add_job.assert_called_once()
        args, kwargs = mocked_instance.add_job.call_args
        self.assertEqual(args[1], "interval")
        self.assertEqual(kwargs["hours"], 4)
        self.assertEqual(kwargs["id"], "pipeline")
        mocked_instance.start.assert_called_once()

    def test_defaults_to_eight_hours_when_unconfigured(self):
        from src import scheduler

        with mock.patch("src.scheduler.get_config", return_value={}), mock.patch(
            "src.scheduler.BlockingScheduler"
        ) as mocked_cls:
            mocked_instance = mocked_cls.return_value
            scheduler.start_scheduler()

        _, kwargs = mocked_instance.add_job.call_args
        self.assertEqual(kwargs["hours"], 8)

    def test_job_closure_runs_platform_then_user_stages(self):
        from src import scheduler

        with mock.patch(
            "src.scheduler.get_config", return_value={"scheduler": {}}
        ), mock.patch("src.scheduler.BlockingScheduler") as mocked_cls, mock.patch(
            "src.scheduler.run_platform_stages"
        ) as mocked_platform, mock.patch(
            "src.scheduler.run_user_stages"
        ) as mocked_user, mock.patch(
            "src.scheduler.set_tenant_user_id"
        ) as mocked_set_tenant:
            mocked_instance = mocked_cls.return_value
            scheduler.start_scheduler()
            job_fn = mocked_instance.add_job.call_args[0][0]
            job_fn()

        mocked_platform.assert_called_once_with(crawl=True, enrich=True, embed=True)
        mocked_set_tenant.assert_called_once_with(1)
        mocked_user.assert_called_once_with(
            sync=True, prefilter=True, match=True, generate=False, export=False, user_id=1
        )


if __name__ == "__main__":
    unittest.main()
