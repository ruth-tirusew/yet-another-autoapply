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


def test_pin_promotes_auto_company_to_manual(auth_client):
    from src.catalog_db import add_target_company, list_target_companies

    add_target_company(1, "acme", "greenhouse", origin="auto")
    company_id = list_target_companies(1)[0]["id"]

    resp = auth_client.post(f"/targets/{company_id}/pin", follow_redirects=False)
    assert resp.status_code == 303
    assert list_target_companies(1)[0]["origin"] == "manual"


def test_deleting_an_auto_company_blocklists_it(auth_client):
    from src.catalog_db import add_target_company, list_target_companies, sync_target_company_watchlist
    from src.db import connect

    add_target_company(1, "acme", "greenhouse", origin="auto")
    company_id = list_target_companies(1)[0]["id"]

    resp = auth_client.post(f"/targets/{company_id}/delete", follow_redirects=False)
    assert resp.status_code == 303
    assert list_target_companies(1) == []

    with connect() as conn:
        cat_id = conn.execute(
            "INSERT INTO catalog_jobs (url_hash, url, title, company, company_slug, ats_type, "
            "source_tier, status) VALUES ('h1', 'https://x/1', 'E', 'Acme', 'acme', 'greenhouse', 'native', 'active')"
        ).lastrowid
        conn.execute(
            "INSERT INTO user_jobs (user_id, catalog_job_id, status) VALUES (1, ?, 'applied')",
            (cat_id,),
        )

    result = sync_target_company_watchlist(1, threshold=70)
    assert result["added"] == 0, "a removed auto company must not be re-added"


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
