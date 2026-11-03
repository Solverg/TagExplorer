from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HAS_QT = importlib.util.find_spec("PyQt6") is not None
if HAS_QT:
    from PyQt6.QtWidgets import QApplication, QMainWindow
    from ui.updater import UpdateDialog


@unittest.skipUnless(HAS_QT, "PyQt6 is not installed")
class UpdateDialogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_closes_main_window_before_starting_installer(self):
        events = []
        class Window(QMainWindow):
            def closeEvent(self, event):
                events.append("closed")
                event.accept()
        window = Window()
        dialog = UpdateDialog("v1.3.0", "https://example.test", window)
        with patch("ui.updater.is_frozen", return_value=True), \
             patch("ui.updater.subprocess.Popen", side_effect=lambda *a, **k: events.append("started")) as start, \
             patch("ui.updater.QApplication.quit"):
            dialog.apply_update("update.exe")
        self.assertEqual(events, ["closed", "started"])
        self.assertEqual(start.call_args.kwargs['env']['PYINSTALLER_RESET_ENVIRONMENT'], '1')
        dialog.close()

    def test_does_not_start_installer_if_window_refuses_to_close(self):
        class Window(QMainWindow):
            def closeEvent(self, event):
                event.ignore()
        window = Window()
        dialog = UpdateDialog("v1.3.0", "https://example.test", window)
        with patch("ui.updater.is_frozen", return_value=True), \
             patch("ui.updater.subprocess.Popen") as start:
            dialog.apply_update("update.exe")
        start.assert_not_called()
        self.assertIn("Could not close", dialog.info_label.text())
        dialog.close()
