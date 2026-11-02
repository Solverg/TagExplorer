from __future__ import annotations

from pathlib import Path
import io
import os
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.updater import download_update, find_release_asset_url, parse_version  # noqa: E402


class UpdaterVersionTest(unittest.TestCase):
    def test_installed_update_stages_in_temporary_directory(self) -> None:
        class Response(io.BytesIO):
            headers = {"content-length": "4"}

        with TemporaryDirectory() as tmp:
            with patch("core.updater.is_frozen", return_value=True), \
                 patch("core.updater.tempfile.tempdir", tmp), \
                 patch("core.updater.urllib.request.urlopen", return_value=Response(b"fake")), \
                 patch("core.updater.validate_downloaded_exe"), \
                 patch("core.updater.run_downloaded_exe_healthcheck"):
                path = download_update("https://example.test/TagExplorer.exe")
                self.assertEqual(path.name, "TagExplorer_new.exe")
                self.assertEqual(os.path.commonpath([tmp, str(path)]), tmp)
                self.assertEqual(path.read_bytes(), b"fake")

    def test_parse_version_accepts_plain_and_v_tags(self) -> None:
        self.assertEqual(parse_version("1.2.3"), (1, 2, 3))
        self.assertEqual(parse_version("v1.2.3"), (1, 2, 3))

    def test_parse_version_rejects_unsupported_tags(self) -> None:
        self.assertIsNone(parse_version("1.2"))
        self.assertIsNone(parse_version("v1.2.3-beta"))

    def test_find_release_asset_url_returns_matching_asset(self) -> None:
        release = {
            "assets": [
                {"name": "notes.txt", "browser_download_url": "https://example.test/notes"},
                {
                    "name": "TagExplorer.exe",
                    "browser_download_url": "https://example.test/TagExplorer.exe",
                },
            ]
        }

        self.assertEqual(
            find_release_asset_url(release),
            "https://example.test/TagExplorer.exe",
        )


if __name__ == "__main__":
    unittest.main()
