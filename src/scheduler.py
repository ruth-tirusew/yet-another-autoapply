"""Optional APScheduler daemon for periodic platform + per-user pipeline runs."""

from __future__ import annotations

from apscheduler.schedulers.blocking import BlockingScheduler

from src.pipeline import run_platform_stages, run_user_stages
from src.settings import get_config
from src.tenant import set_tenant_user_id


def start_scheduler() -> None:
    cfg = get_config()
    sched_cfg = cfg.get("scheduler", {})
    hours = sched_cfg.get("crawl_interval_hours", 8)

    def _job() -> None:
        run_platform_stages(crawl=True, enrich=True, embed=True)
        set_tenant_user_id(1)
        run_user_stages(sync=True, prefilter=True, match=True, generate=False, export=False, user_id=1)

    scheduler = BlockingScheduler()
    scheduler.add_job(_job, "interval", hours=hours, id="pipeline")
    print(f"Scheduler started — platform crawl + user sync every {hours}h. Ctrl+C to stop.")
    scheduler.start()


if __name__ == "__main__":
    start_scheduler()
