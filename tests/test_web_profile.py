"""HTTP-level tests for src/web/routes/profile.py."""

from __future__ import annotations

import time
from unittest import mock

from tests.web_helpers import auth_client  # noqa: F401


def test_profile_page_shows_onboarding_when_no_cv_uploaded(auth_client):
    resp = auth_client.get("/profile")
    assert resp.status_code == 200


def test_upload_without_file_redirects_without_starting_upload(auth_client):
    with mock.patch(
        "src.web.routes.profile.start_profile_upload",
        side_effect=AssertionError("should not be called"),
    ):
        resp = auth_client.post("/profile/upload", files={}, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/profile"


def test_upload_runs_in_background_and_page_shows_progress(auth_client):
    started = mock.Mock()

    def fake_start(user_id, pdf_path):
        started(user_id, pdf_path)
        return True

    with mock.patch("src.web.routes.profile.start_profile_upload", side_effect=fake_start):
        resp = auth_client.post(
            "/profile/upload",
            files={"file": ("resume.pdf", b"%PDF-1.4 fake", "application/pdf")},
            follow_redirects=False,
        )
    assert resp.status_code == 303
    started.assert_called_once()

    with mock.patch("src.web.routes.profile.is_profile_uploading", return_value=True):
        page = auth_client.get("/profile")
    assert page.status_code == 200
    assert "Processing your resume" in page.text
    assert 'action="/profile/upload"' not in page.text


def test_upload_status_partial_polls_while_running(auth_client):
    with mock.patch("src.web.routes.profile.is_profile_uploading", return_value=True):
        resp = auth_client.get("/partials/profile-upload-status")
    assert resp.status_code == 200
    assert "hx-get=\"/partials/profile-upload-status\"" in resp.text


def test_upload_status_partial_refreshes_page_when_done(auth_client):
    with mock.patch("src.web.routes.profile.is_profile_uploading", return_value=False), mock.patch(
        "src.web.routes.profile.consume_profile_upload_result", return_value={"error": None}
    ):
        resp = auth_client.get("/partials/profile-upload-status")
    assert resp.status_code == 200
    assert resp.headers.get("hx-refresh") == "true"


def test_upload_status_partial_shows_error_without_crashing(auth_client):
    with mock.patch("src.web.routes.profile.is_profile_uploading", return_value=False), mock.patch(
        "src.web.routes.profile.consume_profile_upload_result",
        return_value={"error": "PDF extraction failed"},
    ):
        resp = auth_client.get("/partials/profile-upload-status")
    assert resp.status_code == 200
    assert "PDF extraction failed" in resp.text
    assert "hx-get" not in resp.text  # terminal state, no further polling


def test_upload_status_partial_shows_warnings_without_crashing(auth_client):
    with mock.patch("src.web.routes.profile.is_profile_uploading", return_value=False), mock.patch(
        "src.web.routes.profile.consume_profile_upload_result",
        return_value={"error": None, "warnings": ["Profile evaluation failed (boom); CV was saved without a score."]},
    ):
        resp = auth_client.get("/partials/profile-upload-status")
    assert resp.status_code == 200
    assert "Profile evaluation failed" in resp.text
    assert "hx-get" not in resp.text  # terminal state, no further polling


def test_profile_uploader_dedupes_concurrent_starts_for_same_user():
    from src.web.services import profile_uploader

    def slow_upload_cv(pdf_path, user_id=None):
        time.sleep(0.2)
        return {"warnings": []}

    with mock.patch("src.profile.upload_cv", side_effect=slow_upload_cv):
        assert profile_uploader.start_profile_upload(999, "/tmp/fake.pdf") is True
        assert profile_uploader.is_profile_uploading(999) is True
        # second call while already running is a no-op
        assert profile_uploader.start_profile_upload(999, "/tmp/other.pdf") is False

        for _ in range(50):
            if not profile_uploader.is_profile_uploading(999):
                break
            time.sleep(0.05)

    assert profile_uploader.is_profile_uploading(999) is False
    result = profile_uploader.consume_profile_upload_result(999)
    assert result == {"error": None, "warnings": []}


def test_profile_uploader_runs_upload_cv_and_records_result():
    from src.web.services import profile_uploader

    calls = []

    def fake_upload_cv(pdf_path, user_id=None):
        calls.append((pdf_path, user_id))
        return {"warnings": []}

    with mock.patch("src.profile.upload_cv", side_effect=fake_upload_cv):
        assert profile_uploader.start_profile_upload(998, "/tmp/fake.pdf") is True
        for _ in range(50):
            if not profile_uploader.is_profile_uploading(998):
                break
            time.sleep(0.05)

    assert calls == [("/tmp/fake.pdf", 998)]
    result = profile_uploader.consume_profile_upload_result(998)
    assert result == {"error": None, "warnings": []}
    # consuming pops the result — a second read finds nothing left
    assert profile_uploader.consume_profile_upload_result(998) is None


def test_profile_uploader_records_error_on_failure():
    from src.web.services import profile_uploader

    with mock.patch("src.profile.upload_cv", side_effect=RuntimeError("boom")):
        assert profile_uploader.start_profile_upload(997, "/tmp/fake.pdf") is True
        for _ in range(50):
            if not profile_uploader.is_profile_uploading(997):
                break
            time.sleep(0.05)

    result = profile_uploader.consume_profile_upload_result(997)
    assert result == {"error": "boom", "warnings": []}
