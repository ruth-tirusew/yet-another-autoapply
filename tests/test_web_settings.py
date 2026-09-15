"""HTTP-level tests for src/web/routes/settings.py."""

from __future__ import annotations

from unittest import mock

from tests.web_helpers import auth_client, csrf  # noqa: F401

SAFE_TABS = [
    "account",
    "pipeline",
    "hiring_agent",
    "keywords",
    "auto_apply",
    "applicant",
    "cover_letter",
    "integrations",
]


def test_settings_index_renders(auth_client):
    resp = auth_client.get("/settings")
    assert resp.status_code == 200


def test_all_safe_panels_render(auth_client):
    for tab in SAFE_TABS:
        resp = auth_client.get(f"/settings/panel/{tab}")
        assert resp.status_code == 200, f"panel {tab} failed: {resp.text[:200]}"


def test_models_panel_renders_without_live_provider_call(auth_client):
    with mock.patch("src.web.routes.settings.list_models", return_value=[]):
        resp = auth_client.get("/settings/panel/models")
    assert resp.status_code == 200


def test_unknown_panel_returns_404(auth_client):
    resp = auth_client.get("/settings/panel/bogus")
    assert resp.status_code == 404
    assert "Unknown tab" in resp.text


def test_save_pipeline_round_trips_match_threshold(auth_client):
    resp = auth_client.post(
        "/settings/save/pipeline",
        data={
            "match_threshold": "42",
            "role_fit_min": "10",
            "maybe_score_boost": "5",
            "stale_days": "20",
            "enrich_delay_seconds": "1.0",
            "crawl_delay_seconds": "1.5",
            "enrich_limit": "25",
            "match_limit": "10",
            "generate_limit": "5",
        },
    )
    assert resp.status_code == 200
    assert resp.headers.get("hx-trigger") == "configSaved"

    panel = auth_client.get("/settings/panel/pipeline")
    assert "42" in panel.text


def test_save_imap_test_without_credentials_is_a_guard_path(auth_client):
    resp = auth_client.post("/settings/test-imap", data={})
    assert resp.status_code == 200
    assert "required" in resp.text.lower()
