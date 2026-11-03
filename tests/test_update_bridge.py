from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
CSC = Path(os.environ.get("WINDIR", "C:/Windows")) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"


@unittest.skipUnless(sys.platform == "win32" and CSC.exists(), "Windows .NET Framework compiler required")
class UpdateBridgeIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.fake_app = cls.root / "fake-app.exe"
        fake_setup = cls.root / "fake-setup.exe"
        cleaner = cls.root / "cleaner.exe"
        cls.bridge = cls.root / "bridge.exe"
        version = cls.root / "version.txt"
        version.write_text("1.3.0", encoding="ascii")
        def compile_exe(output, *args):
            subprocess.run([str(CSC), "/nologo", "/target:winexe", "/platform:x64", f"/out:{output}",
                            *map(str, args)], check=True, capture_output=True)
        compile_exe(cls.fake_app, ROOT / "tests/fixtures/update_fake_app.cs")
        compile_exe(fake_setup, ROOT / "tests/fixtures/update_fake_setup.cs")
        compile_exe(cleaner, ROOT / "installer/BridgeCleaner.cs")
        compile_exe(cls.bridge, "/define:TEST_MODE", "/reference:System.Windows.Forms.dll",
                    f"/resource:{fake_setup},TagExplorer-Setup.exe", f"/resource:{cleaner},BridgeCleaner.exe",
                    f"/resource:{version},version.txt", ROOT / "installer/UpdateBridge.cs")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_upgrade(self, extra_env=None):
        with TemporaryDirectory(dir=self.root, prefix="папка с пробелами ") as tmp:
            base = Path(tmp)
            staged = base / "TagExplorer_new.exe"
            installed = base / "Program Files/TagExplorer/TagExplorer.exe"
            already_installed = (extra_env or {}).get("TAGEXPLORER_TEST_ALREADY_INSTALLED") == "1"
            old = installed if already_installed else base / "old TagExplorer.exe"
            marker = base / "launched.txt"
            error = base / "error.txt"
            old.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.fake_app, old)
            shutil.copyfile(self.bridge, staged)
            env = dict(os.environ, TAGEXPLORER_TEST_INSTALLED_EXE=str(installed),
                       TAGEXPLORER_TEST_APP_SOURCE=str(self.fake_app), TAGEXPLORER_TEST_LAUNCH_MARKER=str(marker),
                       TAGEXPLORER_TEST_ERROR_FILE=str(error), **(extra_env or {}))
            if env.pop("TAGEXPLORER_TEST_CHANGE_OLD", None) == "1":
                env["TAGEXPLORER_TEST_MODIFY_OLD"] = str(old)
            result = subprocess.run([str(staged), "--apply-update", str(old)], env=env, timeout=30)
            deadline = time.monotonic() + 5
            while staged.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            return result.returncode, old.exists(), installed.exists(), marker.exists(), error.read_text() if error.exists() else "", staged.exists()

    def test_success_starts_new_app_then_removes_old_and_staged_files(self):
        code, old, installed, marker, error, staged = self.run_upgrade()
        self.assertEqual((code, old, installed, marker, error, staged), (0, False, True, True, "", False))

    def test_cancel_keeps_old_executable(self):
        code, old, installed, marker, _error, _staged = self.run_upgrade({"TAGEXPLORER_TEST_CANCEL": "1"})
        self.assertEqual((code, old, installed, marker), (2, True, False, False))

    def test_installed_update_keeps_installed_executable(self):
        code, old, installed, marker, error, _staged = self.run_upgrade({"TAGEXPLORER_TEST_ALREADY_INSTALLED": "1"})
        self.assertEqual((code, old, installed, marker, error), (0, True, True, True, ""))

    def test_installed_update_resets_inherited_pyinstaller_environment(self):
        code, old, installed, marker, error, staged = self.run_upgrade({
            "TAGEXPLORER_TEST_ALREADY_INSTALLED": "1",
            "TAGEXPLORER_TEST_REQUIRE_RESET": "1",
            "_PYI_APPLICATION_HOME_DIR": "removed old extraction",
            "_PYI_PARENT_PROCESS_LEVEL": "1",
        })
        self.assertEqual((code, old, installed, marker, error, staged), (0, True, True, True, "", False))

    def test_invalid_install_keeps_old_executable(self):
        code, old, installed, marker, error, _staged = self.run_upgrade({"TAGEXPLORER_TEST_MISSING_INSTALL": "1"})
        self.assertEqual((code, old, installed, marker), (1, True, False, False))
        self.assertIn("not found", error)

    def test_changed_old_executable_is_preserved(self):
        code, old, installed, _marker, error, _staged = self.run_upgrade({"TAGEXPLORER_TEST_CHANGE_OLD": "1"})
        self.assertEqual((code, old, installed), (1, True, True))
        self.assertIn("changed", error)
