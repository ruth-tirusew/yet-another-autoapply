"""HTTP-level tests for src/web/routes/coaching.py (no-profile guard paths)."""

from __future__ import annotations

from tests.web_helpers import auth_client  # noqa: F401


def test_coaching_page_renders_with_no_data_and_no_llm_call(auth_client):
    resp = auth_client.get("/coaching")
    assert resp.status_code == 200


def test_profile_guide_generate_without_profile_short_circuits(auth_client):
    resp = auth_client.post("/coaching/profile-guide/generate")
    assert resp.status_code == 200


def test_cv_preview_generate_without_profile_reports_error(auth_client):
    resp = auth_client.post("/coaching/cv-preview/generate")
    assert resp.status_code == 200
    assert "no profile" in resp.text.lower() or "profile" in resp.text.lower()


def test_cv_preview_html_without_draft_shows_placeholder(auth_client):
    resp = auth_client.get("/coaching/cv-preview/html")
    assert resp.status_code == 200


def test_cv_preview_apply_without_draft_reports_error(auth_client):
    resp = auth_client.post("/coaching/cv-preview/apply")
    assert resp.status_code == 200
