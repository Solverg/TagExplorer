"""User-confirmed shell actions for files selected in the UI."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess

from PyQt6.QtWidgets import QMessageBox, QWidget

from core.media_safety import (
    EXPLORER_SELECT_TIMEOUT_SECONDS,
    explorer_select_warning_messages,
    startfile_warning_messages,
)


def _confirm_risky_action(parent: QWidget, title: str, path: Path, messages: list[str]) -> bool:
    if not messages:
        return True

    details = "\n".join(f"- {message}" for message in messages)
    reply = QMessageBox.question(
        parent,
        title,
        f"{details}\n\nContinue opening?\n\n{path}",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    return reply == QMessageBox.StandardButton.Yes


def open_path_with_shell(parent: QWidget, path: Path) -> None:
    if not _confirm_risky_action(parent, "Open file", path, startfile_warning_messages(path)):
        return

    try:
        os.startfile(path)  # type: ignore[attr-defined]
    except (FileNotFoundError, OSError) as exc:
        QMessageBox.warning(parent, "Open file", f"Unable to open file:\n{exc}")


def reveal_path_in_explorer(parent: QWidget, path: Path) -> None:
    if not _confirm_risky_action(
        parent,
        "Open location",
        path,
        explorer_select_warning_messages(path),
    ):
        return

    try:
        subprocess.run(
            ["explorer", "/select,", str(path)],
            check=False,
            timeout=EXPLORER_SELECT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        QMessageBox.warning(parent, "Open location", "Explorer did not respond in time.")
    except OSError as exc:
        QMessageBox.warning(parent, "Open location", f"Unable to open file location:\n{exc}")
