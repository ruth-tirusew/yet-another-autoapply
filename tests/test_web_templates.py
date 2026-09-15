"""HTTP-level tests for src/web/routes/templates.py (CV Template Studio)."""

from __future__ import annotations

from tests.web_helpers import auth_client  # noqa: F401


def test_gallery_auto_provisions_builtin_templates(auth_client):
    resp = auth_client.get("/templates")
    assert resp.status_code == 200
    assert "Classic" in resp.text


def test_editor_renders_for_builtin_template(auth_client):
    auth_client.get("/templates")  # provisions builtins
    resp = auth_client.get("/templates/classic/edit")
    assert resp.status_code == 200


def test_editor_404_for_unknown_slug(auth_client):
    resp = auth_client.get("/templates/does-not-exist/edit")
    assert resp.status_code == 404


def test_preview_renders_sample_resume(auth_client):
    auth_client.get("/templates")
    resp = auth_client.get("/templates/classic/preview")
    assert resp.status_code == 200
    assert len(resp.text) > 0


def test_builtin_template_cannot_be_deleted(auth_client):
    auth_client.get("/templates")
    resp = auth_client.post("/templates/classic/delete")
    assert resp.status_code == 400


def test_builtin_template_cannot_be_saved_directly(auth_client):
    auth_client.get("/templates")
    resp = auth_client.post("/templates/classic/save", data={"name": "Classic"})
    assert resp.status_code == 400


def test_duplicate_creates_editable_copy(auth_client):
    auth_client.get("/templates")
    resp = auth_client.post(
        "/templates/classic/duplicate", data={"name": "My Copy"}, follow_redirects=False
    )
    assert resp.status_code == 303
    assert "/edit" in resp.headers["location"]
