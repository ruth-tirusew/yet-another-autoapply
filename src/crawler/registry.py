"""Adapter registry and factory."""

from __future__ import annotations

from typing import Any

from src.crawler.adapters.ashby import AshbyAdapter
from src.crawler.adapters.base import BaseAdapter
from src.crawler.adapters.ever_jobs import EverJobsAdapter
from src.crawler.adapters.greenhouse import GreenhouseAdapter
from src.crawler.adapters.job_board_aggregator import JobBoardAggregatorAdapter
from src.crawler.adapters.json_api import JsonApiAdapter
from src.crawler.adapters.legacy import LegacyAdapter
from src.crawler.adapters.lever import LeverAdapter
from src.crawler.adapters.rss import RssAdapter
from src.crawler.adapters.smartrecruiters import SmartRecruitersAdapter
from src.crawler.adapters.target_companies import TargetCompaniesAdapter
from src.crawler.adapters.workable import WorkableAdapter

ADAPTERS: dict[str, type[BaseAdapter]] = {
    "greenhouse": GreenhouseAdapter,
    "lever": LeverAdapter,
    "ashby": AshbyAdapter,
    "workable": WorkableAdapter,
    "smartrecruiters": SmartRecruitersAdapter,
    "rss": RssAdapter,
    "json_api": JsonApiAdapter,
    "legacy": LegacyAdapter,
    "ever_jobs": EverJobsAdapter,
    "job_board_aggregator": JobBoardAggregatorAdapter,
    "target_companies": TargetCompaniesAdapter,
}


def get_adapter(source: dict[str, Any]) -> BaseAdapter:
    adapter_type = source.get("adapter")
    if not adapter_type:
        raise ValueError(f"Source {source.get('id')} missing adapter type")
    cls = ADAPTERS.get(adapter_type)
    if not cls:
        raise ValueError(f"Unknown adapter type: {adapter_type}")
    return cls(source)
