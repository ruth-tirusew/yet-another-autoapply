"""The job detail page surfaces grounded requirement verdicts."""

from __future__ import annotations

import json

from tests.web_helpers import auth_client  # noqa: F401

MATCH_DETAILS = {
    "overall_score": 78,
    "skill_match": {"score": 30, "max": 40, "evidence": "3 met, 1 partial"},
    "experience_match": {"score": 30, "max": 35, "evidence": "meets years"},
    "role_fit": {"score": 18, "max": 25, "evidence": "senior vs senior"},
    "gaps": ["Strong SQL skills"],
    "strengths": ["Kubernetes — Backend @ Acme"],
    "recommendation": "apply",
    "tailoring_hints": [],
    "engine": "grounded",
    "coverage": 0.75,
    "retrieval": "vector",
    "model": "test-model",
    "warnings": ["Requirements extracted without a model (bullet heuristics)."],
    "requirements": [
        {
            "id": "r1",
            "text": "Experience running services on Kubernetes",
            "kind": "must",
            "verdict": "met",
            "evidence": [{"chunk_id": "work-0", "ref": "Backend @ Acme", "quote": "Ran clusters"}],
        },
        {
            "id": "r2",
            "text": "Strong SQL skills",
            "kind": "must",
            "verdict": "missing",
            "evidence": [],
            "note": "no supporting evidence retrieved",
        },
        {"id": "r3", "text": "Go", "kind": "nice", "verdict": "partial", "evidence": []},
    ],
}


def _seed_job(match_details=None):
    from src.db import connect, upsert_job

    upsert_job({"url": "https://example.com/job/1", "title": "Senior Backend Engineer"}, user_id=1)
    if match_details is not None:
        with connect() as conn:
            conn.execute(
                "UPDATE user_jobs SET status='queued', match_score=?, match_details=?",
                (match_details["overall_score"], json.dumps(match_details)),
            )


def test_requirement_panel_lists_each_verdict_with_its_evidence(auth_client):
    _seed_job(MATCH_DETAILS)
    body = auth_client.get("/job/1").text
    assert "Requirement coverage" in body
    assert "Experience running services on Kubernetes" in body
    assert "Backend @ Acme" in body
    assert "Ran clusters" in body
    assert "no supporting evidence retrieved" in body


def test_panel_shows_coverage_retrieval_and_warnings(auth_client):
    _seed_job(MATCH_DETAILS)
    body = auth_client.get("/job/1").text
    assert "75% weighted coverage" in body
    assert "vector retrieval" in body
    assert "nice to have" in body
    assert "bullet heuristics" in body


def test_panel_is_absent_for_legacy_results_without_requirements(auth_client):
    legacy = {k: v for k, v in MATCH_DETAILS.items() if k != "requirements"}
    legacy["engine"] = "llm"
    _seed_job(legacy)
    body = auth_client.get("/job/1").text
    assert "Requirement coverage" not in body
    assert "Why it matches" in body, "the legacy match panels must still render"


def test_unscored_job_detail_still_renders(auth_client):
    _seed_job()
    assert auth_client.get("/job/1").status_code == 200
