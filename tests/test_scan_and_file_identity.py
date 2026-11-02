from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cache import FileCache
from core.file_ops import FileChangedError, FileIdentity, RenameOperation, preflight_rename, safe_delete
from core.scanner import FileScanner


class RecursiveScanTest(unittest.TestCase):
    def test_recursive_scan_includes_hidden_and_previously_ignored_folders(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            for name in ("Windows", ".hidden", "node_modules"):
                directory = root / name
                directory.mkdir(parents=True)
                (directory / "document.txt").write_text(name, encoding="utf-8")
            records = FileScanner(Path(tmp) / "cache").deep_scan_directory(root)
            self.assertEqual({Path(record["path"]).parent.name for record in records},
                             {"Windows", ".hidden", "node_modules"})

    def test_repeated_scan_finds_nested_additions_deletions_and_changes(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "root"
            nested = root / "nested"
            nested.mkdir(parents=True)
            old = nested / "old [one].txt"
            old.write_text("old", encoding="utf-8")
            scanner = FileScanner(base / "cache")
            original = scanner.deep_scan_directory(root)
            self.assertEqual([r["filename"] for r in original], [old.name])

            # A legacy cache could still be present after an application upgrade.
            cache_dir = base / "cache"
            cache_dir.mkdir()
            key = str(root.resolve()).replace(":", "").replace("\\", "_").replace("/", "_")
            snapshot = root.stat()
            (cache_dir / f"scan_{key}.json").write_text(json.dumps({
                "scanned_at": time.time(),
                "root_snapshot": {"path": str(root.resolve()), "modified_time_ns": snapshot.st_mtime_ns,
                                  "device_id": snapshot.st_dev, "inode": snapshot.st_ino,
                                  "mode": snapshot.st_mode, "is_reparse_point": False},
                "records": original,
            }), encoding="utf-8")

            old.unlink()
            added = nested / "new [two].txt"
            added.write_text("new contents", encoding="utf-8")
            second = scanner.deep_scan_directory(root)
            self.assertEqual([r["filename"] for r in second], [added.name])
            added.write_text("updated contents are longer", encoding="utf-8")
            third = scanner.deep_scan_directory(root)
            self.assertEqual(third[0]["size"], added.stat().st_size)


class CachedIdentityTest(unittest.TestCase):
    def test_cached_identity_blocks_changed_file_for_rename_and_delete(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            folder = base / "files"
            nested = folder / "nested"
            nested.mkdir(parents=True)
            file = nested / "sample.txt"
            file.write_text("original", encoding="utf-8")
            scanner = FileScanner(base / "scan-cache")
            record = scanner.scan_directory(nested)[0]
            with patch("core.cache._get_db_path", return_value=base / "cache.db"):
                cache = FileCache()
                try:
                    cache.update_files_batch([record])
                    for loaded in (cache.get_all_files(nested)[0], cache.get_files_under(folder)[0],
                                   cache.get_record(file)):
                        self.assertIsNotNone(loaded)
                        for key in ("size", "modified_time_ns", "device_id", "inode"):
                            self.assertEqual(loaded[key], record[key])
                        preflight_rename(RenameOperation(file, nested / "renamed.txt", FileIdentity.from_record(loaded)))

                    file.write_text("changed contents are longer", encoding="utf-8")
                    identity = FileIdentity.from_record(cache.get_record(file))
                    with self.assertRaises(FileChangedError):
                        preflight_rename(RenameOperation(file, nested / "renamed.txt", identity))
                    with self.assertRaises(FileChangedError):
                        safe_delete(file, identity)
                    self.assertTrue(file.exists())
                finally:
                    cache.close()

    def test_replaced_file_with_same_size_and_time_is_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            folder = base / "files"
            folder.mkdir()
            file = folder / "sample.txt"
            file.write_bytes(b"original")
            original_stat = file.stat()
            record = FileScanner(base / "scan-cache").scan_directory(folder)[0]
            with patch("core.cache._get_db_path", return_value=base / "cache.db"):
                cache = FileCache()
                try:
                    cache.update_files_batch([record])
                    identity = FileIdentity.from_record(cache.get_record(file))
                    replacement = folder / "replacement.txt"
                    replacement.write_bytes(b"replaced")
                    os.utime(replacement, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
                    os.replace(replacement, file)
                    with self.assertRaises(FileChangedError):
                        preflight_rename(RenameOperation(file, folder / "renamed.txt", identity))
                finally:
                    cache.close()

    def test_unsigned_file_ids_round_trip(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            file = base / "sample.txt"
            file.write_text("x", encoding="utf-8")
            record = FileScanner(base / "scan-cache").scan_directory(base)[0]
            record.update(device_id=(1 << 64) - 1, inode=(1 << 63) + 123)
            with patch("core.cache._get_db_path", return_value=base / "cache.db"):
                cache = FileCache()
                try:
                    cache.update_files_batch([record])
                    loaded = cache.get_record(file)
                    self.assertEqual(loaded["device_id"], record["device_id"])
                    self.assertEqual(loaded["inode"], record["inode"])
                finally:
                    cache.close()


if __name__ == "__main__":
    unittest.main()
