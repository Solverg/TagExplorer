from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.media_safety import (  # noqa: E402
    MediaSafetyError,
    _validate_dimensions,
    explorer_select_warning_messages,
    is_network_path,
    startfile_warning_messages,
)


class MediaSafetyPolicyTest(unittest.TestCase):
    def test_network_path_detection(self) -> None:
        self.assertFalse(is_network_path("C:/Users/example/file.txt"))
        self.assertTrue(is_network_path("//server/share/file.txt"))
        self.assertTrue(is_network_path(r"\\server\share\file.txt"))

    def test_active_file_open_warnings(self) -> None:
        self.assertTrue(startfile_warning_messages("C:/tmp/run.exe"))
        shortcut_messages = startfile_warning_messages("C:/tmp/link.lnk")
        self.assertTrue(any("shortcut" in message.lower() for message in shortcut_messages))

    def test_explorer_warns_for_network_locations(self) -> None:
        self.assertFalse(explorer_select_warning_messages("C:/tmp/file.txt"))
        self.assertTrue(explorer_select_warning_messages("//server/share/file.txt"))

    def test_image_pixel_limit_rejects_decompression_bomb_shape(self) -> None:
        with self.assertRaises(MediaSafetyError):
            _validate_dimensions(50_001, 5_000, kind="Image")

        _validate_dimensions(10_000, 10_000, kind="Image")


if __name__ == "__main__":
    unittest.main()
