"""Tests for vector prefilter and embeddings cosine similarity."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.embeddings import cosine_similarity

os.environ.setdefault("SESSION_SECRET", "test-session-secret-for-unit-tests")


class PrefilterTests(unittest.TestCase):
    def test_cosine_identical_vectors(self):
        v = [1.0, 0.0, 0.0]
        self.assertAlmostEqual(cosine_similarity(v, v), 1.0)

    def test_cosine_orthogonal_vectors(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        self.assertAlmostEqual(cosine_similarity(a, b), 0.0)

    def test_cosine_empty_returns_zero(self):
        self.assertEqual(cosine_similarity([], [1.0]), 0.0)


class ResetEmbeddingsTests(unittest.TestCase):
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
        from src.catalog_db import embedding_to_blob, migrate_catalog_schema
        from src.db import connect, init_db

        init_db()
        blob = embedding_to_blob([0.1, 0.2, 0.3])
        from src.db import create_user

        user = create_user("embed@test.com", display_name="Embed")
        uid = user["id"]
        with connect() as conn:
            migrate_catalog_schema(conn)
            conn.execute(
                """
                INSERT INTO profile (
                    user_id, resume_json, updated_at,
                    embedding, embedding_general, embedding_niche
                )
                VALUES (?, '{}', '2026-01-01', ?, ?, ?)
                """,
                (uid, blob, blob, blob),
            )
            conn.execute(
                """
                INSERT INTO catalog_jobs (url_hash, title, company, status, embedding)
                VALUES ('abc', 'Engineer', 'Acme', 'active', ?)
                """,
                (blob,),
            )
            conn.execute(
                """
                INSERT INTO user_jobs (user_id, catalog_job_id, status, vector_score)
                VALUES (?, 1, 'new', 0.42)
                """,
                (uid,),
            )
        self.user_id = uid

    def tearDown(self):
        self.patcher_import.stop()
        self.patcher_dirs.stop()
        self.patcher_db.stop()
        self.patcher_data.stop()
        self.tmp.cleanup()

    def test_reset_embeddings_clears_stored_vectors(self):
        from src.db import connect
        from src.embeddings import reset_embeddings

        result = reset_embeddings()
        self.assertEqual(result["profiles"], 1)
        self.assertEqual(result["catalog_jobs"], 1)
        self.assertEqual(result["vector_scores"], 1)

        with connect() as conn:
            profile = conn.execute(
                """
                SELECT embedding, embedding_general, embedding_niche
                FROM profile WHERE user_id = ?
                """,
                (self.user_id,),
            ).fetchone()
            catalog = conn.execute(
                "SELECT embedding FROM catalog_jobs WHERE id = 1"
            ).fetchone()
            score = conn.execute(
                "SELECT vector_score FROM user_jobs WHERE id = 1"
            ).fetchone()
        self.assertIsNone(profile["embedding"])
        self.assertIsNone(profile["embedding_general"])
        self.assertIsNone(profile["embedding_niche"])
        self.assertIsNone(catalog["embedding"])
        self.assertIsNone(score["vector_score"])


if __name__ == "__main__":
    unittest.main()
