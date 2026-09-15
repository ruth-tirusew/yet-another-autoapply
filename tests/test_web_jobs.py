"""HTTP-level tests for src/web/routes/jobs.py."""

from __future__ import annotations

from unittest import mock

from tests.web_helpers import auth_client  # noqa: F401


def test_job_detail_404_for_missing_job(auth_client):
    resp = auth_client.get("/job/999999")
    assert resp.status_code == 404
    assert "Job not found" in resp.text


def test_job_detail_renders_for_existing_job(auth_client):
    from src.db import upsert_job

    upsert_job({"url": "https://example.com/job/1", "title": "Backend Engineer"}, user_id=1)
    resp = auth_client.get("/job/1")
    assert resp.status_code == 200
    assert "Backend Engineer" in resp.text


def test_jobs_list_renders_and_filters_by_status(auth_client):
    from src.db import connect, upsert_job

    upsert_job({"url": "https://example.com/job/1", "title": "New Role"}, user_id=1)
    upsert_job({"url": "https://example.com/job/2", "title": "Queued Role"}, user_id=1)
    with connect() as conn:
        conn.execute("UPDATE user_jobs SET status='queued' WHERE catalog_job_id=2")

    resp = auth_client.get("/jobs")
    assert resp.status_code == 200
    assert "New Role" in resp.text
    assert "Queued Role" in resp.text

    filtered = auth_client.get("/jobs?status=queued")
    assert filtered.status_code == 200
    assert "Queued Role" in filtered.text
    assert "New Role" not in filtered.text


def test_match_without_resume_redirects_with_profile_error(auth_client):
    from src.db import upsert_job

    upsert_job(
        {"url": "https://example.com/job/1", "title": "Backend Engineer"},
        user_id=1,
    )
    resp = auth_client.post("/job/1/match", follow_redirects=False)
    assert resp.status_code == 303
    assert "score_error=profile" in resp.headers["location"]


def test_match_missing_job_returns_404(auth_client):
    resp = auth_client.post("/job/999999/match")
    assert resp.status_code == 404


def test_reject_and_skip_transition_status(auth_client):
    from src.db import connect, upsert_job

    upsert_job({"url": "https://example.com/job/1", "title": "Backend Engineer"}, user_id=1)
    upsert_job({"url": "https://example.com/job/2", "title": "Other Engineer"}, user_id=1)

    resp = auth_client.post("/job/1/reject", follow_redirects=False)
    assert resp.status_code == 303
    resp = auth_client.post("/job/2/skip", follow_redirects=False)
    assert resp.status_code == 303

    with connect() as conn:
        statuses = {
            row["catalog_job_id"]: row["status"]
            for row in conn.execute("SELECT catalog_job_id, status FROM user_jobs").fetchall()
        }
    assert statuses[1] == "rejected"
    assert statuses[2] == "skipped"


def test_approve_job_generation_failure_still_approves_but_flags_error(auth_client):
    """A tailor/cover-letter failure during approve must not look like a clean success:
    the job stays approved (that's the human's intent) but the redirect flags the
    failure, an audit event is logged, and auto-apply must not fire without materials.
    """
    from src.db import connect, get_application_events, upsert_job

    upsert_job({"url": "https://example.com/job/1", "title": "Backend Engineer"}, user_id=1)

    with mock.patch("src.tailor.tailor_cv", side_effect=RuntimeError("LLM unreachable")), mock.patch(
        "src.cover_letter.generate_cover_letter"
    ) as gen_cover, mock.patch("src.web.routes.jobs.apply_to_job") as apply_mock:
        resp = auth_client.post("/job/1/approve", follow_redirects=False)

    assert resp.status_code == 303
    assert "approve_error=1" in resp.headers["location"]
    gen_cover.assert_not_called()
    apply_mock.assert_not_called()

    with connect() as conn:
        status = conn.execute(
            "SELECT status FROM user_jobs WHERE catalog_job_id=1"
        ).fetchone()["status"]
    assert status == "approved"

    events = get_application_events(1)
    failed = [e for e in events if e["event_type"] == "generate_failed"]
    assert len(failed) == 1
    assert "LLM unreachable" in failed[0]["detail"]["message"]

    detail = auth_client.get("/job/1?approve_error=1")
    assert detail.status_code == 200
    assert "Generation failed" in detail.text
    assert "LLM unreachable" in detail.text


def test_approve_job_success_redirects_without_error_flag(auth_client):
    from src.db import connect, upsert_job

    upsert_job({"url": "https://example.com/job/1", "title": "Backend Engineer"}, user_id=1)

    with mock.patch("src.tailor.tailor_cv"), mock.patch("src.cover_letter.generate_cover_letter"):
        resp = auth_client.post("/job/1/approve", follow_redirects=False)

    assert resp.status_code == 303
    assert resp.headers["location"] == "/job/1"

    with connect() as conn:
        status = conn.execute(
            "SELECT status FROM user_jobs WHERE catalog_job_id=1"
        ).fetchone()["status"]
    assert status == "approved"


def test_clear_jobs_wipes_queue_and_redirects_with_count(auth_client):
    from src.db import connect, upsert_job

    upsert_job({"url": "https://example.com/job/1", "title": "Backend Engineer"}, user_id=1)
    upsert_job({"url": "https://example.com/job/2", "title": "Other Engineer"}, user_id=1)

    resp = auth_client.post("/jobs/clear", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/jobs?cleared=2"

    with connect() as conn:
        remaining = conn.execute("SELECT COUNT(*) FROM user_jobs WHERE user_id=1").fetchone()[0]
    assert remaining == 0

    listing = auth_client.get("/jobs?cleared=2")
    assert listing.status_code == 200
    assert "Cleared 2 jobs" in listing.text
