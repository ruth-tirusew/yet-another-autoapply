"""Shared test scaffolding for DB-backed unit tests.

Spins up a fresh temp-dir SQLite DB per test, isolated from the real
data/jobs.db. Mirrors the pattern originally in tests/test_prefilter.py.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("SESSION_SECRET", "test-session-secret-for-unit-tests")


class TempDBTestCase(unittest.TestCase):
    def setUp(self):
        from src.tenant import set_tenant_user_id

        # current_user_id is a contextvar — if some other test sets it directly
        # (outside a request thread) and forgets to reset it, it persists for
        # the rest of the pytest process. Reset it on both ends so this test
        # neither inherits nor leaks tenant state via that fallback default.
        set_tenant_user_id(None)

        self.tmp = tempfile.TemporaryDirectory()
        tmp_path = Path(self.tmp.name)
        self.patcher_db = mock.patch("src.settings.DB_PATH", tmp_path / "test.db")
        self.patcher_data = mock.patch("src.settings.DATA_DIR", tmp_path / "data")
        self.patcher_db.start()
        self.patcher_data.start()
        self.patcher_import = mock.patch("src.yaml_import.auto_import_if_empty", return_value=False)
        self.patcher_import.start()
        self.patcher_dirs = mock.patch("src.db.ensure_dirs")
        self.patcher_dirs.start()

        from src.db import init_db

        init_db()

    def tearDown(self):
        from src.tenant import set_tenant_user_id

        set_tenant_user_id(None)
        self.patcher_import.stop()
        self.patcher_dirs.stop()
        self.patcher_db.stop()
        self.patcher_data.stop()
        self.tmp.cleanup()
