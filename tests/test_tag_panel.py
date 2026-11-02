from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HAS_QT = importlib.util.find_spec("PyQt6") is not None
if HAS_QT:
    from PyQt6.QtWidgets import QApplication
    from ui.tag_panel import TagPanel


@unittest.skipUnless(HAS_QT, "PyQt6 is not installed")
class TagPanelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_refresh_preserves_filters_including_tags_no_longer_present(self) -> None:
        panel = TagPanel()
        try:
            panel.set_tags(["work", "archive"])
            panel._on_tag_clicked("work")
            panel._on_tag_exclude_clicked("archive")
            panel.set_tags(["new"])
            self.assertEqual(panel.selected_tags(), ["work"])
            self.assertEqual(panel.excluded_tags(), ["archive"])
            panel.clear_selection()
            self.assertEqual(panel.selected_tags(), [])
            self.assertEqual(panel.excluded_tags(), [])
        finally:
            panel.close()

    def test_navigation_can_reset_filters(self) -> None:
        panel = TagPanel()
        try:
            panel.set_tags(["work"])
            panel._on_tag_clicked("work")
            panel.set_tags(["work"], preserve_selection=False)
            self.assertEqual(panel.selected_tags(), [])
        finally:
            panel.close()
