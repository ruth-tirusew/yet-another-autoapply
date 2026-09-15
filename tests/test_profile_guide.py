"""Tests for profile improvement guide cache and helpers."""

import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from src.coaching.profile_guide import (
    OSSSuggestion,
    ProfileGuideReport,
    build_profile_guide,
    cache_key,
    collect_user_projects,
    extract_stack,
    load_cached_guide,
    save_cached_guide,
    weak_categories,
)


SAMPLE_RESUME = {
    "basics": {"name": "Jane Doe", "label": "Backend Engineer"},
    "skills": [
        {"name": "Languages", "keywords": ["Python", "Go"]},
        {"name": "Frameworks", "keywords": ["FastAPI", "Django"]},
    ],
    "projects": [{"name": "API", "url": "https://github.com/jane/api"}],
    "work": [{"name": "Acme"}],
}

SAMPLE_EVALUATION = {
    "scores": {
        "open_source": {"score": 5, "max": 35, "evidence": "No external contributions"},
        "self_projects": {"score": 20, "max": 30, "evidence": "Solid side projects"},
        "production": {"score": 15, "max": 25, "evidence": "One internship"},
        "technical_skills": {"score": 8, "max": 10, "evidence": "Strong stack"},
    }
}


class ProfileGuideHelperTests(unittest.TestCase):
    def test_collect_user_projects_merges_and_dedupes(self):
        profile = {
            "resume_json": {
                "projects": [
                    {
                        "name": "API",
                        "url": "https://github.com/jane/api",
                        "description": "REST API",
                        "technologies": ["Python"],
                    }
                ],
                "work": [],
            },
            "github_json": {
                "projects": [
                    {
                        "name": "api",
                        "github_url": "https://github.com/jane/api",
                        "description": "Same repo",
                        "technologies": ["FastAPI"],
                        "project_type": "self_project",
                    },
                    {
                        "name": "bot",
                        "github_url": "https://github.com/jane/bot",
                        "description": "Telegram bot",
                        "technologies": ["Python"],
                        "project_type": "self_project",
                    },
                ]
            },
        }
        projects = collect_user_projects(profile)
        names = {p["name"] for p in projects}
        self.assertIn("API", names)
        self.assertIn("bot", names)
        self.assertEqual(len(projects), 2)

    def test_oss_suggestion_uses_related_project(self):
        oss = OSSSuggestion(
            name="FastAPI",
            url="https://github.com/fastapi/fastapi",
            related_project="API",
            why="Your API project uses FastAPI patterns.",
            first_step="Read contributing docs.",
        )
        self.assertEqual(oss.related_project, "API")

    def test_extract_stack_dedupes_and_flattens(self):
        profile = {
            "resume_json": SAMPLE_RESUME,
            "github_json": {
                "projects": [
                    {
                        "technologies": ["Python", "Redis"],
                        "github_details": {"languages": ["Go"]},
                    }
                ]
            },
        }
        stack = extract_stack(profile)
        self.assertIn("Python", stack)
        self.assertIn("FastAPI", stack)
        self.assertIn("Redis", stack)
        self.assertIn("Go", stack)
        self.assertEqual(len(stack), len(set(s.lower() for s in stack)))

    def test_weak_categories_below_half_max(self):
        weak = weak_categories(SAMPLE_EVALUATION)
        keys = {c["key"] for c in weak}
        self.assertIn("open_source", keys)
        self.assertNotIn("technical_skills", keys)

    def test_cache_key_changes_when_updated_at_changes(self):
        freq = Counter({"Kubernetes": 2})
        p1 = {"updated_at": "2026-01-01T00:00:00"}
        p2 = {"updated_at": "2026-01-02T00:00:00"}
        self.assertNotEqual(cache_key(p1, freq), cache_key(p2, freq))

    def test_cache_key_changes_when_gaps_change(self):
        profile = {"updated_at": "2026-01-01T00:00:00"}
        f1 = Counter({"Kubernetes": 2})
        f2 = Counter({"Terraform": 3})
        self.assertNotEqual(cache_key(profile, f1), cache_key(profile, f2))


class ProfileGuideCacheTests(unittest.TestCase):
    def test_save_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            uid = 99
            profile_dir = Path(tmp) / "users" / "99" / "profile"
            profile_dir.mkdir(parents=True)

            report = ProfileGuideReport(
                summary="Focus on OSS",
                cv_tips=[{"title": "Add metrics", "detail": "Quantify impact."}],
            )

            with patch("src.coaching.profile_guide.user_profile_dir", return_value=profile_dir):
                save_cached_guide(uid, "abc123", report)
                cached = load_cached_guide(uid)

            self.assertIsNotNone(cached)
            assert cached is not None
            self.assertEqual(cached.cache_key, "abc123")
            self.assertEqual(cached.report.summary, "Focus on OSS")
            self.assertEqual(len(cached.report.cv_tips), 1)

            guide_path = profile_dir / "guide.json"
            self.assertTrue(guide_path.exists())
            on_disk = json.loads(guide_path.read_text())
            self.assertEqual(on_disk["cache_key"], "abc123")


class BuildProfileGuideTests(unittest.TestCase):
    def test_no_profile_returns_upload_message(self):
        with patch("src.coaching.profile_guide.get_profile", return_value=None):
            report = build_profile_guide(user_id=1, use_llm=False)
        self.assertIn("Upload a CV", report.summary)


if __name__ == "__main__":
    unittest.main()
