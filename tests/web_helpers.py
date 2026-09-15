"""Shared pytest fixtures for HTTP-level web route tests (src/web/routes/*)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def csrf(client: TestClient, path: str) -> str:
    resp = client.get(path)
    start = resp.text.find('name="csrf_token" value="')
    assert start != -1, f"csrf token not found on {path}"
    start += len('name="csrf_token" value="')
    end = resp.text.find('"', start)
    return resp.text[start:end]


@pytest.fixture()
def auth_client(tmp_path, monkeypatch):
    """A TestClient logged in as a fresh user, backed by an isolated temp DB."""
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("SESSION_SECRET", "test-secret-key-for-tests-only")
    monkeypatch.setattr("src.settings.DB_PATH", db_path)
    monkeypatch.setattr("src.settings.DATA_DIR", tmp_path)
    monkeypatch.setattr("src.settings.PROFILE_DIR", tmp_path / "profile")
    monkeypatch.setattr("src.settings.ROOT", Path(__file__).resolve().parent.parent)
    # These are process-global caches keyed by tenant id, not by DB path — a
    # value cached by an earlier test would otherwise leak into this one.
    monkeypatch.setattr("src.settings._config_cache", {})
    monkeypatch.setattr("src.settings._sources_cache", {})

    from src.db import init_db
    from src.web.app import app

    init_db()
    with TestClient(app) as client:
        reg = client.post(
            "/register",
            data={
                "email": "webtest@example.com",
                "password": "password123",
                "password_confirm": "password123",
                "display_name": "Web Test",
                "csrf_token": csrf(client, "/register"),
            },
            follow_redirects=False,
        )
        assert reg.status_code == 303, reg.text
        yield client
