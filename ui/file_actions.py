"""User-confirmed shell actions for files selected in the UI."""

from __future__ import annotations

from ui.i18n import tr, translate_detail

import os
from pathlib import Path
import subprocess

from PyQt6.QtWidgets import QMessageBox, QWidget

from core.file_ops import FileIdentity, FileOperationError, ensure_current_directory, ensure_current_regular_file
from core.tags import normalize_tags
from core.media_safety import (
    EXPLORER_SELECT_TIMEOUT_SECONDS,
    explorer_select_warning_messages,
    startfile_warning_messages,
)


def snapshot_selection(
    paths: list[Path], records: dict[str, dict], is_directory: bool,
) -> dict[Path, tuple[FileIdentity, list[str]]]:
    """Capture the selected objects before a dialog can run another scan."""
    snapshot: dict[Path, tuple[FileIdentity, list[str]]] = {}
    check = ensure_current_directory if is_directory else ensure_current_regular_file
    for path in paths:
        record = records.get(str(path))
        if record is None:
            raise FileOperationError(f"Selection is no longer available: {path}. Refresh and try again.")
        expected = FileIdentity.from_record(record)
        if expected is None or (expected.inode is None and expected.modified_time_ns is None):
            raise FileOperationError(f"Selection cannot be verified: {path}. Refresh and try again.")
        snapshot[path] = (check(path, expected), normalize_tags(record.get("tags", [])))
    return snapshot


def _confirm_risky_action(parent: QWidget, title: str, path: Path, messages: list[str]) -> bool:
    if not messages:
        return True

    details = "\n".join(f"- {translate_detail(message)}" for message in messages)
    reply = QMessageBox.question(
        parent,
        tr(title),
        tr("{details}\n\nContinue opening?\n\n{path}", details=details, path=path),
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
        QMessageBox.warning(parent, tr("Open file"), tr("Unable to open file:\n{error}", error=exc))


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
        QMessageBox.warning(parent, tr("Open location"), tr("Explorer did not respond in time."))
    except OSError as exc:
        QMessageBox.warning(parent, tr("Open location"), tr("Unable to open file location:\n{error}", error=exc))
