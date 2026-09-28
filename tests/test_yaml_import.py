"""Tests for src/yaml_import.py — specifically the DB-reset recovery path.

``import_yaml_to_db`` renames its input files to ``*.yaml.imported`` after a
successful import so it never re-imports on top of live edits. That used to
make the import a one-way door: once ``sources.yaml`` was renamed away, any
later reset of the database (not the YAML files) found nothing to import
from and silently fell back to the single seeded default, permanently
losing every other configured source. These tests pin down the fix —
``import_yaml_to_db`` and ``auto_import_if_empty`` must fall back to the
``.imported`` backup when the live file is gone, and must never double the
``.imported`` suffix when re-running against a backup that's already been
renamed once.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

from tests.helpers import TempDBTestCase

SOURCES_YAML = """
sources:
  - id: greenhouse
    name: Greenhouse
    adapter: greenhouse
    enabled: true
    companies: [shopify]
  - id: remoteok
    name: RemoteOK
    adapter: json_api
    enabled: true
"""


class YamlImportRecoveryTests(TempDBTestCase):
    def setUp(self):
        super().setUp()
        self.root = Path(self.tmp.name) / "repo_root"
        (self.root / "config").mkdir(parents=True)
        self.patcher_root = mock.patch("src.settings.ROOT", self.root)
        self.patcher_root.start()
        # TempDBTestCase patches auto_import_if_empty to a no-op for the
        # whole test (only stopped in its tearDown) so unrelated tests
        # never hit real import logic during init_db(). This test class is
        # about that exact function, so restore the real one for the test
        # body and re-arm the patch before super().tearDown() tries to stop
        # it again.
        self.patcher_import.stop()

    def tearDown(self):
        self.patcher_import.start()
        self.patcher_root.stop()
        super().tearDown()

    def _write_sources_backup(self) -> Path:
        path = self.root / "config" / "sources.yaml.imported"
        path.write_text(SOURCES_YAML, encoding="utf-8")
        return path

    def _write_config_backup(self) -> None:
        # Present in the real repo (config.yaml.imported, alongside
        # sources.yaml.imported). Without it, import_yaml_to_db's config-
        # collections branch calls seed_platform_defaults() as a side
        # effect, which also seeds the aggregator source — a real but
        # separate quirk this test suite isn't about, so it's avoided here
        # to keep the sources-recovery assertions isolated.
        (self.root / "config.yaml.imported").write_text("pipeline: {}\n", encoding="utf-8")

    def test_import_recovers_sources_from_imported_backup_when_live_file_is_gone(self):
        """The scenario that lost 22 sources: live sources.yaml absent, only
        the renamed backup remains, and the DB has no sources yet."""
        from src.config_store import PLATFORM_SOURCES_USER_ID
        from src.db import list_user_sources
        from src.yaml_import import import_yaml_to_db

        self._write_config_backup()
        self._write_sources_backup()
        summary = import_yaml_to_db(rename_after=True)

        self.assertEqual(summary["sources"], 2)
        ids = {s["id"] for s in list_user_sources(PLATFORM_SOURCES_USER_ID)}
        self.assertEqual(ids, {"greenhouse", "remoteok"})

    def test_rename_after_does_not_double_suffix_an_already_renamed_backup(self):
        from src.yaml_import import import_yaml_to_db

        self._write_config_backup()
        backup = self._write_sources_backup()
        import_yaml_to_db(rename_after=True)

        # The backup must still exist under its original name — a second
        # rename attempt (config_path.with_suffix(".yaml.imported") applied
        # to a path that already ends in .imported) would otherwise mangle
        # it into "sources.yaml.yaml.imported" or fail outright.
        self.assertTrue(backup.exists())
        self.assertFalse((self.root / "config" / "sources.yaml.yaml.imported").exists())

    def test_auto_import_if_empty_recovers_from_backup_not_just_bare_defaults(self):
        """Simulates the exact failure: platform collections empty (fresh/
        reset DB) and only the .imported backup on disk — must restore the
        real source list, not silently seed just the aggregator default."""
        from src.config_store import PLATFORM_SOURCES_USER_ID
        from src.db import list_user_sources
        from src.yaml_import import auto_import_if_empty

        self._write_config_backup()
        self._write_sources_backup()
        ran = auto_import_if_empty()

        self.assertTrue(ran)
        ids = {s["id"] for s in list_user_sources(PLATFORM_SOURCES_USER_ID)}
        self.assertEqual(ids, {"greenhouse", "remoteok"})

    def test_live_sources_file_is_still_preferred_over_a_stale_backup(self):
        from src.config_store import PLATFORM_SOURCES_USER_ID
        from src.db import list_user_sources
        from src.yaml_import import import_yaml_to_db

        self._write_config_backup()
        self._write_sources_backup()
        live = self.root / "config" / "sources.yaml"
        live.write_text(
            "sources:\n  - id: onlylive\n    name: Only Live\n    adapter: json_api\n    enabled: true\n",
            encoding="utf-8",
        )

        import_yaml_to_db(rename_after=True)

        ids = {s["id"] for s in list_user_sources(PLATFORM_SOURCES_USER_ID)}
        self.assertEqual(ids, {"onlylive"})
        # the live file was consumed and renamed, the stale backup left alone
        self.assertFalse(live.exists())
        self.assertTrue((self.root / "config" / "sources.yaml.imported").exists())


if __name__ == "__main__":
    import unittest

    unittest.main()
