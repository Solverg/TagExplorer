from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.updater import find_release_asset_url, parse_version  # noqa: E402


class UpdaterVersionTest(unittest.TestCase):
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
