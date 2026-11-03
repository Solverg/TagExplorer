"""Exercise the real installer and update bridge in an isolated, non-admin directory."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.updater import run_downloaded_exe_healthcheck, validate_downloaded_exe  # noqa: E402
from main import APP_VERSION  # noqa: E402


def run(*args: object) -> None:
    subprocess.run([str(arg) for arg in args], check=True, timeout=180)


def main() -> None:
    if sys.platform != "win32":
        raise RuntimeError("The installer smoke test requires Windows.")
    compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    candidates = [ROOT / ".codex-python/installer-tools/inno/ISCC.exe"]
    candidates.extend(Path(os.environ[key]) / "Inno Setup 6/ISCC.exe"
                      for key in ("ProgramFiles(x86)", "ProgramFiles") if os.environ.get(key))
    if shutil.which("ISCC.exe"):
        candidates.append(Path(shutil.which("ISCC.exe")))
    iscc = next((path for path in candidates if path.is_file()), None)
    app = ROOT / "build/installer/app/TagExplorer.exe"
    if iscc is None or not all(p.is_file() for p in (compiler, app)):
        raise RuntimeError("Build the application first and ensure Inno Setup 6 and csc.exe are available.")

    with TemporaryDirectory(prefix="TagExplorer smoke ") as tmp:
        root = Path(tmp)
        output = root / "setup"
        output.mkdir()
        run(iscc, "/Qp", "/DTestMode=1", f"/DAppVersion={APP_VERSION}",
            f"/DAppExe={app}", f"/DOutputDir={output}", ROOT / "installer/TagExplorer.iss")
        setup = output / "TagExplorer-Setup.exe"
        cleaner = root / "cleaner.exe"
        legacy = root / "папка с пробелами/TagExplorer.exe"
        legacy.parent.mkdir()
        old_source = root / "old.cs"
        old_source.write_text((ROOT / "tests/fixtures/update_fake_app.cs").read_text(encoding="utf-8")
                              .replace("1.3.0.0", "1.2.0.0").replace("1.3.0", "1.2.0"), encoding="utf-8")
        run(compiler, "/nologo", "/target:winexe", "/platform:x64", f"/out:{legacy}", old_source)
        run(compiler, "/nologo", "/target:winexe", "/platform:x64", f"/out:{cleaner}", ROOT / "installer/BridgeCleaner.cs")
        version = root / "version.txt"
        version.write_text(APP_VERSION, encoding="ascii")
        bridge = legacy.parent / "TagExplorer_new.exe"
        run(compiler, "/nologo", "/target:winexe", "/platform:x64", "/define:TEST_MODE",
            "/reference:System.Windows.Forms.dll", f"/out:{bridge}", f"/resource:{setup},TagExplorer-Setup.exe",
            f"/resource:{cleaner},BridgeCleaner.exe", f"/resource:{version},version.txt", ROOT / "installer/UpdateBridge.cs")
        bridge_source = root / "bridge-source.exe"
        shutil.copyfile(bridge, bridge_source)
        validate_downloaded_exe(bridge)
        run_downloaded_exe_healthcheck(bridge)
        installed = root / "Program Files/TagExplorer/TagExplorer.exe"
        sentinel = root / "user-data.txt"
        sentinel.write_text("unchanged", encoding="utf-8")
        error = root / "error.txt"
        env = dict(os.environ, TAGEXPLORER_TEST_INSTALLED_EXE=str(installed),
                   TAGEXPLORER_TEST_SILENT_SETUP="1", TAGEXPLORER_TEST_ERROR_FILE=str(error))
        # Simulate the inherited state of the old one-file executable after its
        # extraction directory was deleted. Both the healthcheck and final
        # launch must reset that state before starting the installed build.
        env.update(_PYI_ARCHIVE_FILE=str(installed),
                   _PYI_APPLICATION_HOME_DIR=str(root / 'removed old extraction'),
                   _PYI_PARENT_PROCESS_LEVEL='1')
        env.pop('PYINSTALLER_RESET_ENVIRONMENT', None)
        result = subprocess.run([str(bridge), "--apply-update", str(legacy)], env=env, timeout=180)
        if result.returncode != 0:
            raise RuntimeError(f"Bridge failed ({result.returncode}): {error.read_text() if error.exists() else 'no error details'}")
        deadline = time.monotonic() + 15
        while bridge.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        if legacy.exists() or bridge.exists() or not installed.is_file() or sentinel.read_text() != "unchanged":
            raise RuntimeError("The installer or cleanup did not produce the expected result.")
        # Repeat with the installed executable at the same path as the old app;
        # this is the case where stale PyInstaller state refers to that path.
        shutil.copyfile(bridge_source, bridge)
        result = subprocess.run([str(bridge), "--apply-update", str(installed)], env=env, timeout=180)
        if result.returncode != 0:
            raise RuntimeError(f"Installed upgrade failed ({result.returncode}): {error.read_text() if error.exists() else 'no error details'}")
        deadline = time.monotonic() + 15
        while bridge.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        if bridge.exists() or not installed.is_file() or sentinel.read_text() != "unchanged":
            raise RuntimeError("Installed upgrade did not preserve the application and user data.")
        print("Real installer and bridge passed isolated portable and installed upgrade smoke tests.")


if __name__ == "__main__":
    main()
