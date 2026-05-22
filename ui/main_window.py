"""Main application window for TagExplorer."""

from __future__ import annotations

import os
import gc
from pathlib import Path
from typing import Any, Iterator

import pywinstyles

from PyQt6.QtCore import QDir, QThread, Qt, QStandardPaths, QStorageInfo, QTimer, pyqtSignal
from PyQt6.QtGui import QFileSystemModel, QPainter
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QStyle,
    QStyleOption,
    QTreeView,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QSplitterHandle,
)

from core.cache import FileCache
from core.filter_engine import filter_files
from core.scanner import FileScanner
from core.tags import normalize_tags, sort_tags
from ui.file_list import FileListWidget
from ui.file_tile import FileTileWidget
from ui.preview_panel import PreviewPanel
from ui.tag_panel import TagPanel
from ui.type_filter import CollapsibleTypeFilter
from ui.updater import UpdateChecker, UpdateDialog


class ScanWorker(QThread):
    """Background scanner that updates cache and emits records."""

    finished_scan = pyqtSignal(int, str, list)
    failed = pyqtSignal(int, str, str)
    SCAN_DB_BATCH_SIZE = 1000

    def __init__(
        self,
        scan_id: int,
        folder: Path,
        force_rescan: bool = False,
        recursive_scan: bool = False,
    ) -> None:
        super().__init__()
        self.scan_id = scan_id
        self.folder = folder
        self.force_rescan = force_rescan
        self.scanner = FileScanner()
        self.recursive_scan = recursive_scan

    def _should_cancel(self) -> bool:
        return self.isInterruptionRequested()

    def run(self) -> None:
        folder_key = self.folder.resolve().as_posix()
        cache: FileCache | None = None
        try:
            cache = FileCache()
            if self.force_rescan:
                if self.recursive_scan:
                    cache.clear_tree(self.folder)
                else:
                    cache.clear_folder(self.folder)

            if self._should_cancel():
                return

            current_paths: set[str] = set()
            batches = (
                self.scanner.iter_deep_scan_directory(
                    self.folder,
                    force_rescan=self.force_rescan,
                    should_cancel=self._should_cancel,
                    batch_size=self.SCAN_DB_BATCH_SIZE,
                )
                if self.recursive_scan
                else self.scanner.iter_scan_directory(
                    self.folder,
                    should_cancel=self._should_cancel,
                    batch_size=self.SCAN_DB_BATCH_SIZE,
                )
            )

            for batch in batches:
                if self._should_cancel():
                    return
                to_update: list[dict[str, Any]] = []
                for record in batch:
                    if self._should_cancel():
                        return
                    normalized_path = Path(record["path"]).resolve().as_posix()
                    record["path"] = normalized_path
                    current_paths.add(normalized_path)
                    to_update.append(record)
                cache.update_files_batch(to_update)

            if self._should_cancel():
                return

            if self.recursive_scan:
                cache.delete_missing_tree(self.folder, current_paths)
                records = cache.get_files_under(self.folder)
            else:
                cache.delete_missing(self.folder, current_paths)
                records = cache.get_all_files(self.folder)
            if not self._should_cancel():
                self.finished_scan.emit(self.scan_id, folder_key, records)
        except Exception as exc:  # noqa: BLE001
            if not self._should_cancel():
                self.failed.emit(self.scan_id, folder_key, str(exc))
        finally:
            if cache is not None:
                cache.close()


class VisibleSplitterHandle(QSplitterHandle):
    """Theme-aware splitter handle with a subtle visible separator."""

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        option = QStyleOption()
        option.initFrom(self)
        self.style().drawPrimitive(QStyle.PrimitiveElement.PE_Widget, option, painter, self)

        palette = self.palette()
        base_color = Qt.GlobalColor.transparent
        line_color = palette.color(palette.ColorRole.Mid)
        line_color.setAlpha(100)
        grip_color = palette.color(palette.ColorRole.Dark)
        grip_color.setAlpha(150)

        if self.orientation() == Qt.Orientation.Horizontal:
            center_x = self.rect().center().x()
            painter.fillRect(self.rect(), base_color)
            painter.setPen(line_color)
            painter.drawLine(center_x, 0, center_x, self.height())

            dot_radius = 1
            spacing = 4
            center_y = self.rect().center().y()
            painter.setBrush(grip_color)
            painter.setPen(Qt.PenStyle.NoPen)
            for offset in (-spacing, 0, spacing):
                painter.drawEllipse(center_x - dot_radius, center_y + offset - dot_radius, 3, 3)
        else:
            center_y = self.rect().center().y()
            painter.fillRect(self.rect(), base_color)
            painter.setPen(line_color)
            painter.drawLine(0, center_y, self.width(), center_y)

            dot_radius = 1
            spacing = 4
            center_x = self.rect().center().x()
            painter.setBrush(grip_color)
            painter.setPen(Qt.PenStyle.NoPen)
            for offset in (-spacing, 0, spacing):
                painter.drawEllipse(center_x + offset - dot_radius, center_y - dot_radius, 3, 3)

        painter.end()


class VisibleSplitter(QSplitter):
    """Splitter with custom theme-aware handles for better discoverability."""

    def createHandle(self) -> QSplitterHandle:
        return VisibleSplitterHandle(self.orientation(), self)


class MainWindow(QMainWindow):
    """Top-level window composing navigation, tag filtering, and preview panels."""

    PLACE_PATH_ROLE = Qt.ItemDataRole.UserRole
    PLACE_KIND_ROLE = Qt.ItemDataRole.UserRole + 1

    QUICK_ACCESS_LOCATIONS = (
        QStandardPaths.StandardLocation.DesktopLocation,
        QStandardPaths.StandardLocation.DocumentsLocation,
        QStandardPaths.StandardLocation.DownloadLocation,
        QStandardPaths.StandardLocation.PicturesLocation,
        QStandardPaths.StandardLocation.MusicLocation,
        QStandardPaths.StandardLocation.MoviesLocation,
    )

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("TagExplorer")
        self.resize(1300, 760)

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        if os.name == "nt":
            try:
                pywinstyles.apply_style(self, "acrylic")
            except Exception:
                try:
                    pywinstyles.apply_style(self, "mica")
                except Exception:
                    pass

            try:
                pywinstyles.change_header_color(self, color="#1e1e1e")
            except Exception:
                pass

        self.cache = FileCache()
        self._pending_cache_warnings = list(self.cache.security_warnings)
        self.current_folder = Path.home().resolve()
        self.all_records: list[dict[str, Any]] = []
        self._filtered_tagged: list[dict[str, Any]] = []
        self._filtered_untagged: list[dict[str, Any]] = []
        self._list_view_dirty = True
        self._tile_view_dirty = True
        self._current_view_mode = "list"
        self._pending_view_scroll_restore: tuple[str, int] | None = None
        self.scan_worker: ScanWorker | None = None
        self._scan_generation = 0
        self._pending_scan: tuple[int, Path, bool, bool] | None = None
        self._closing = False
        self._update_checked = False
        self._manual_update_check = False
        self.update_checker: UpdateChecker | None = None
        self.update_dialog: UpdateDialog | None = None
        self.recursive_scan_enabled = False
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(150)
        self._filter_timer.timeout.connect(self._apply_filter)

        self._build_menu()
        self._build_ui()
        self._connect_signals()

        self.setStatusBar(QStatusBar())
        self._populate_places_tree()
        self._set_navigation_root(self.current_folder)
        self._sync_places_selection()
        self._show_cache_warnings()
        self.refresh_folder(force_rescan=False)

    def _build_menu(self) -> None:
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu("File")
        open_action = file_menu.addAction("Open Folder")
        open_action.triggered.connect(self.open_folder_dialog)

        view_menu = menu_bar.addMenu("View")
        refresh_action = view_menu.addAction("Refresh")
        refresh_action.setShortcut("F5")
        refresh_action.triggered.connect(lambda: self.refresh_folder(force_rescan=True))

        self.list_view_action = view_menu.addAction("List")
        self.list_view_action.setCheckable(True)
        self.list_view_action.setChecked(True)
        self.list_view_action.triggered.connect(lambda: self._set_view_mode("list"))

        self.tile_view_action = view_menu.addAction("Tiles")
        self.tile_view_action.setCheckable(True)
        self.tile_view_action.triggered.connect(lambda: self._set_view_mode("tiles"))

        options_menu = menu_bar.addMenu("Options")
        self.tags_at_start_action = options_menu.addAction("Tags at the beginning")
        self.tags_at_start_action.setCheckable(True)
        self.tags_at_start_action.setChecked(False)
        self.tags_at_start_action.triggered.connect(self._toggle_tags_position)

        help_menu = menu_bar.addMenu("Help")
        check_updates_action = help_menu.addAction("Check for Updates")
        check_updates_action.triggered.connect(lambda: self.check_for_updates(manual=True))

        about_action = help_menu.addAction("About")
        about_action.triggered.connect(self.show_about)

    def _build_ui(self) -> None:
        splitter = VisibleSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(10)
        self.setCentralWidget(splitter)

        left_panel = QWidget()
        left_panel.setObjectName("LeftPanel")
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(10, 10, 10, 10)
        left_layout.setSpacing(8)
        buttons_layout = QHBoxLayout()

        self.open_folder_button = QPushButton("Open Folder")
        self.open_folder_button.clicked.connect(self.open_folder_dialog)
        buttons_layout.addWidget(self.open_folder_button)

        self.recursive_scan_button = QPushButton("Scan Folder Recursively")
        self.recursive_scan_button.clicked.connect(self.open_recursive_folder_dialog)
        buttons_layout.addWidget(self.recursive_scan_button)

        self.rescan_button = QPushButton("Rescan Current Folder")
        self.rescan_button.clicked.connect(lambda: self.refresh_folder(force_rescan=True))
        buttons_layout.addWidget(self.rescan_button)

        self.recursive_scan_switch = QCheckBox("Recursive mode")
        self.recursive_scan_switch.setChecked(False)
        self.recursive_scan_switch.toggled.connect(self._toggle_recursive_scan_mode)

        left_layout.addLayout(buttons_layout)
        left_layout.addWidget(self.recursive_scan_switch)
        self.places_tree = QTreeWidget()
        self.places_tree.setObjectName("PlacesTree")
        self.places_tree.setHeaderHidden(True)
        self.places_tree.setUniformRowHeights(True)
        self.places_tree.setRootIsDecorated(True)
        self.places_tree.setAnimated(True)
        self.places_tree.setIndentation(10)

        self.folder_tree = QTreeView()
        self.folder_tree.setObjectName("FolderTree")
        self.folder_model = QFileSystemModel()
        self.folder_model.setFilter(QDir.Filter.AllDirs | QDir.Filter.NoDotAndDotDot)
        self.folder_model.setRootPath(str(self.current_folder))
        self.folder_tree.setModel(self.folder_model)
        self.folder_tree.setRootIndex(self.folder_model.index(str(self.current_folder)))
        self.folder_tree.setHeaderHidden(True)
        self.folder_tree.setUniformRowHeights(True)
        for col in range(1, self.folder_model.columnCount()):
            self.folder_tree.hideColumn(col)

        self.places_tree.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.folder_tree.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.places_tree.viewport().setAutoFillBackground(False)
        self.folder_tree.viewport().setAutoFillBackground(False)

        self.left_nav_splitter = QSplitter(Qt.Orientation.Vertical)
        self.left_nav_splitter.setObjectName("LeftNavSplitter")
        self.left_nav_splitter.setHandleWidth(6)
        self.left_nav_splitter.addWidget(self.places_tree)
        self.left_nav_splitter.addWidget(self.folder_tree)
        self.left_nav_splitter.setSizes([280, 360])

        left_layout.addWidget(self.left_nav_splitter, 1)

        center_panel = QWidget()
        center_panel.setObjectName("CenterPanel")
        center_layout = QVBoxLayout(center_panel)
        self.tag_panel = TagPanel()
        self.type_filter = CollapsibleTypeFilter()
        self.file_list = FileListWidget()
        self.file_tile_widget = FileTileWidget()
        self.file_list.tags_at_start = self.tags_at_start_action.isChecked()
        self.file_tile_widget.tags_at_start = self.tags_at_start_action.isChecked()
        self.view_stack = QStackedWidget()
        self.view_stack.addWidget(self.file_list)
        self.view_stack.addWidget(self.file_tile_widget)
        center_layout.addWidget(self.tag_panel, 2)
        center_layout.addWidget(self.type_filter, 0)
        center_layout.addWidget(self.view_stack, 5)

        self.preview_panel = PreviewPanel()
        self.preview_panel.setObjectName("PreviewPanel")

        splitter.addWidget(left_panel)
        splitter.addWidget(center_panel)
        splitter.addWidget(self.preview_panel)
        splitter.setSizes([260, 720, 320])
        splitter.setCollapsible(2, True)

        glass_style = """
            QMainWindow, QSplitter {
                background: transparent;
            }

            #LeftPanel, #CenterPanel, #PreviewPanel {
                background-color: rgba(24, 24, 24, 56);
                border: 1px solid rgba(255, 255, 255, 38);
                border-radius: 10px;
            }

            #LeftNavSplitter {
                background: transparent;
            }

            #LeftNavSplitter::handle {
                background-color: rgba(255, 255, 255, 40);
                border-radius: 2px;
            }

            #PlacesTree, #FolderTree {
                background-color: rgba(255, 255, 255, 14);
                border: 1px solid rgba(255, 255, 255, 26);
                border-radius: 8px;
                color: #f4f4f4;
                padding: 4px;
            }

            QTreeView::item, QTreeWidget::item {
                padding: 3px;
                border-radius: 4px;
            }

            QTreeView::item:selected, QTreeWidget::item:selected {
                background-color: rgba(130, 180, 255, 70);
                color: #ffffff;
            }

            QPushButton {
                background-color: rgba(255, 255, 255, 26);
                border: 1px solid rgba(255, 255, 255, 70);
                border-radius: 4px;
                padding: 4px;
                color: white;
            }

            QPushButton:hover {
                background-color: rgba(255, 255, 255, 46);
            }

            QMenuBar {
                background-color: rgba(30, 30, 30, 200);
                color: #f4f4f4;
                font-weight: normal;
            }

            QMenuBar::item:selected {
                background-color: rgba(255, 255, 255, 40);
            }

            QMenu {
                background-color: #2a2a2a;
                color: #f4f4f4;
                border: 1px solid rgba(255, 255, 255, 40);
                font-weight: normal;
            }

            QMenu::item {
                padding: 6px 28px 6px 12px;
            }

            QMenu::item:selected {
                background-color: rgba(130, 180, 255, 100);
            }

            QCheckBox {
                color: #f4f4f4;
                font-weight: normal;
            }

            QLabel {
                color: #f4f4f4;
                font-weight: normal;
            }

            QRadioButton {
                color: #f4f4f4;
                font-weight: normal;
            }

            QStatusBar {
                color: #f4f4f4;
                font-weight: normal;
            }
        """

        self.setStyleSheet(glass_style)

    def _connect_signals(self) -> None:
        self.places_tree.itemClicked.connect(self._on_place_clicked)
        self.folder_tree.clicked.connect(self._on_folder_clicked)
        self.tag_panel.filters_changed.connect(self._schedule_filter)
        self.type_filter.filters_changed.connect(self._schedule_filter)
        self.file_list.selection_changed.connect(self.preview_panel.show_selection)
        self.file_tile_widget.selection_changed.connect(self.preview_panel.show_selection)
        self.file_list.files_changed.connect(self._on_files_changed)
        self.file_tile_widget.files_changed.connect(self._on_files_changed)

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        if not self._update_checked:
            self._update_checked = True
            QTimer.singleShot(1500, lambda: self.check_for_updates(manual=False))

    def _toggle_tags_position(self, checked: bool) -> None:
        self.file_list.tags_at_start = checked
        self.file_tile_widget.tags_at_start = checked

    def _populate_places_tree(self) -> None:
        self.places_tree.blockSignals(True)
        self.places_tree.clear()

        quick_access = self._create_group_item(
            "Quick access",
            QStyle.StandardPixmap.SP_DirHomeIcon,
        )
        self.places_tree.addTopLevelItem(quick_access)

        for title, path, kind in self._iter_quick_access_places():
            quick_access.addChild(self._create_place_item(title, path, kind))

        this_pc = self._create_group_item(
            "This PC",
            QStyle.StandardPixmap.SP_ComputerIcon,
        )
        self.places_tree.addTopLevelItem(this_pc)

        for title, path, kind in self._iter_drive_places():
            this_pc.addChild(self._create_place_item(title, path, kind))

        quick_access.setExpanded(True)
        this_pc.setExpanded(True)
        self.places_tree.blockSignals(False)

    def _create_group_item(
        self,
        title: str,
        icon_kind: QStyle.StandardPixmap,
    ) -> QTreeWidgetItem:
        item = QTreeWidgetItem([title])
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        item.setIcon(0, self.style().standardIcon(icon_kind))
        return item

    def _create_place_item(self, title: str, path: str, kind: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem([title])
        item.setData(0, self.PLACE_PATH_ROLE, path)
        item.setData(0, self.PLACE_KIND_ROLE, kind)
        item.setToolTip(0, path)
        item.setIcon(0, self._icon_for_place(Path(path), kind))
        return item

    def _icon_for_place(self, path: Path, kind: str):
        if kind == "drive":
            return self.style().standardIcon(QStyle.StandardPixmap.SP_DriveHDIcon)

        index = self.folder_model.index(str(path))
        if index.isValid():
            return self.folder_model.fileIcon(index)

        return self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon)

    def _iter_quick_access_places(self) -> Iterator[tuple[str, str, str]]:
        seen: set[str] = set()

        for location in self.QUICK_ACCESS_LOCATIONS:
            path = QStandardPaths.writableLocation(location)
            if not path:
                continue

            place_path = Path(path)
            if not place_path.exists():
                continue

            normalized = self._normalized_path(place_path)
            if normalized in seen:
                continue

            seen.add(normalized)
            title = QStandardPaths.displayName(location) or place_path.name or str(place_path)
            yield title, str(place_path.resolve()), "folder"

    def _iter_drive_places(self) -> Iterator[tuple[str, str, str]]:
        seen: set[str] = set()

        for volume in QStorageInfo.mountedVolumes():
            if not volume.isValid() or not volume.isReady():
                continue

            root_path = volume.rootPath()
            if not root_path:
                continue

            normalized = self._normalized_path(Path(root_path))
            if normalized in seen:
                continue

            seen.add(normalized)
            root_label = Path(root_path).drive or root_path
            display_name = volume.displayName().strip()

            if display_name and display_name.lower() not in root_label.lower():
                title = f"{display_name} ({root_label})"
            else:
                title = display_name or root_label

            yield title, str(Path(root_path).resolve()), "drive"

    def _on_place_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        raw_path = item.data(0, self.PLACE_PATH_ROLE)
        if not raw_path:
            return

        selected_path = Path(str(raw_path))
        if selected_path.is_dir():
            self._set_current_folder(selected_path, update_navigation_root=True)

    def _on_folder_clicked(self, index) -> None:
        path = Path(self.folder_model.filePath(index))
        if path.is_dir():
            self._set_current_folder(path, update_navigation_root=False)

    def _set_current_folder(self, folder: Path, *, update_navigation_root: bool) -> None:
        resolved_folder = folder.resolve()
        self.current_folder = resolved_folder
        self._pending_view_scroll_restore = None

        # --- ЭТАП 4: Экстренная выгрузка памяти ---
        if hasattr(self, "file_tile_widget"):
            self.file_tile_widget.thumb_mgr.cancel_all()
            self.file_tile_widget._sec_tagged.model._thumb_cache._cache.clear()
            self.file_tile_widget._sec_untagged.model._thumb_cache._cache.clear()

        gc.collect()
        # -----------------------------------------

        if update_navigation_root:
            self._set_navigation_root(resolved_folder)

        self._sync_places_selection()
        self.refresh_folder(force_rescan=False)

    def _set_navigation_root(self, folder: Path) -> None:
        folder_str = str(folder)
        self.folder_model.setRootPath(folder_str)
        self.folder_tree.setRootIndex(self.folder_model.index(folder_str))

    def _sync_places_selection(self) -> None:
        best_item: QTreeWidgetItem | None = None
        best_length = -1

        current_path = self._normalized_path(self.current_folder)

        iterator_root = self.places_tree.invisibleRootItem()
        for top_index in range(iterator_root.childCount()):
            group_item = iterator_root.child(top_index)
            for child_index in range(group_item.childCount()):
                item = group_item.child(child_index)
                item_path = item.data(0, self.PLACE_PATH_ROLE)
                if not item_path:
                    continue

                normalized_item_path = self._normalized_path(Path(str(item_path)))
                if not self._is_same_or_parent(normalized_item_path, current_path):
                    continue

                if len(normalized_item_path) > best_length:
                    best_item = item
                    best_length = len(normalized_item_path)

        self.places_tree.blockSignals(True)
        self.places_tree.clearSelection()
        if best_item is not None:
            best_item.setSelected(True)
            self.places_tree.setCurrentItem(best_item)
        self.places_tree.blockSignals(False)

    def _normalized_path(self, path: Path) -> str:
        resolved = str(path.resolve())
        normalized = os.path.normpath(resolved)
        if os.name == "nt":
            return normalized.casefold()
        return normalized

    def _is_same_or_parent(self, parent_path: str, child_path: str) -> bool:
        try:
            return os.path.commonpath([parent_path, child_path]) == parent_path
        except ValueError:
            return False

    def open_folder_dialog(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Open Folder", str(self.current_folder))
        if not selected:
            return

        self._set_current_folder(Path(selected), update_navigation_root=True)

    def open_recursive_folder_dialog(self) -> None:
        """Pick a folder and immediately run recursive scan for it."""
        selected = QFileDialog.getExistingDirectory(
            self,
            "Scan Folder Recursively",
            str(self.current_folder),
        )
        if not selected:
            return

        self.recursive_scan_switch.blockSignals(True)
        self.recursive_scan_switch.setChecked(True)
        self.recursive_scan_switch.blockSignals(False)
        self.recursive_scan_enabled = True
        self._update_rescan_button_label()
        self._set_current_folder(Path(selected), update_navigation_root=True)

    def refresh_folder(self, force_rescan: bool) -> None:
        """Scan current folder and update UI with fresh records."""
        if self._closing:
            return

        self._scan_generation += 1
        scan_id = self._scan_generation
        folder = self.current_folder
        recursive_scan = self.recursive_scan_enabled

        if self.scan_worker and self.scan_worker.isRunning():
            self._pending_scan = (scan_id, folder, force_rescan, recursive_scan)
            self.scan_worker.requestInterruption()
            self.statusBar().showMessage(f"Waiting for previous scan to stop: {folder}")
            return

        self._start_scan(scan_id, folder, force_rescan, recursive_scan)

    def _start_scan(
        self,
        scan_id: int,
        folder: Path,
        force_rescan: bool,
        recursive_scan: bool,
    ) -> None:
        """Start a scan for an immutable folder/mode snapshot."""
        if self._closing:
            return

        self._populate_places_tree()
        self._sync_places_selection()
        self.statusBar().showMessage(f"Scanning: {folder}")
        self.scan_worker = ScanWorker(
            scan_id,
            folder,
            force_rescan=force_rescan,
            recursive_scan=recursive_scan,
        )
        self.scan_worker.finished_scan.connect(self._on_scan_complete)
        self.scan_worker.failed.connect(self._on_scan_failed)
        self.scan_worker.finished.connect(
            lambda worker=self.scan_worker, worker_scan_id=scan_id: self._on_scan_worker_finished(
                worker_scan_id,
                worker,
            )
        )
        self.scan_worker.start()

    def _on_scan_complete(self, scan_id: int, folder_key: str, records: list[dict[str, Any]]) -> None:
        if self._closing or scan_id != self._scan_generation:
            return
        if folder_key != self.current_folder.resolve().as_posix():
            return

        self.all_records = records
        tags = sort_tags(
            tag
            for record in records
            for tag in normalize_tags(record.get("tags", []))
        )
        self.tag_panel.set_tags(tags)

    def _schedule_filter(self, *args) -> None:
        self._filter_timer.start()

    def _on_scan_failed(self, scan_id: int, folder_key: str, message: str) -> None:
        if self._closing or scan_id != self._scan_generation:
            return
        if folder_key != self.current_folder.resolve().as_posix():
            return

        QMessageBox.warning(self, "Scan error", f"Could not scan folder:\n{message}")
        self.statusBar().showMessage("Scan failed")

    def _on_scan_worker_finished(self, scan_id: int, worker: ScanWorker) -> None:
        if self.scan_worker is worker:
            self.scan_worker = None
        worker.deleteLater()

        if self._closing:
            return

        if self._pending_scan is not None:
            pending_scan = self._pending_scan
            self._pending_scan = None
            self._start_scan(*pending_scan)

    def _show_cache_warnings(self) -> None:
        if not self._pending_cache_warnings:
            return
        QMessageBox.warning(
            self,
            "Cache security warning",
            "\n\n".join(self._pending_cache_warnings),
        )
        self._pending_cache_warnings.clear()

    def _apply_filter(self, *args) -> None:
        selected_tags = self.tag_panel.selected_tags()
        excluded_tags = self.tag_panel.excluded_tags()
        mode = self.tag_panel.mode()
        selected_types = self.type_filter.selected_types()

        try:
            tagged, untagged = filter_files(self.all_records, selected_tags, mode, selected_types, excluded_tags)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Filter error", f"Could not apply filters:\n{exc}")
            return

        self._filtered_tagged = tagged
        self._filtered_untagged = untagged
        self._list_view_dirty = True
        self._tile_view_dirty = True
        self._refresh_active_file_view()
        self._restore_pending_view_scroll()
        displayed = len(tagged) + len(untagged)
        total = len(self.all_records)
        self.statusBar().showMessage(f"Displayed {displayed} of {total} files in {self.current_folder}")

    def _refresh_active_file_view(self) -> None:
        if self._current_view_mode == "tiles":
            if self._tile_view_dirty:
                self.file_tile_widget.set_files_batched(self._filtered_tagged, self._filtered_untagged)
                self._tile_view_dirty = False
            return

        if self._list_view_dirty:
            self.file_list.set_files(self._filtered_tagged, self._filtered_untagged)
            self._list_view_dirty = False

    def _toggle_recursive_scan_mode(self, enabled: bool) -> None:
        """Enable/disable safe recursive scan mode without changing default scanning."""
        self.recursive_scan_enabled = enabled
        self._update_rescan_button_label()
        mode = "recursive" if enabled else "standard"
        self.statusBar().showMessage(f"Scan mode: {mode}", 3000)
        self.refresh_folder(force_rescan=True)

    def _update_rescan_button_label(self) -> None:
        """Keep rescan action obvious for the active scan mode."""
        if self.recursive_scan_enabled:
            self.rescan_button.setText("Rescan Recursive Folder")
            self.rescan_button.setToolTip("Run full recursive rescan for the current folder")
            return

        self.rescan_button.setText("Rescan Current Folder")
        self.rescan_button.setToolTip("Rescan only direct files in the current folder")

    def _set_view_mode(self, mode: str) -> None:
        if mode == "tiles":
            self._current_view_mode = "tiles"
            self.view_stack.setCurrentWidget(self.file_tile_widget)
            self._sync_view_menu_state(list_checked=False, tiles_checked=True)
            self._refresh_active_file_view()
            self.statusBar().showMessage("Tile view enabled")
            return

        self._current_view_mode = "list"
        self.view_stack.setCurrentWidget(self.file_list)
        self._sync_view_menu_state(list_checked=True, tiles_checked=False)
        self._refresh_active_file_view()
        self.statusBar().showMessage("List view enabled")

    def _sync_view_menu_state(self, *, list_checked: bool, tiles_checked: bool) -> None:
        self.list_view_action.blockSignals(True)
        self.tile_view_action.blockSignals(True)
        self.list_view_action.setChecked(list_checked)
        self.tile_view_action.setChecked(tiles_checked)
        self.list_view_action.blockSignals(False)
        self.tile_view_action.blockSignals(False)

    def _capture_active_view_scroll(self) -> tuple[str, int]:
        if self._current_view_mode == "tiles":
            return ("tiles", self.file_tile_widget.scroll_position())
        return ("list", self.file_list.scroll_position())

    def _restore_pending_view_scroll(self) -> None:
        if self._pending_view_scroll_restore is None:
            return

        mode, value = self._pending_view_scroll_restore
        self._pending_view_scroll_restore = None
        if mode != self._current_view_mode:
            return

        if mode == "tiles":
            self.file_tile_widget.restore_scroll_position(value)
            return

        self.file_list.restore_scroll_position(value)

    def _on_files_changed(self) -> None:
        self._pending_view_scroll_restore = self._capture_active_view_scroll()
        self.refresh_folder(force_rescan=True)

    def show_about(self) -> None:
        version = QApplication.instance().applicationVersion()
        QMessageBox.about(
            self,
            "About TagExplorer",
            f"TagExplorer {version}\n\n"
            "A desktop file explorer that filters files by filename tags.",
        )

    def check_for_updates(self, manual: bool = False) -> None:
        """Check GitHub Releases for a newer TagExplorer build."""
        if self.update_checker is not None and self.update_checker.isRunning():
            if manual:
                self.statusBar().showMessage("Update check already in progress", 3000)
            return

        app = QApplication.instance()
        current_version = app.applicationVersion() if app is not None else "1.1.0"
        self._manual_update_check = manual
        if manual:
            self.statusBar().showMessage("Checking for updates...")

        self.update_checker = UpdateChecker(current_version)
        self.update_checker.update_available.connect(self.show_update_dialog)
        self.update_checker.no_update.connect(self._on_no_update_available)
        self.update_checker.check_failed.connect(self._on_update_check_failed)
        self.update_checker.finished.connect(self._clear_update_checker)
        self.update_checker.start()

    def show_update_dialog(self, version: str, download_url: str) -> None:
        self.statusBar().showMessage(f"Update available: {version}", 5000)
        self.update_dialog = UpdateDialog(version, download_url, self)
        self.update_dialog.exec()

    def _on_no_update_available(self, latest_tag: str) -> None:
        if self._manual_update_check:
            suffix = f" Latest release: {latest_tag}." if latest_tag else ""
            QMessageBox.information(self, "No Updates Available", f"TagExplorer is up to date.{suffix}")
            self.statusBar().showMessage("TagExplorer is up to date", 3000)

    def _on_update_check_failed(self, message: str) -> None:
        if self._manual_update_check:
            QMessageBox.warning(self, "Update Check Failed", message)
            self.statusBar().showMessage("Update check failed", 3000)

    def _clear_update_checker(self) -> None:
        if self.update_checker is not None:
            self.update_checker.deleteLater()
            self.update_checker = None

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._closing = True
        self._pending_scan = None
        self._filter_timer.stop()
        if self.update_checker and self.update_checker.isRunning():
            self.update_checker.requestInterruption()
            self.update_checker.wait(3000)
        if self.scan_worker and self.scan_worker.isRunning():
            self.scan_worker.requestInterruption()
            if not self.scan_worker.wait(10000):
                self._closing = False
                self.statusBar().showMessage("Waiting for scan to stop before closing")
                event.ignore()
                return
        if self.scan_worker is not None:
            self.scan_worker.deleteLater()
            self.scan_worker = None
        self.preview_panel.shutdown()
        self.file_tile_widget.shutdown()
        self.cache.close()
        super().closeEvent(event)
