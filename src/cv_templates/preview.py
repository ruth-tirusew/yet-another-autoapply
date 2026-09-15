"""Sample resume data for template studio previews."""

from __future__ import annotations

from src.db import get_application, get_profile
from src.profile import load_resume
from src.tenant import resolve_user_id


SAMPLE_RESUME: dict = {
    "basics": {
        "name": "Jane Developer",
        "email": "jane@example.com",
        "phone": "+1 555 0100",
        "url": "https://jane.dev",
        "summary": (
            "Senior software engineer with 8+ years building distributed systems "
            "and developer platforms. Strong in Python, Go, and cloud-native tooling."
        ),
        "profiles": [
            {"network": "GitHub", "url": "https://github.com/jane"},
            {"network": "LinkedIn", "url": "https://linkedin.com/in/jane"},
        ],
    },
    "work": [
        {
            "name": "Acme Corp",
            "position": "Staff Engineer",
            "startDate": "2021-03",
            "endDate": "",
            "summary": "Platform team lead for internal developer experience.",
            "highlights": [
                "Reduced deploy time 40% with a custom CI orchestration layer",
                "Mentored 4 engineers; shipped observability standards org-wide",
            ],
        },
        {
            "name": "Startup Labs",
            "position": "Backend Engineer",
            "startDate": "2017-06",
            "endDate": "2021-02",
            "highlights": [
                "Built FastAPI services handling 2M requests/day",
                "Introduced PostgreSQL migration tooling and zero-downtime deploys",
            ],
        },
    ],
    "education": [
        {
            "institution": "State University",
            "studyType": "B.S.",
            "area": "Computer Science",
        }
    ],
    "skills": [
        {"name": "Python", "keywords": ["FastAPI", "Django"]},
        {"name": "Go"},
        {"name": "PostgreSQL"},
        {"name": "Kubernetes"},
    ],
}


def preview_resume(sample: str = "profile", user_id: int | None = None, job_id: int | None = None) -> dict:
    uid = resolve_user_id(user_id)
    if sample == "tailored" and job_id:
        app = get_application(job_id, uid)
        if app and app.get("tailored_resume_json"):
            return app["tailored_resume_json"]
    if sample == "profile":
        profile = get_profile(uid)
        if profile and profile.get("resume_json"):
            return profile["resume_json"]
        loaded = load_resume(uid)
        if loaded:
            return loaded
    return SAMPLE_RESUME
