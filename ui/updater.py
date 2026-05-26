"""Qt update checker and update dialog for TagExplorer."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from core.updater import (
    ASSET_NAME,
    UpdateError,
    download_update,
    fetch_latest_release,
    find_release_asset_url,
    is_frozen,
    parse_version,
)

logger = logging.getLogger(__name__)


class UpdateChecker(QThread):
    """Background thread that checks GitHub Releases for a newer version."""

    update_available = pyqtSignal(str, str)
    no_update = pyqtSignal(str)
    check_failed = pyqtSignal(str)

    def __init__(self, current_version: str) -> None:
        super().__init__()
        self.current_version = current_version

    def run(self) -> None:
        try:
            data = fetch_latest_release()
            latest_tag = str(data.get("tag_name", "")).strip()
            if not latest_tag:
                raise UpdateError("GitHub release response did not contain a version tag.")

            if data.get("draft") or data.get("prerelease"):
                self.no_update.emit(latest_tag)
                return

            latest_version = parse_version(latest_tag)
            current_version = parse_version(self.current_version)
            if latest_version is None:
                raise UpdateError(f"Unsupported GitHub release tag: {latest_tag}.")
            if current_version is None:
                raise UpdateError(f"Unsupported current app version: {self.current_version}.")

            if latest_version <= current_version:
                self.no_update.emit(latest_tag)
                return

            download_url = find_release_asset_url(data)
            if not download_url:
                raise UpdateError(f"Release {latest_tag} does not include {ASSET_NAME}.")

            if not self.isInterruptionRequested():
                self.update_available.emit(latest_tag, download_url)
        except UpdateError as exc:
            logger.info("Update check failed: %s", exc)
            if not self.isInterruptionRequested():
                self.check_failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Unexpected update check failure")
            if not self.isInterruptionRequested():
                self.check_failed.emit(str(exc))


class UpdateDownloader(QThread):
    """Background thread that downloads and stages an update."""

    progress = pyqtSignal(int)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, download_url: str) -> None:
        super().__init__()
        self.download_url = download_url

    def run(self) -> None:
        try:
            staged_path = download_update(self.download_url, self.progress.emit)
            self.finished.emit(str(staged_path))
        except Exception as exc:  # noqa: BLE001
            self.error.emit(str(exc))


class UpdateDialog(QDialog):
    """Prompt the user to download and install an available update."""

    def __init__(self, version: str, download_url: str, parent: Any = None) -> None:
        super().__init__(parent)
        self.version = version
        self.download_url = download_url
        self.downloader: UpdateDownloader | None = None

        self.setWindowTitle("Update Available")
        self.setFixedSize(420, 178)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        self.info_label = QLabel(
            f"<b>TagExplorer {version} is available.</b><br><br>"
            "Install the update now?"
        )
        self.info_label.setWordWrap(True)
        layout.addWidget(self.info_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)

        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self.cancel_button = QPushButton("Later")
        self.cancel_button.clicked.connect(self.reject)

        self.update_button = QPushButton("Update")
        self.update_button.setObjectName("primary")
        self.update_button.clicked.connect(self.start_update)

        button_layout.addWidget(self.cancel_button)
        button_layout.addWidget(self.update_button)
        layout.addLayout(button_layout)

    def start_update(self) -> None:
        self.update_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.progress_bar.show()
        self.info_label.setText("Downloading update...")

        self.downloader = UpdateDownloader(self.download_url)
        self.downloader.progress.connect(self.progress_bar.setValue)
        self.downloader.finished.connect(self.apply_update)
        self.downloader.error.connect(self.show_error)
        self.downloader.start()

    def show_error(self, error_message: str) -> None:
        self.info_label.setText(f"Update failed: {error_message}")
        self.cancel_button.setEnabled(True)
        self.cancel_button.setText("Close")

    def apply_update(self, new_exe_path: str) -> None:
        if not is_frozen():
            self.info_label.setText(
                f"Update downloaded to:<br>{Path(new_exe_path)}<br><br>"
                "Development mode does not replace the running app."
            )
            self.cancel_button.setEnabled(True)
            self.cancel_button.setText("Close")
            return

        self.info_label.setText("Installing update and restarting...")
        current_exe = sys.executable
        exe_dir = os.path.dirname(current_exe)
        create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        subprocess.Popen(
            [new_exe_path, "--apply-update", current_exe],
            cwd=exe_dir,
            creationflags=create_no_window,
        )
        os._exit(0)
