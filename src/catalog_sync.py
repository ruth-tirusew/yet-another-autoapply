"""Sync shared catalog listings into per-user job queues."""

from __future__ import annotations

from src.catalog_db import sync_catalog_to_user


def sync_user_jobs(user_id: int | None = None, limit: int = 500) -> int:
    return sync_catalog_to_user(user_id=user_id, limit=limit)
