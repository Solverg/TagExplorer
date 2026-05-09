"""Update helpers for TagExplorer GitHub Releases."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

GITHUB_REPO = "Solverg/TagExplorer"
ASSET_NAME = "TagExplorer.exe"
NEW_ASSET_NAME = "TagExplorer_new.exe"
MIN_EXE_SIZE_BYTES = 1024 * 1024
USER_AGENT = f"{ASSET_NAME}/updater"

_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


class UpdateError(RuntimeError):
    """Raised when an update check or update install step fails."""


def is_frozen() -> bool:
    """Return True when TagExplorer is running as a bundled executable."""
    return bool(getattr(sys, "frozen", False))


def parse_version(version: str) -> tuple[int, int, int] | None:
    """Parse a release tag like v1.2.3 or 1.2.3 for version comparison."""
    match = _VERSION_RE.match(version.strip())
    if not match:
        return None
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch)


def find_release_asset_url(
    release_data: dict[str, Any],
    asset_name: str = ASSET_NAME,
) -> str | None:
    """Return the browser download URL for the named release asset."""
    for asset in release_data.get("assets", []):
        if asset.get("name") == asset_name:
            url = asset.get("browser_download_url")
            if isinstance(url, str) and url:
                return url
    return None


def fetch_latest_release() -> dict[str, Any]:
    """Fetch the latest GitHub release metadata."""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise UpdateError(f"GitHub returned HTTP {exc.code}.") from exc
    except urllib.error.URLError as exc:
        raise UpdateError(f"Could not reach GitHub: {exc.reason}") from exc

    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise UpdateError("GitHub returned an invalid release response.") from exc

    if not isinstance(data, dict):
        raise UpdateError("GitHub returned an unexpected release response.")
    return data


def validate_downloaded_exe(file_path: str | Path) -> None:
    """Perform a small sanity check that the downloaded file is a Windows EXE."""
    path = Path(file_path)
    if not path.exists():
        raise UpdateError("The update file was not found after download.")

    file_size = path.stat().st_size
    if file_size < MIN_EXE_SIZE_BYTES:
        raise UpdateError(
            "The downloaded update is too small. The download may be incomplete or corrupted."
        )

    with path.open("rb") as file_obj:
        magic = file_obj.read(2)

    if magic != b"MZ":
        raise UpdateError(
            "The downloaded update is not a Windows executable. The release asset may be invalid."
        )


def run_downloaded_exe_healthcheck(file_path: str | Path) -> None:
    """Run the downloaded executable in updater healthcheck mode."""
    create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(
        [str(file_path), "--healthcheck"],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
        creationflags=create_no_window,
    )

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        message = f"Update healthcheck failed with code {result.returncode}."
        if stderr:
            message = f"{message} {stderr}"
        raise UpdateError(message)


def update_target_dir() -> Path:
    """Return the directory where downloaded update files should be staged."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path.cwd().resolve()


def download_update(
    download_url: str,
    progress_callback: Callable[[int], None] | None = None,
) -> Path:
    """Download, validate, healthcheck, and stage a new TagExplorer executable."""
    target_dir = update_target_dir()
    temp_download_path: Path | None = None
    request = urllib.request.Request(download_url, headers={"User-Agent": USER_AGENT})

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            total_size = int(response.headers.get("content-length", 0) or 0)
            downloaded_size = 0

            fd, temp_name = tempfile.mkstemp(
                prefix="TagExplorer-",
                suffix=".part",
                dir=target_dir,
            )
            temp_download_path = Path(temp_name)

            with os.fdopen(fd, "wb") as file_obj:
                while True:
                    chunk = response.read(65536)
                    if not chunk:
                        break
                    file_obj.write(chunk)
                    downloaded_size += len(chunk)
                    if total_size > 0 and progress_callback is not None:
                        progress_callback(int((downloaded_size / total_size) * 100))

        if total_size > 0 and downloaded_size < total_size:
            raise UpdateError("The update download was interrupted before completion.")

        validate_downloaded_exe(temp_download_path)
        run_downloaded_exe_healthcheck(temp_download_path)

        staged_path = target_dir / NEW_ASSET_NAME
        if staged_path.exists():
            try:
                staged_path.unlink()
            except OSError as exc:
                raise UpdateError(f"Could not remove the previous staged update: {exc}") from exc

        os.replace(temp_download_path, staged_path)
        temp_download_path = None
        if progress_callback is not None:
            progress_callback(100)
        return staged_path
    except urllib.error.HTTPError as exc:
        raise UpdateError(f"GitHub returned HTTP {exc.code} while downloading the update.") from exc
    except urllib.error.URLError as exc:
        raise UpdateError(f"Could not download the update: {exc.reason}") from exc
    finally:
        if temp_download_path is not None and temp_download_path.exists():
            try:
                temp_download_path.unlink()
            except OSError:
                logger.debug("Could not remove temporary update file", exc_info=True)


def apply_update_and_relaunch(argv: Sequence[str] | None = None) -> int:
    """Replace the old executable with the running staged executable and relaunch."""
    args = list(sys.argv if argv is None else argv)
    try:
        update_arg_index = args.index("--apply-update")
        old_exe = Path(args[update_arg_index + 1]).resolve()
    except (ValueError, IndexError):
        print("--apply-update requires the target executable path.", file=sys.stderr)
        return 1

    new_exe = Path(sys.executable).resolve()
    exe_dir = old_exe.parent
    last_error: OSError | None = None

    for _attempt in range(30):
        try:
            os.replace(new_exe, old_exe)
            break
        except OSError as exc:
            last_error = exc
            time.sleep(0.5)
    else:
        print(f"Could not replace the old executable: {last_error}", file=sys.stderr)
        return 1

    subprocess.Popen(
        [str(old_exe)],
        cwd=str(exe_dir),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    os._exit(0)

