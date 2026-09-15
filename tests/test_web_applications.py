"""HTTP-level tests for src/web/routes/applications.py."""

from __future__ import annotations

from tests.web_helpers import auth_client  # noqa: F401


def test_applications_page_defaults_to_active_tab(auth_client):
    resp = auth_client.get("/applications")
    assert resp.status_code == 200


def test_hx_request_returns_partial_fragment_only(auth_client):
    resp = auth_client.get("/applications", headers={"HX-Request": "true"})
    assert resp.status_code == 200
    assert "<html" not in resp.text.lower()


def test_retry_failed_with_no_failed_jobs_is_a_noop_redirect(auth_client):
    resp = auth_client.post("/applications/retry-failed", follow_redirects=False)
    assert resp.status_code == 303
    assert "Retried%200" in resp.headers["location"]


def test_retry_one_on_non_failed_job(auth_client):
    from src.db import upsert_job

    upsert_job({"url": "https://example.com/job/1", "title": "Backend Engineer"}, user_id=1)
    resp = auth_client.post("/applications/1/retry", follow_redirects=False)
    assert resp.status_code == 303
    assert "retry_summary=" in resp.headers["location"]
