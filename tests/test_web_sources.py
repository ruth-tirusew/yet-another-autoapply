"""HTTP-level tests for src/web/routes/sources.py (network calls mocked out)."""

from __future__ import annotations

from unittest import mock

from tests.web_helpers import auth_client  # noqa: F401


def test_sources_page_renders_without_hitting_the_network(auth_client):
    with mock.patch("src.web.routes.sources.fetch_awesome_list", return_value=[]) as mocked:
        resp = auth_client.get("/sources")
    assert resp.status_code == 200
    mocked.assert_called_once()


def test_toggle_unknown_source_returns_404(auth_client):
    with mock.patch("src.web.routes.sources.fetch_awesome_list", return_value=[]):
        resp = auth_client.post("/sources/toggle", data={"source_id": "does-not-exist", "enabled": "true"})
    assert resp.status_code == 404
    assert "Source not found" in resp.text


def test_toggle_known_source_returns_row_fragment(auth_client):
    from src.web.services.config_service import get_sources_list

    with mock.patch("src.web.routes.sources.fetch_awesome_list", return_value=[]):
        sources = get_sources_list()
        if not sources:
            return  # no built-in static sources configured in this environment
        source_id = sources[0]["id"]
        resp = auth_client.post("/sources/toggle", data={"source_id": source_id, "enabled": "true"})

    assert resp.status_code == 200
    assert "<tr" in resp.text
