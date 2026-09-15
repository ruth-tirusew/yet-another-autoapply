"""HTTP-level tests for src/web/routes/partials.py."""

from __future__ import annotations

from unittest import mock

from tests.web_helpers import auth_client  # noqa: F401


def test_profile_eval_status_partial_renders(auth_client):
    resp = auth_client.get("/partials/profile-eval-status")
    assert resp.status_code == 200


def test_model_status_partial_renders_without_live_provider_call(auth_client):
    with mock.patch(
        "src.web.routes.partials.check_provider_health",
        return_value={"provider": "ollama", "reachable": False, "models": []},
    ):
        resp = auth_client.get("/partials/model-status")
    assert resp.status_code == 200
