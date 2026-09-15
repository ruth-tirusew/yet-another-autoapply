"""Tests for src/user_config.py (per-user config facade over config_store)."""

from __future__ import annotations

from tests.helpers import TempDBTestCase


class UserConfigTests(TempDBTestCase):
    def setUp(self):
        super().setUp()
        from src.db import create_user

        self.user_id = create_user("config@test.com", display_name="Config")["id"]

    def test_save_root_section_routes_sources_and_collections(self):
        from src.user_config import load_user_config_raw, save_user_config_section

        save_user_config_section(
            self.user_id,
            "root",
            {
                "pipeline": {"match_threshold": 55},
                "sources": [{"id": "custom", "name": "Custom", "enabled": True}],
                "unknown_key": {"ignored": True},
            },
        )
        raw = load_user_config_raw(self.user_id)
        self.assertEqual(raw["pipeline"]["match_threshold"], 55)
        self.assertEqual([s["id"] for s in raw["sources"]], ["custom"])
        self.assertNotIn("unknown_key", raw)

    def test_save_root_requires_dict(self):
        from src.user_config import save_user_config_section

        with self.assertRaises(ValueError):
            save_user_config_section(self.user_id, "root", ["not", "a", "dict"])

    def test_save_sources_section_direct(self):
        from src.user_config import get_user_sources, save_user_config_section

        save_user_config_section(self.user_id, "sources", [{"id": "a", "enabled": False}])
        self.assertEqual(get_user_sources(self.user_id)[0]["id"], "a")

    def test_save_named_collection_section(self):
        from src.user_config import load_user_config_raw, save_user_config_section

        save_user_config_section(self.user_id, "matching", {"scoring_mode": "rules"})
        raw = load_user_config_raw(self.user_id)
        self.assertEqual(raw["matching"]["scoring_mode"], "rules")

    def test_sources_isolated_per_user(self):
        from src.db import create_user
        from src.user_config import get_user_sources, save_user_config_section

        other_uid = create_user("config2@test.com", display_name="Config2")["id"]
        save_user_config_section(self.user_id, "sources", [{"id": "mine", "enabled": True}])
        self.assertEqual(get_user_sources(other_uid), [])
        self.assertEqual(get_user_sources(self.user_id)[0]["id"], "mine")


if __name__ == "__main__":
    import unittest

    unittest.main()
