"""HTTP-level tests for src/web/routes/pipeline.py."""

from __future__ import annotations

from unittest import mock

from tests.web_helpers import auth_client  # noqa: F401


def test_pipeline_page_renders_on_empty_db(auth_client):
    resp = auth_client.get("/pipeline")
    assert resp.status_code == 200


def test_pipeline_status_partial_renders(auth_client):
    resp = auth_client.get("/partials/pipeline-status")
    assert resp.status_code == 200


def test_pipeline_run_returns_409_when_already_busy(auth_client):
    with mock.patch(
        "src.web.routes.pipeline.start_run", side_effect=RuntimeError("pipeline already running")
    ):
        resp = auth_client.post("/pipeline/run", data={"stages": "crawl"})
    assert resp.status_code == 409
    assert "already running" in resp.text


def test_pipeline_run_starts_with_requested_stages(auth_client):
    with mock.patch("src.web.routes.pipeline.start_run") as mocked_start:
        resp = auth_client.post("/pipeline/run", data={"stages": "crawl,match"})
    assert resp.status_code == 200
    args, kwargs = mocked_start.call_args
    assert args[0] == ["crawl", "match"]
    assert kwargs["user_id"] == 1
