"""File list widget and tag editing dialog."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from PyQt6.QtCore import (
    QAbstractItemModel,
    QEvent,
    QFileInfo,
    QItemSelectionModel,
    QModelIndex,
    QTimer,
    Qt,
    pyqtSignal,
)
from PyQt6.QtGui import QAction, QFont
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileIconProvider,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from core.scanner import FileScanner
from core.file_ops import (
    BatchRenameError,
    FileIdentity,
    FileOperationError,
    FilenameValidationError,
    RenameOperation,
    TargetExistsError,
    build_target_path,
    ensure_current_regular_file,
    execute_rename_plan,
    path_key,
    preflight_rename,
    safe_delete,
    safe_rename,
    unique_path,
)
from core.tags import (
    TagValidationError,
    build_tagged_filename,
    normalize_tags,
    sort_tags,
    tag_sort_key,
    validate_tag_text,
)
from ui.file_actions import open_path_with_shell, reveal_path_in_explorer
from ui.image_viewer import ImageViewerDialog, get_supported_image_suffixes


class TagEditDialog(QDialog):
    """Dialog for adding and removing tags. Supports 3-state batch editing."""
    _BASE_TAG_COLORS = [
        "#3B4F7C",
        "#2E6B5E",
        "#5B3E7A",
        "#7A3E3E",
        "#2C5F7A",
        "#6B4C2A",
        "#2A3140",
        "#303440",
        "#2D3A35",
        "#1B4D3E",
        "#4A2C4D",
        "#839958",
        "#105666",
    ]

    def __init__(
        self,
        title_info: str,
        initial_states: dict[str, int],
        available_tags: list[str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.tag_states = dict(initial_states)
        self.available_tags = available_tags
        self.tag_buttons: dict[str, QPushButton] = {}
        self.tag_base_colors: dict[str, str] = {}

        self.setWindowTitle("Edit tags")
        self.resize(480, 500)

        layout = QVBoxLayout(self)
        self.file_label = QLabel(title_info)
        self.file_label.setStyleSheet("color: #f4f4f4; font-weight: normal;")
        layout.addWidget(self.file_label)

        avail_label = QLabel("Available tags (Click to add/remove):")
        avail_label.setStyleSheet("color: #f4f4f4; font-weight: normal;")
        layout.addWidget(avail_label)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setMinimumHeight(140)

        self.avail_container = QWidget()
        self.avail_layout = QGridLayout(self.avail_container)
        self.avail_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll_area.setWidget(self.avail_container)
        layout.addWidget(self.scroll_area)

        self._rebuild_grid()

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(line)

        self.chips_container = QWidget()
        self.chips_layout = QHBoxLayout(self.chips_container)
        layout.addWidget(self.chips_container)

        input_row = QHBoxLayout()
        self.tag_input = QLineEdit()
        self.tag_input.setPlaceholderText("Enter new tag")
        self.add_button = QPushButton("Add")
        self.add_button.clicked.connect(self._add_tag)
        input_row.addWidget(self.tag_input)
        input_row.addWidget(self.add_button)
        layout.addLayout(input_row)

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        self._refresh_chips()

    def _rebuild_grid(self) -> None:
        while self.avail_layout.count():
            item = self.avail_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.tag_buttons.clear()

        cols = 3
        all_known = sorted(set(self.available_tags) | set(self.tag_states.keys()), key=tag_sort_key)
        for i, tag in enumerate(all_known):
            btn = QPushButton(tag)
            btn.setCheckable(True)
            self.tag_base_colors[tag] = self._BASE_TAG_COLORS[i % len(self._BASE_TAG_COLORS)]
            state = self.tag_states.get(tag, 0)
            self._apply_button_style(btn, tag, state)
            btn.clicked.connect(lambda _checked=False, t=tag: self._toggle_tag(t))
            self.avail_layout.addWidget(btn, i // cols, i % cols)
            self.tag_buttons[tag] = btn

    @staticmethod
    def _hex_to_rgba(hex_color: str, alpha: float) -> str:
        clean_hex = hex_color.lstrip("#")
        red = int(clean_hex[0:2], 16)
        green = int(clean_hex[2:4], 16)
        blue = int(clean_hex[4:6], 16)
        return f"rgba({red}, {green}, {blue}, {alpha})"

    def _apply_button_style(self, btn: QPushButton, tag: str, state: int) -> None:
        is_dark = self.palette().window().color().lightness() < 128
        base_color = self.tag_base_colors.get(tag, self._BASE_TAG_COLORS[0])
        text_color = "#f7f9fc" if is_dark else "#1f2a37"
        selected_text_color = "#ffffff" if is_dark else "#0f172a"
        border_color = "rgba(255, 255, 255, 0.2)" if is_dark else "rgba(15, 23, 42, 0.24)"
        checked_border = "rgba(120, 200, 255, 0.95)" if is_dark else "rgba(31, 111, 235, 0.95)"
        base_alpha = 0.9 if is_dark else 0.18
        hover_alpha = min(base_alpha + 0.08, 0.95)
        selected_alpha = 0.95 if is_dark else 0.28
        mixed_alpha = min(base_alpha + 0.05, 0.95)
        base_background = self._hex_to_rgba(base_color, base_alpha)
        hover_background = self._hex_to_rgba(base_color, hover_alpha)
        checked_background = self._hex_to_rgba(base_color, selected_alpha)
        mixed_background = self._hex_to_rgba(base_color, mixed_alpha)

        btn.setStyleSheet(
            f"""
            QPushButton {{
                border: 1px solid {border_color};
                border-radius: 6px;
                padding: 4px 10px;
                background-color: {base_background};
                color: {text_color};
                text-align: center;
                font-weight: 500;
            }}
            QPushButton:checked {{
                background-color: {checked_background};
                color: {selected_text_color};
                border: 2px solid {checked_border};
                padding: 3px 9px;
                font-weight: 650;
            }}
            QPushButton:hover:!checked {{
                background-color: {hover_background};
            }}
            QPushButton[mixed="true"] {{
                background-color: {mixed_background};
                color: {text_color};
                border: 1px dashed {checked_border};
                border-radius: 6px;
                padding: 4px 10px;
                font-weight: 500;
            }}
            """
        )

        is_mixed = state == 2
        btn.setProperty("mixed", is_mixed)
        btn.setChecked(state == 1)
        btn.style().unpolish(btn)
        btn.style().polish(btn)
        btn.update()

        if state == 1:
            btn.setToolTip("Will be added to all selected files")
        elif state == 2:
            btn.setToolTip("Present in some files. Click to apply to all.")
        else:
            btn.setToolTip("Click to add to all selected files")

    def _toggle_tag(self, tag: str) -> None:
        current = self.tag_states.get(tag, 0)
        if current == 1:
            self.tag_states[tag] = 0
        elif current == 2:
            self.tag_states[tag] = 1
        else:
            self.tag_states[tag] = 1

        if tag in self.tag_buttons:
            self._apply_button_style(self.tag_buttons[tag], tag, self.tag_states[tag])
        self._refresh_chips()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() in {QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange}:
            for tag, button in self.tag_buttons.items():
                self._apply_button_style(button, tag, self.tag_states.get(tag, 0))
            self._refresh_chips()

    def _refresh_chips(self) -> None:
        while self.chips_layout.count():
            item = self.chips_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        active_tags = [(tag, state) for tag, state in self.tag_states.items() if state in (1, 2)]
        active_tags.sort(key=lambda item: tag_sort_key(item[0]))

        if not active_tags:
            no_tags_label = QLabel("No tags selected")
            no_tags_label.setStyleSheet("color: #f4f4f4; font-weight: normal;")
            self.chips_layout.addWidget(no_tags_label)
            self.chips_layout.addStretch(1)
            return

        for tag, state in active_tags:
            badge = QFrame()
            badge.setFrameShape(QFrame.Shape.StyledPanel)
            base_color = self.tag_base_colors.get(tag, self._BASE_TAG_COLORS[0])
            is_dark = self.palette().window().color().lightness() < 128
            chip_bg = self._hex_to_rgba(base_color, 0.95 if (is_dark and state == 1) else (0.28 if state == 1 else (0.9 if is_dark else 0.18)))
            chip_text = "#ffffff" if (state == 1 and is_dark) else ("#0f172a" if state == 1 else ("#f7f9fc" if is_dark else "#1f2a37"))
            chip_border = "rgba(120, 200, 255, 0.95)" if is_dark else "rgba(31, 111, 235, 0.95)"
            border_style = "solid" if state == 1 else "dashed"
            border_width = 2 if state == 1 else 1
            badge.setStyleSheet(
                f"QFrame {{ background-color: {chip_bg}; border: {border_width}px {border_style} {chip_border}; border-radius: 6px; }}"
            )
            badge_layout = QHBoxLayout(badge)
            badge_layout.setContentsMargins(6, 2, 6, 2)
            label_text = f"{tag} (mixed)" if state == 2 else tag
            label = QLabel(label_text)
            label.setStyleSheet(f"color: {chip_text}; font-weight: normal;")
            badge_layout.addWidget(label)
            remove_button = QPushButton("×")
            remove_button.setFixedWidth(24)
            remove_button.setStyleSheet("color: #f4f4f4; font-weight: normal; border: none; background: transparent;")
            remove_button.clicked.connect(lambda _checked=False, t=tag: self._remove_chip(t))
            badge_layout.addWidget(remove_button)
            self.chips_layout.addWidget(badge)
        self.chips_layout.addStretch(1)

    def _add_tag(self) -> None:
        new_tag = self.tag_input.text().strip()
        if not new_tag:
            return
        try:
            validate_tag_text(new_tag)
        except TagValidationError as exc:
            QMessageBox.warning(self, "Invalid tag", str(exc))
            return

        self.tag_states[new_tag] = 1
        if new_tag not in self.available_tags:
            self.available_tags.append(new_tag)
            self.available_tags.sort(key=tag_sort_key)
            self._rebuild_grid()
        elif new_tag in self.tag_buttons:
            self._apply_button_style(self.tag_buttons[new_tag], new_tag, 1)

        self._refresh_chips()
        self.tag_input.clear()

    def _remove_chip(self, tag: str) -> None:
        self.tag_states[tag] = 0
        if tag in self.tag_buttons:
            self._apply_button_style(self.tag_buttons[tag], tag, 0)
        self._refresh_chips()

    def get_actions(self) -> tuple[set[str], set[str]]:
        """Return sets for adding to all files and removing from all files."""
        add_set = {tag for tag, state in self.tag_states.items() if state == 1}
        remove_set = {tag for tag, state in self.tag_states.items() if state == 0}
        return add_set, remove_set


class FileTreeModel(QAbstractItemModel):
    """Легковесная виртуальная модель для отображения десятков тысяч файлов без затрат памяти."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tagged_records: list[dict] = []
        self.untagged_records: list[dict] = []
        self.icon_provider = QFileIconProvider()
        self._icon_cache: dict[str, Any] = {}

    def set_files(self, tagged: list[dict], untagged: list[dict]) -> None:
        self.beginResetModel()
        self.tagged_records = tagged[:]
        self.untagged_records = untagged[:]
        self.endResetModel()

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole and section == 0:
            return "Name"
        return None

    def index(self, row: int, column: int, parent: QModelIndex = QModelIndex()) -> QModelIndex:
        if not self.hasIndex(row, column, parent):
            return QModelIndex()

        if not parent.isValid():
            return self.createIndex(row, column, 1 if row == 0 else 2)

        pid = parent.internalId()
        if pid == 1:
            return self.createIndex(row, column, 10_000_000 + row)
        if pid == 2:
            return self.createIndex(row, column, 20_000_000 + row)

        return QModelIndex()

    def parent(self, index: QModelIndex) -> QModelIndex:
        if not index.isValid():
            return QModelIndex()

        node_id = index.internalId()
        if node_id in (1, 2):
            return QModelIndex()
        if node_id >= 20_000_000:
            return self.createIndex(1, 0, 2)
        if node_id >= 10_000_000:
            return self.createIndex(0, 0, 1)

        return QModelIndex()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if not parent.isValid():
            return 2

        node_id = parent.internalId()
        if node_id == 1:
            return len(self.tagged_records)
        if node_id == 2:
            return len(self.untagged_records)

        return 0

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 1

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None

        node_id = index.internalId()

        if node_id == 1:
            if role == Qt.ItemDataRole.DisplayRole:
                return "Tagged files"
            if role == Qt.ItemDataRole.FontRole:
                font = QFont()
                font.setBold(True)
                return font
            return None
        if node_id == 2:
            if role == Qt.ItemDataRole.DisplayRole:
                return "Untagged files"
            if role == Qt.ItemDataRole.FontRole:
                font = QFont()
                font.setBold(True)
                return font
            return None

        if node_id >= 20_000_000:
            row = node_id - 20_000_000
            if row >= len(self.untagged_records):
                return None
            record = self.untagged_records[row]
        else:
            row = node_id - 10_000_000
            if row >= len(self.tagged_records):
                return None
            record = self.tagged_records[row]

        if role == Qt.ItemDataRole.DisplayRole:
            return record["filename"]
        if role == Qt.ItemDataRole.UserRole:
            return record["path"]
        if role == Qt.ItemDataRole.DecorationRole:
            ext = Path(record["path"]).suffix.lower()
            if ext not in self._icon_cache:
                self._icon_cache[ext] = self.icon_provider.icon(QFileInfo(record["path"]))
            return self._icon_cache[ext]

        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags

        node_id = index.internalId()
        if node_id in (1, 2):
            return Qt.ItemFlag.ItemIsEnabled

        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable


class FileListWidget(QWidget):
    """Displays tagged and untagged files using a virtualized tree model."""

    selection_changed = pyqtSignal(list)
    files_changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._records_by_path: dict[str, dict] = {}
        self._viewer: ImageViewerDialog | None = None
        self.tags_at_start = False
        self._reload_scroll_restore: int | None = None
        self._pending_scroll_restore: int | None = None
        self._pending_scroll_restore_attempts = 0

        layout = QVBoxLayout(self)
        self.tree = QTreeView()
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QTreeView.SelectionMode.ExtendedSelection)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.model = FileTreeModel(self)
        self.tree.setModel(self.model)
        self.tree.selectionModel().selectionChanged.connect(self._on_selection_changed)
        self.tree.doubleClicked.connect(self._open_file)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)
        layout.addWidget(self.tree)

    def scroll_position(self) -> int:
        return self.tree.verticalScrollBar().value()

    def preserve_scroll_on_next_reload(self) -> None:
        self._reload_scroll_restore = self.scroll_position()

    def restore_scroll_position(self, value: int) -> None:
        self._pending_scroll_restore = max(0, value)
        self._pending_scroll_restore_attempts = 12
        self._apply_pending_scroll_restore()

    def _apply_pending_scroll_restore(self) -> None:
        if self._pending_scroll_restore is None:
            return

        value = self._pending_scroll_restore
        scroll_bar = self.tree.verticalScrollBar()
        scroll_bar.setValue(min(value, scroll_bar.maximum()))
        if self._pending_scroll_restore_attempts > 0:
            self._pending_scroll_restore_attempts -= 1
            QTimer.singleShot(25, self._apply_pending_scroll_restore)
            return

        self._pending_scroll_restore = None

    def set_files(self, tagged: list[dict], untagged: list[dict]) -> None:
        """Populate virtual file list, preserving expand/collapse state."""
        tagged_expanded = True
        untagged_expanded = True
        if self.model.rowCount() == 2:
            tagged_expanded = self.tree.isExpanded(self.model.index(0, 0))
            untagged_expanded = self.tree.isExpanded(self.model.index(1, 0))

        self._records_by_path.clear()
        for record in tagged + untagged:
            self._records_by_path[str(Path(record["path"]))] = record

        self.model.set_files(tagged, untagged)
        self.tree.setExpanded(self.model.index(0, 0), tagged_expanded)
        self.tree.setExpanded(self.model.index(1, 0), untagged_expanded)
        if self._reload_scroll_restore is not None:
            scroll_position = self._reload_scroll_restore
            self._reload_scroll_restore = None
            self.restore_scroll_position(scroll_position)

    def _selected_paths(self) -> list[Path]:
        paths: list[Path] = []
        for index in self.tree.selectionModel().selectedIndexes():
            raw_path = index.data(Qt.ItemDataRole.UserRole)
            if raw_path:
                paths.append(Path(raw_path))
        return paths

    def _on_selection_changed(self) -> None:
        self.selection_changed.emit(self._selected_paths())

    def _get_current_ordered_paths(self) -> list[Path]:
        """Returns all paths currently in model order."""
        paths: list[Path] = []
        for record in self.model.tagged_records:
            paths.append(Path(record["path"]))
        for record in self.model.untagged_records:
            paths.append(Path(record["path"]))
        return paths

    def _clear_viewer_reference(self, viewer: ImageViewerDialog) -> None:
        if self._viewer is viewer:
            self._viewer = None

    def _close_viewer(self) -> None:
        viewer = self._viewer
        self._viewer = None
        if viewer is None:
            return
        try:
            viewer.close()
        except RuntimeError:
            return

    def _open_file(self, index: QModelIndex) -> None:
        raw_path = index.data(Qt.ItemDataRole.UserRole)
        if not raw_path:
            return

        path = Path(raw_path)
        try:
            ensure_current_regular_file(path, self._identity_for_path(path))
        except FileOperationError as exc:
            QMessageBox.warning(self, "Open file", str(exc))
            return

        if path.suffix.lower() in get_supported_image_suffixes():
            ordered_paths = self._get_current_ordered_paths()
            self._close_viewer()
            viewer = ImageViewerDialog(ordered_paths, path, self)
            self._viewer = viewer
            viewer.destroyed.connect(lambda _obj=None, viewer=viewer: self._clear_viewer_reference(viewer))
            viewer.show()
            return

        open_path_with_shell(self, path)

    def _show_context_menu(self, position) -> None:
        index = self.tree.indexAt(position)
        if not index.isValid() or not index.data(Qt.ItemDataRole.UserRole):
            return

        if not self.tree.selectionModel().isSelected(index):
            self.tree.clearSelection()
            self.tree.selectionModel().select(index, QItemSelectionModel.SelectionFlag.Select)

        menu = QMenu(self)
        edit_action = QAction("Edit tags", self)
        rename_action = QAction("Rename", self)
        delete_action = QAction("Delete", self)
        open_location_action = QAction("Open file location", self)

        edit_action.triggered.connect(self._edit_tags)
        rename_action.triggered.connect(self._rename_files)
        delete_action.triggered.connect(self._delete_files)
        open_location_action.triggered.connect(self._open_file_location)

        menu.addAction(edit_action)
        menu.addSeparator()
        menu.addAction(rename_action)
        menu.addAction(delete_action)
        menu.addSeparator()
        menu.addAction(open_location_action)
        menu.exec(self.tree.viewport().mapToGlobal(position))

    def _edit_tags(self) -> None:
        selected_paths = self._selected_paths()
        if not selected_paths:
            QMessageBox.information(self, "Edit tags", "Please select one or more files to edit tags.")
            return

        available_tags = set()
        for record in self._records_by_path.values():
            available_tags.update(normalize_tags(record.get("tags", [])))
        available_tags = sorted(list(available_tags), key=tag_sort_key)

        tag_counts: dict[str, int] = {}
        for path in selected_paths:
            for tag in self._tags_for_path(path):
                tag_counts[tag] = tag_counts.get(tag, 0) + 1

        total_files = len(selected_paths)
        initial_states: dict[str, int] = {}
        for tag, count in tag_counts.items():
            initial_states[tag] = 1 if count == total_files else 2

        title_info = f"File: {selected_paths[0].name}" if total_files == 1 else f"Selected files: {total_files}"
        dialog = TagEditDialog(title_info, initial_states, available_tags, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        tags_to_add, tags_to_remove = dialog.get_actions()
        operations: list[RenameOperation] = []
        reserved_targets: set[str] = set()
        errors: list[str] = []

        for source_path in selected_paths:
            existing_tags = set(self._tags_for_path(source_path))
            final_tags = sort_tags((existing_tags - tags_to_remove) | tags_to_add)
            operation, error_message = self._build_tag_rename_operation(
                source_path,
                final_tags,
                reserved_targets,
            )
            if error_message:
                errors.append(f"{source_path.name}: {error_message}")
            elif operation is not None:
                operations.append(operation)
                if path_key(operation.source) != path_key(operation.target):
                    reserved_targets.add(path_key(operation.target))

        if errors:
            details = "\n".join(errors[:8])
            more = f"\n... and {len(errors) - 8} more file(s)." if len(errors) > 8 else ""
            QMessageBox.warning(self, "Batch edit cancelled", f"{details}{more}")
            return

        if not operations:
            return

        try:
            execute_rename_plan(operations)
        except BatchRenameError as exc:
            message = str(exc)
            if exc.rollback_errors:
                message += "\n\nRollback warnings:\n" + "\n".join(exc.rollback_errors[:8])
            QMessageBox.warning(self, "Batch edit failed", message)
            return

        self.preserve_scroll_on_next_reload()
        self.files_changed.emit()

    def _rename_files(self) -> None:
        selected_paths = self._selected_paths()
        if not selected_paths:
            return
        if len(selected_paths) > 1:
            QMessageBox.information(self, "Rename", "Please select a single file to rename.")
            return

        source_path = selected_paths[0]
        new_name, ok = QInputDialog.getText(self, "Rename", "New filename:", text=source_path.name)
        if not ok:
            return

        new_name = new_name.strip()
        if not new_name or new_name == source_path.name:
            return

        try:
            target_path = build_target_path(source_path, new_name)
            self._safe_rename_with_collision_prompt(source_path, target_path)
        except (FilenameValidationError, FileOperationError) as exc:
            QMessageBox.warning(self, "Rename", str(exc))
            return

        self.preserve_scroll_on_next_reload()
        self.files_changed.emit()

    def _delete_files(self) -> None:
        selected_paths = self._selected_paths()
        if not selected_paths:
            return

        names = "\n".join(path.name for path in selected_paths[:8])
        more = f"\n... and {len(selected_paths) - 8} more file(s)." if len(selected_paths) > 8 else ""
        reply = QMessageBox.question(
            self,
            "Delete",
            f"Delete selected file(s)?\n\n{names}{more}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        errors: list[str] = []
        deleted = 0
        for path in selected_paths:
            try:
                safe_delete(path, self._identity_for_path(path))
                deleted += 1
            except FileOperationError as exc:
                errors.append(f"{path.name}: {exc}")

        if deleted:
            self.preserve_scroll_on_next_reload()
            self.files_changed.emit()
        if errors:
            QMessageBox.warning(self, "Delete", "\n".join(errors[:8]))

    def _tags_for_path(self, source_path: Path) -> list[str]:
        record = self._records_by_path.get(str(source_path), {})
        raw_tags = record.get("tags", FileScanner.parse_tags(source_path.name))
        if not isinstance(raw_tags, list):
            raw_tags = FileScanner.parse_tags(source_path.name)
        return normalize_tags(raw_tags)

    def _rename_with_tags(self, source_path: Path, updated_tags: list[str]) -> tuple[bool, str | None]:
        try:
            target_path = build_target_path(source_path, self._build_filename(source_path, updated_tags))
            if os.fspath(source_path) == os.fspath(target_path):
                return False, None
            self._safe_rename_with_collision_prompt(source_path, target_path)
        except (TagValidationError, FilenameValidationError, FileOperationError) as exc:
            return False, str(exc)
        return True, None

    def _build_tag_rename_operation(
        self,
        source_path: Path,
        updated_tags: list[str],
        reserved_targets: set[str],
    ) -> tuple[RenameOperation | None, str | None]:
        try:
            target_path = build_target_path(source_path, self._build_filename(source_path, updated_tags))
            if os.fspath(source_path) == os.fspath(target_path):
                return None, None
            operation = RenameOperation(source_path, target_path, self._identity_for_path(source_path))
            try:
                preflight_rename(operation, reserved_targets)
            except TargetExistsError:
                suggested_path = unique_path(target_path, reserved_targets)
                reply = QMessageBox.question(
                    self,
                    "Tag conflict",
                    f"Applying tags creates a conflict: '{target_path.name}' already exists.\n\n"
                    f"Save as '{suggested_path.name}'?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return None, f"Skipped (user rejected auto-rename to {suggested_path.name})"
                operation = RenameOperation(source_path, suggested_path, self._identity_for_path(source_path))
                preflight_rename(operation, reserved_targets)
            return operation, None
        except (TagValidationError, FilenameValidationError, FileOperationError) as exc:
            return None, str(exc)

    def _safe_rename_with_collision_prompt(self, source_path: Path, target_path: Path) -> Path:
        operation = RenameOperation(source_path, target_path, self._identity_for_path(source_path))
        try:
            return safe_rename(operation)
        except TargetExistsError:
            suggested_path = unique_path(target_path)
            reply = QMessageBox.question(
                self,
                "File already exists",
                f"A file named '{target_path.name}' already exists.\n\n"
                f"Would you like to rename it to '{suggested_path.name}' instead?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if reply != QMessageBox.StandardButton.Yes:
                raise FileOperationError("Rename cancelled because the target already exists.")
            return safe_rename(RenameOperation(source_path, suggested_path, self._identity_for_path(source_path)))

    def _build_filename(self, path: Path, tags: list[str]) -> str:
        return build_tagged_filename(path, tags, self.tags_at_start)

    def _get_unique_path(self, target_path: Path) -> Path:
        if not target_path.exists():
            return target_path
        return unique_path(target_path)

    def _open_file_location(self) -> None:
        selected_paths = self._selected_paths()
        if not selected_paths:
            return

        path = selected_paths[0]
        try:
            ensure_current_regular_file(path, self._identity_for_path(path))
            reveal_path_in_explorer(self, path)
        except FileOperationError as exc:
            QMessageBox.warning(self, "Open location", f"Unable to open file location:\n{exc}")

    def _record_for_path(self, path: Path) -> dict | None:
        return self._records_by_path.get(str(path))

    def _identity_for_path(self, path: Path) -> FileIdentity | None:
        return FileIdentity.from_record(self._record_for_path(path))
