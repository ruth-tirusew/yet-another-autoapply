"""Tests for secrets encryption and config store."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("SESSION_SECRET", "test-session-secret-for-unit-tests")


class TestSecrets(unittest.TestCase):
    def test_encrypt_decrypt_roundtrip(self):
        from src.secrets import decrypt_secret, encrypt_secret, mask_secret

        plain = "gsk_test_key_abcdefghijklmnop"
        cipher = encrypt_secret(plain)
        self.assertNotEqual(cipher, plain)
        self.assertEqual(decrypt_secret(cipher), plain)
        self.assertTrue(mask_secret(plain).startswith("••••••"))
        self.assertTrue(mask_secret(plain).endswith("mnop"))


class TestConfigStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        self.db_path = self.tmp_path / "test.db"
        self.data_dir = self.tmp_path / "data"
        self.patcher_db = mock.patch("src.settings.DB_PATH", self.db_path)
        self.patcher_data = mock.patch("src.settings.DATA_DIR", self.data_dir)
        self.patcher_db.start()
        self.patcher_data.start()
        self.patcher_import = mock.patch("src.yaml_import.auto_import_if_empty", return_value=False)
        self.patcher_import.start()
        self.patcher_dirs = mock.patch("src.db.ensure_dirs")
        self.patcher_dirs.start()
        from src.db import init_db

        init_db()

    def tearDown(self):
        self.patcher_import.stop()
        self.patcher_dirs.stop()
        self.patcher_db.stop()
        self.patcher_data.stop()
        self.tmp.cleanup()

    def test_platform_collection_roundtrip(self):
        from src.config_store import get_collection, save_collection

        save_collection("pipeline", {"match_threshold": 55}, scope="platform", scope_id=0)
        data = get_collection("pipeline", scope="platform", scope_id=0)
        self.assertEqual(data["match_threshold"], 55)

    def test_credential_tenant_isolation(self):
        from src.db import create_user, get_llm_api_key, save_llm_credential

        u1 = create_user("a@test.com", display_name="A")
        u2 = create_user("b@test.com", display_name="B")
        save_llm_credential(u1["id"], "groq", "key-for-user-a")
        save_llm_credential(u2["id"], "groq", "key-for-user-b")
        self.assertEqual(get_llm_api_key(u1["id"], "groq"), "key-for-user-a")
        self.assertEqual(get_llm_api_key(u2["id"], "groq"), "key-for-user-b")

    def test_default_embedding_model(self):
        from src.config_store import DEFAULT_COLLECTIONS

        self.assertEqual(
            DEFAULT_COLLECTIONS["matching"]["embedding_model"],
            "mxbai-embed-large",
        )


class TestYamlImport(unittest.TestCase):
    def test_normalize_config_yaml(self):
        from src.yaml_import import _normalize_config_yaml

        raw = {
            "pipeline": {"match_threshold": 80},
            "stale_job_days": 14,
            "models": {"default": "a", "quality": "b"},
        }
        out = _normalize_config_yaml(raw)
        self.assertEqual(out["pipeline"]["match_threshold"], 80)
        self.assertEqual(out["pipeline"]["stale_days"], 14)
        self.assertEqual(out["models"]["default"], "a")


if __name__ == "__main__":
    unittest.main()
