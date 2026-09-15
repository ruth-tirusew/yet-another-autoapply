"""HTTP-level tests for src/web/routes/targets.py."""

from __future__ import annotations

from tests.web_helpers import auth_client  # noqa: F401


def test_targets_page_renders_empty(auth_client):
    resp = auth_client.get("/targets")
    assert resp.status_code == 200


def test_add_target_detects_ats_and_shows_up_in_list(auth_client):
    resp = auth_client.post(
        "/targets/add",
        data={
            "company_slug": "Acme",
            "ats_type": "",
            "tier": "2",
            "display_name": "Acme Corp",
            "careers_url": "https://boards.greenhouse.io/acme",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303

    listing = auth_client.get("/targets")
    assert "acme" in listing.text.lower()


def test_toggle_and_delete_target(auth_client):
    from src.catalog_db import list_target_companies

    auth_client.post(
        "/targets/add",
        data={"company_slug": "acme", "ats_type": "greenhouse", "tier": "2"},
    )
    company_id = list_target_companies(1)[0]["id"]

    resp = auth_client.post(f"/targets/{company_id}/toggle", data={"enabled": "0"}, follow_redirects=False)
    assert resp.status_code == 303

    resp = auth_client.post(f"/targets/{company_id}/delete", follow_redirects=False)
    assert resp.status_code == 303
    assert list_target_companies(1) == []
