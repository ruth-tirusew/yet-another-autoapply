"""HTTP-level tests for src/web/routes/dashboard.py."""

from __future__ import annotations

from tests.web_helpers import auth_client  # noqa: F401


def test_dashboard_renders_on_empty_db(auth_client):
    resp = auth_client.get("/")
    assert resp.status_code == 200
    assert "Dashboard" in resp.text


def test_dashboard_shows_queued_job(auth_client):
    from src.db import connect, upsert_job

    upsert_job({"url": "https://example.com/job/1", "title": "Queued Engineer"}, user_id=1)
    with connect() as conn:
        conn.execute("UPDATE user_jobs SET status='queued' WHERE user_id=1")

    resp = auth_client.get("/")
    assert resp.status_code == 200
    assert "Queued Engineer" in resp.text


def test_dashboard_redirects_when_not_logged_in(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.setattr("src.settings.DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr("src.settings.DATA_DIR", tmp_path)
    monkeypatch.setattr("src.settings._config_cache", {})
    monkeypatch.setattr("src.settings._sources_cache", {})

    from src.db import init_db
    from src.web.app import app

    init_db()
    with TestClient(app) as client:
        resp = client.get("/", follow_redirects=False)

    assert resp.status_code == 303
    assert "/login" in resp.headers["location"]
