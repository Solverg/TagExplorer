from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cache import CacheDatabaseError, FileCache, HAS_CIPHER, sqlite_engine


class CacheMigrationTest(unittest.TestCase):
    def test_unreadable_existing_database_is_not_discarded_without_cipher(self) -> None:
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "cache.db"
            original = b"encrypted-or-unknown-cache-data"
            db_path.write_bytes(original)
            with patch("core.cache._get_db_path", return_value=db_path), patch("core.cache.HAS_CIPHER", False):
                with self.assertRaises(CacheDatabaseError):
                    FileCache()
            self.assertEqual(db_path.read_bytes(), original)
            self.assertEqual([entry.name for entry in Path(tmp).iterdir()], ["cache.db"])

    def test_old_schema_keeps_records_and_thumbnails(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            db_path = base / "cache.db"
            file = base / "document.txt"
            conn = sqlite_engine.connect(str(db_path))
            if HAS_CIPHER:
                conn.execute("PRAGMA key = 'TagExplorer_Universal_Secret_2024_Shared'")
            conn.execute("CREATE TABLE files (path TEXT PRIMARY KEY, folder TEXT, filename TEXT, "
                         "tags TEXT, modified_time REAL, file_type TEXT, thumbnail BLOB)")
            conn.execute("INSERT INTO files VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (file.as_posix(), base.as_posix(), file.name, '[\"old\"]', 10, "document", b"old-image"))
            conn.commit()
            conn.close()
            with patch("core.cache._get_db_path", return_value=db_path), patch("core.cache._restrict_path_permissions"):
                cache = FileCache()
                try:
                    self.assertEqual(cache.get_record(file)["tags"], ["old"])
                    self.assertEqual(cache.get_thumbnail(file.as_posix()), b"old-image")
                    self.assertIsNone(cache.get_record(file)["size"])
                    cache.update_thumbnail(file.as_posix(), b"new-image")
                finally:
                    cache.close()
                cache = FileCache()
                try:
                    self.assertEqual(cache.get_thumbnail(file.as_posix()), b"new-image")
                finally:
                    cache.close()

    def test_json_migration_keeps_existing_rows_and_retries_bad_input(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            folder = base / "files"
            folder.mkdir()
            old_file = folder / "old.txt"
            existing = folder / "existing.txt"
            legacy_dir = base / "legacy"
            legacy_dir.mkdir()
            def record(path, tags):
                return {"path": path.as_posix(), "filename": path.name, "tags": tags,
                        "modified_time": 10, "file_type": "document"}
            source = legacy_dir / "scan_old.json"
            source.write_text(json.dumps({"root_folder": str(folder), "scanned_at": 10,
                                          "records": [record(old_file, ["legacy"]),
                                                      record(existing, ["stale"])]}), encoding="utf-8")
            bad = legacy_dir / "scan_bad.json"
            bad.write_text("{broken", encoding="utf-8")
            unrelated = legacy_dir / "notes.json"
            unrelated.write_text("keep", encoding="utf-8")
            with patch("core.cache._get_db_path", return_value=base / "db.sqlite"):
                cache = FileCache()
                try:
                    cache.update_files_batch([record(existing, ["new"])])
                    warnings = cache.migrate_legacy_scan_caches(legacy_dir)
                    self.assertEqual(len(warnings), 1)
                    self.assertFalse(source.exists())
                    self.assertTrue(bad.exists())
                    self.assertTrue(unrelated.exists())
                    self.assertEqual(cache.get_record(old_file)["tags"], ["legacy"])
                    self.assertEqual(cache.get_record(existing)["tags"], ["new"])
                    self.assertEqual(len(cache.migrate_legacy_scan_caches(legacy_dir)), 1)
                finally:
                    cache.close()

    def test_interrupted_json_migration_retains_the_original(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "scan_old.json"
            file = base / "old.txt"
            source.write_text(json.dumps({"root_folder": str(base), "scanned_at": 1,
                                          "records": [{"path": file.as_posix(), "filename": file.name,
                                                       "tags": [], "modified_time": 1,
                                                       "file_type": "document"}]}), encoding="utf-8")
            with patch("core.cache._get_db_path", return_value=base / "db.sqlite"):
                cache = FileCache()
                try:
                    checks = iter((False, True))
                    cache.migrate_legacy_scan_caches(base, lambda: next(checks))
                    self.assertTrue(source.exists())
                    self.assertIsNone(cache.get_record(file))
                    self.assertEqual(cache.migrate_legacy_scan_caches(base), [])
                    self.assertFalse(source.exists())
                    self.assertIsNotNone(cache.get_record(file))
                finally:
                    cache.close()

    @unittest.skipUnless(HAS_CIPHER, "SQLCipher is not installed")
    def test_plaintext_database_is_migrated_to_sqlcipher(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            db_path = base / "cache.db"
            file = base / "document.txt"
            conn = sqlite3.connect(str(db_path))
            conn.execute("CREATE TABLE files (path TEXT PRIMARY KEY, folder TEXT, filename TEXT, "
                         "tags TEXT, modified_time REAL, file_type TEXT)")
            conn.execute("INSERT INTO files VALUES (?, ?, ?, ?, ?, ?)",
                         (file.as_posix(), base.as_posix(), file.name, '[\"old\"]', 10, "document"))
            conn.commit()
            conn.close()
            with patch("core.cache._get_db_path", return_value=db_path), patch("core.cache._restrict_path_permissions"):
                cache = FileCache()
                try:
                    self.assertEqual(cache.get_record(file)["tags"], ["old"])
                    with db_path.open("rb") as handle:
                        self.assertNotEqual(handle.read(16), b"SQLite format 3\x00")
                finally:
                    cache.close()


if __name__ == "__main__":
    unittest.main()
