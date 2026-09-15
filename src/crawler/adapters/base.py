"""Base adapter interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

# Keys allowed at source root in sources.yaml (merged into config)
ROOT_CONFIG_KEYS = {
    "scraper", "scrapers", "company_slug", "display_name", "feed_url",
    "url", "params", "params_list", "items_path", "source_label",
    "search_term", "is_remote", "results_wanted", "site_type", "companies",
    "field_map", "skip_first", "filter_relevant", "is_list_root",
    "base_url", "remote_only", "exclude_recruiters", "max_chunks", "max_jobs",
    "chunk_rotation", "skip_existing",
}


class BaseAdapter(ABC):
    def __init__(self, source: dict[str, Any]):
        self.source = source
        self.source_id = source.get("id", "unknown")
        self.name = source.get("name", self.source_id)
        self.config = dict(source.get("config", {}))
        for key in ROOT_CONFIG_KEYS:
            if key in source and key not in self.config:
                self.config[key] = source[key]

    @abstractmethod
    def fetch(self) -> list[dict]:
        """Return normalized job dicts."""
        ...
