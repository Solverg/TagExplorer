"""Virtualized tile view widget for displaying files with lazy thumbnails."""

from __future__ import annotations

import math
from collections import OrderedDict
import os
import time
from pathlib import Path

from PyQt6.QtCore import (
    QAbstractListModel, QFileInfo, QModelIndex, QObject, QPoint, QRect, QItemSelectionModel,
    QRunnable, QSize, Qt, QThreadPool, QTimer, pyqtSignal, QByteArray, QBuffer, QIODevice,
)
from PyQt6.QtGui import QAction, QFont, QImage, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QFileIconProvider, QFrame, QInputDialog, QLabel, QListView, QMenu, QMessageBox,
    QScrollArea, QStyle, QStyledItemDelegate, QVBoxLayout, QHBoxLayout, QWidget, QDialog,
    QSizePolicy, QPushButton,
)

from core.scanner import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS, FileScanner
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
from core.cache import FileCache
from core.media_safety import read_limited_image, read_limited_video_frame
from core.tags import TagValidationError, build_tagged_filename, normalize_tags, sort_tags, tag_sort_key
from ui.file_actions import open_path_with_shell, reveal_path_in_explorer
from ui.file_list import TagEditDialog
from ui.image_viewer import ImageViewerDialog, get_supported_image_suffixes

TILE_SIZE = 128
TILE_W = 140
TILE_H = 160
TEXT_H = 32
BATCH_SIZE = 50
MAX_IN_FLIGHT = 4
MAX_THUMB_QUEUE = 800
SCROLL_DEBOUNCE_MS = 60
BATCH_TIME_BUDGET_MS = 8
THUMB_ROLE = Qt.ItemDataRole.UserRole + 1

_icon_cache: dict[str, QPixmap] = {}


# ---------------------------------------------------------------------------
# LRU-кэш
# ---------------------------------------------------------------------------

class LRUCache:
    def __init__(self, max_size: int = 1000) -> None:
        self._cache: OrderedDict[str, QImage] = OrderedDict()
        self._max = max_size

    def get(self, key: str) -> QImage | None:
        if key not in self._cache:
            return None
        self._cache.move_to_end(key)
        return self._cache[key]

    def put(self, key: str, image: QImage) -> None:
        if key in self._cache:
            self._cache.move_to_end(key)
        self._cache[key] = image
        if len(self._cache) > self._max:
            self._cache.popitem(last=False)


def _get_icon_pixmap(path: str) -> QPixmap:
    suffix = Path(path).suffix.lower()
    if suffix not in _icon_cache:
        provider = QFileIconProvider()
        _icon_cache[suffix] = provider.icon(QFileInfo(path)).pixmap(TILE_SIZE, TILE_SIZE)
    return _icon_cache[suffix]


# ---------------------------------------------------------------------------
# Модель (только файлы, без секций)
# ---------------------------------------------------------------------------

class TileModel(QAbstractListModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._records: list[dict] = []
        self._path_to_row: dict[str, int] = {}
        self._thumb_cache = LRUCache(max_size=1000)
        self._dirty_rows: set[int] = set()

        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(100)
        self._flush_timer.timeout.connect(self._flush_dirty)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # type: ignore[override]
        if parent.isValid():
            return 0
        return len(self._records)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):  # type: ignore[override]
        if not index.isValid() or not 0 <= index.row() < len(self._records):
            return None
        record = self._records[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return record["filename"]
        if role == THUMB_ROLE:
            return self._thumb_cache.get(record["path"])
        if role == Qt.ItemDataRole.UserRole:
            return record
        return None

    def set_records(self, records: list[dict]) -> None:
        self.beginResetModel()
        self._records = records
        self._path_to_row = {r["path"]: i for i, r in enumerate(records)}
        self._dirty_rows.clear()
        self._flush_timer.stop()
        self.endResetModel()

    def append_records(self, records: list[dict]) -> None:
        if not records:
            return
        start = len(self._records)
        end = start + len(records) - 1
        self.beginInsertRows(QModelIndex(), start, end)
        for i, record in enumerate(records):
            self._path_to_row[record["path"]] = start + i
        self._records.extend(records)
        self.endInsertRows()

    def update_thumbnail(self, path: str, image: QImage) -> None:
        row = self._path_to_row.get(path)
        if row is None:
            return
        self._thumb_cache.put(path, image)
        self._dirty_rows.add(row)
        if not self._flush_timer.isActive():
            self._flush_timer.start()

    def _flush_dirty(self) -> None:
        if not self._dirty_rows:
            self._flush_timer.stop()
            return
        rows = sorted(self._dirty_rows)
        self._dirty_rows.clear()
        self._flush_timer.stop()
        start = prev = rows[0]
        for row in rows[1:]:
            if row == prev + 1:
                prev = row
                continue
            self.dataChanged.emit(self.index(start), self.index(prev), [THUMB_ROLE])
            start = prev = row
        self.dataChanged.emit(self.index(start), self.index(prev), [THUMB_ROLE])


# ---------------------------------------------------------------------------
# Воркер
# ---------------------------------------------------------------------------

class ThumbnailSignals(QObject):
    done = pyqtSignal(int, str, QImage)


class ThumbnailRunnable(QRunnable):
    def __init__(self, generation: int, path: str):
        super().__init__()
        self.generation = generation
        self.path = path
        self._cancelled = False
        self.signals = ThumbnailSignals()
        self.setAutoDelete(False)

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        if self._cancelled:
            return
        suffix = Path(self.path).suffix.lower()
        image = None
        if suffix in VIDEO_EXTENSIONS:
            image = self._video_thumb()
        elif suffix in IMAGE_EXTENSIONS:
            image = self._image_thumb()
        if not self._cancelled:
            self.signals.done.emit(self.generation, self.path, image or QImage())

    def _image_thumb(self) -> QImage | None:
        try:
            return read_limited_image(self.path, QSize(TILE_SIZE, TILE_SIZE))
        except Exception:
            return None

    def _video_thumb(self) -> QImage | None:
        try:
            image, _duration = read_limited_video_frame(self.path, QSize(TILE_SIZE, TILE_SIZE))
            return image
        except Exception:
            return None


# ---------------------------------------------------------------------------
# Менеджер превью
# ---------------------------------------------------------------------------

class ThumbnailManager:
    def __init__(self, model: TileModel):
        self._model = model
        self.pool = QThreadPool()
        self.pool.setMaxThreadCount(max(2, min(MAX_IN_FLIGHT, (os.cpu_count() or 4) // 2)))
        self.pool.setExpiryTimeout(5000)
        self._queue: OrderedDict[str, bool] = OrderedDict()
        self._in_flight: set[str] = set()
        self._active_workers: dict[str, ThumbnailRunnable] = {}
        self._generation = 0
        self._shutdown = False
        self.cache = FileCache()
        self._pending_thumbnail_blobs: dict[str, bytes] = {}
        self._thumbnail_flush_timer = QTimer()
        self._thumbnail_flush_timer.setSingleShot(True)
        self._thumbnail_flush_timer.setInterval(300)
        self._thumbnail_flush_timer.timeout.connect(self._flush_thumbnail_store)

    def request(self, path: str, priority: bool = False) -> None:
        del path, priority

    def cancel_all(self) -> None:
        self._generation += 1
        for worker in self._active_workers.values():
            worker.cancel()
        self._active_workers.clear()
        self._in_flight.clear()
        self._queue.clear()

    def clear(self) -> None:
        self.cancel_all()

    def shutdown(self, wait_ms: int = 5000) -> None:
        self._shutdown = True
        self.cancel_all()
        self._thumbnail_flush_timer.stop()
        self._flush_thumbnail_store()
        self.pool.clear()
        self.pool.waitForDone(wait_ms)
        self.cache.close()

    def _dispatch(self) -> None:
        if self._shutdown:
            return

        while self._queue and len(self._in_flight) < self.pool.maxThreadCount():
            path, _ = self._queue.popitem(last=False)
            worker = ThumbnailRunnable(self._generation, path)
            worker.signals.done.connect(self._on_done)
            self._in_flight.add(path)
            self._active_workers[path] = worker
            self.pool.start(worker)

    def _on_done(self, generation: int, path: str, image: QImage) -> None:
        if generation != self._generation:
            return

        self._in_flight.discard(path)
        self._active_workers.pop(path, None)
        if not self._shutdown and not image.isNull():
            self._model.update_thumbnail(path, image)
            self._queue_thumbnail_store(path, image)
        self._dispatch()

    def _queue_thumbnail_store(self, path: str, image: QImage) -> None:
        ba = QByteArray()
        buf = QBuffer(ba)
        if not buf.open(QIODevice.OpenModeFlag.WriteOnly):
            return
        if not image.save(buf, "JPG", 80):
            return
        self._pending_thumbnail_blobs[path] = bytes(ba.data())
        if len(self._pending_thumbnail_blobs) >= 100:
            self._flush_thumbnail_store()
        elif not self._thumbnail_flush_timer.isActive():
            self._thumbnail_flush_timer.start()

    def _flush_thumbnail_store(self) -> None:
        if not self._pending_thumbnail_blobs:
            return
        pending = list(self._pending_thumbnail_blobs.items())
        self._pending_thumbnail_blobs.clear()
        try:
            self.cache.update_thumbnails_batch(pending)
        except Exception:
            return


# ---------------------------------------------------------------------------
# Делегат плиток
# ---------------------------------------------------------------------------

class TileDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index) -> QSize:  # type: ignore[override]
        return QSize(TILE_W, TILE_H)

    def paint(self, painter: QPainter, option, index: QModelIndex) -> None:  # type: ignore[override]
        painter.save()
        rect = option.rect

        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(rect, option.palette.highlight())

        record = index.data(Qt.ItemDataRole.UserRole)
        path = record["path"] if record else ""

        thumb: QImage | None = index.data(THUMB_ROLE)
        pixmap = QPixmap.fromImage(thumb) if (thumb and not thumb.isNull()) else _get_icon_pixmap(path)

        image_zone_h = TILE_H - TEXT_H
        if pixmap.width() > TILE_W or pixmap.height() > image_zone_h:
            pixmap = pixmap.scaled(
                TILE_W, image_zone_h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )

        img_x = rect.left() + (TILE_W - pixmap.width()) // 2
        img_y = rect.top() + (image_zone_h - pixmap.height()) // 2
        painter.drawPixmap(img_x, img_y, pixmap)

        text = index.data(Qt.ItemDataRole.DisplayRole) or ""
        text_rect = QRect(rect.left() + 4, rect.bottom() - TEXT_H, TILE_W - 8, TEXT_H)
        painter.setPen(option.palette.text().color())
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
            option.fontMetrics.elidedText(text, Qt.TextElideMode.ElideRight, TILE_W - 8),
        )
        painter.restore()


# ---------------------------------------------------------------------------
# QListView с авторасчётом высоты по фактической сетке плиток
# ---------------------------------------------------------------------------

class _AutoHeightListView(QListView):
    """
    Высота рассчитывается по количеству колонок/строк текущей сетки.
    Это устраняет лишнее пустое пространство в конце секции, которое
    зависело от количества файлов и могло создавать «пустой» скролл.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setContentsMargins(0, 0, 0, 0)

    def sizeHint(self) -> QSize:  # type: ignore[override]
        return self._content_size()

    def minimumSizeHint(self) -> QSize:  # type: ignore[override]
        return QSize(0, 0)

    def _content_size(self) -> QSize:
        model = self.model()
        if model is None or model.rowCount() == 0:
            return QSize(self.width(), 0)

        fw = self.frameWidth()
        available_w = self.width() - (fw * 2)
        if available_w <= 0:
            return QSize(self.width(), 0)

        cols = max(1, available_w // TILE_W)
        rows = math.ceil(model.rowCount() / cols)
        total_h = rows * TILE_H + fw * 2
        return QSize(self.width(), total_h)


# ---------------------------------------------------------------------------
# Виджет одной секции: заголовок + разделитель + QListView с плитками
# ---------------------------------------------------------------------------

class SectionWidget(QWidget):
    """Заголовок секции + линия + QListView с плитками файлов с возможностью сворачивания."""

    file_double_clicked = pyqtSignal(str)   # path
    selection_changed = pyqtSignal(list)    # list[Path]
    context_requested = pyqtSignal(QPoint)

    def __init__(self, label: str, shared_thumb_mgr: ThumbnailManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._thumb_mgr = shared_thumb_mgr
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.is_collapsed = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._header = QWidget()
        self._header.setFixedHeight(36)
        self._header.setCursor(Qt.CursorShape.PointingHandCursor)

        header_layout = QHBoxLayout(self._header)
        header_layout.setContentsMargins(10, 0, 10, 0)
        header_layout.setSpacing(6)

        self._toggle_btn = QPushButton("▼")
        self._toggle_btn.setFlat(True)
        self._toggle_btn.setFixedSize(24, 24)
        self._toggle_btn.clicked.connect(self.toggle_collapse)

        lbl = QLabel(label)
        font = QFont(lbl.font())
        font.setBold(True)
        lbl.setFont(font)
        lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)

        header_layout.addWidget(self._toggle_btn)
        header_layout.addWidget(lbl)
        header_layout.addStretch(1)

        self._header.mousePressEvent = self._on_header_clicked

        self._line = QFrame()
        self._line.setFrameShape(QFrame.Shape.HLine)
        self._line.setFrameShadow(QFrame.Shadow.Sunken)
        self._line.setFixedHeight(2)

        self._model = TileModel(self)
        self._list = _AutoHeightListView(self)
        self._list.setModel(self._model)
        self._list.setItemDelegate(TileDelegate(self._list))
        self._list.setViewMode(QListView.ViewMode.IconMode)
        self._list.setResizeMode(QListView.ResizeMode.Adjust)
        self._list.setUniformItemSizes(True)
        self._list.setMovement(QListView.Movement.Static)
        self._list.setWrapping(True)
        self._list.setGridSize(QSize(TILE_W, TILE_H))
        self._list.setSpacing(8)
        self._list.setWordWrap(True)
        self._list.setMouseTracking(True)
        self._list.setSelectionMode(QListView.SelectionMode.ExtendedSelection)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        self._list.doubleClicked.connect(self._on_double_click)
        self._list.selectionModel().selectionChanged.connect(self._on_selection)
        self._list.customContextMenuRequested.connect(self._on_context_menu)

        self._model.rowsInserted.connect(lambda *_: QTimer.singleShot(0, self._update_list_height))
        self._model.modelReset.connect(lambda *_: QTimer.singleShot(0, self._update_list_height))

        self._empty_label = QLabel("No files")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet("color: gray; padding: 12px;")
        self._empty_label.hide()

        layout.addWidget(self._header)
        layout.addWidget(self._line)
        layout.addWidget(self._empty_label)
        layout.addWidget(self._list)

    @property
    def model(self) -> TileModel:
        return self._model

    @property
    def list_view(self) -> QListView:
        return self._list

    def _on_header_clicked(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle_collapse()

    def toggle_collapse(self) -> None:
        self.is_collapsed = not self.is_collapsed
        self._toggle_btn.setText("▶" if self.is_collapsed else "▼")
        self._update_list_height()

    def set_records(self, records: list[dict]) -> None:
        self._model.set_records(records)
        self._update_list_height()

    def append_records(self, records: list[dict]) -> None:
        self._model.append_records(records)
        self._update_list_height()

    def _update_list_height(self) -> None:
        has_records = self._model.rowCount() > 0

        if not has_records:
            self._list.hide()
            if self.is_collapsed:
                self._empty_label.hide()
                total_h = self._header.height() + self._line.height()
            else:
                self._empty_label.show()
                self._empty_label.setFixedHeight(self._empty_label.sizeHint().height())
                total_h = self._header.height() + self._line.height() + self._empty_label.height()
        else:
            self._list.show()
            self._empty_label.hide()
            self._empty_label.setFixedHeight(0)

            if self.is_collapsed:
                fw = self._list.frameWidth()
                one_row_h = TILE_H + fw * 2
                self._list.setFixedHeight(one_row_h)
                total_h = self._header.height() + self._line.height() + one_row_h
            else:
                list_h = self._list.sizeHint().height()
                self._list.setFixedHeight(list_h)
                total_h = self._header.height() + self._line.height() + list_h

        self.setFixedHeight(total_h)
        self.updateGeometry()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        QTimer.singleShot(0, self._update_list_height)

    def _on_double_click(self, index: QModelIndex) -> None:
        record = index.data(Qt.ItemDataRole.UserRole)
        if record and record.get("path"):
            self.file_double_clicked.emit(record["path"])

    def _on_selection(self) -> None:
        paths = [
            Path(idx.data(Qt.ItemDataRole.UserRole)["path"])
            for idx in self._list.selectedIndexes()
            if idx.data(Qt.ItemDataRole.UserRole)
        ]
        self.selection_changed.emit(paths)

    def _on_context_menu(self, position) -> None:
        index = self._list.indexAt(position)
        if not index.isValid():
            return
        if not index in self._list.selectedIndexes():
            self._list.clearSelection()
            self._list.selectionModel().setCurrentIndex(
                index,
                QItemSelectionModel.SelectionFlag.ClearAndSelect,
            )
        record = index.data(Qt.ItemDataRole.UserRole)
        if not record or not record.get("path"):
            return
        self.context_requested.emit(self._list.viewport().mapToGlobal(position))

    def request_visible_thumbs(self, viewport_rect, scroll_offset: int) -> None:
        if self._list.isHidden() or self._model.rowCount() == 0:
            return

        parent = self.parentWidget()
        if parent is None:
            return

        list_top = self._list.mapTo(parent, QPoint(0, 0)).y()
        margin = TILE_H * 2
        visible_top = scroll_offset - list_top - margin
        visible_bottom = scroll_offset + viewport_rect.height() - list_top + margin
        if visible_bottom < 0:
            return

        row_count = self._model.rowCount()
        grid = self._list.gridSize()
        cell_w = max(1, grid.width() or TILE_W)
        cell_h = max(1, grid.height() or TILE_H)
        cols = max(1, self._list.viewport().width() // cell_w)

        first_line = max(0, int(visible_top // cell_h))
        last_line = max(first_line, int(visible_bottom // cell_h) + 1)
        first_row = min(row_count, first_line * cols)
        last_row = min(row_count, (last_line + 1) * cols)

        for row in range(first_row, last_row):
            record = self._model._records[row]
            path = record.get("path") if record else None
            if not path:
                continue
            suffix = Path(path).suffix.lower()
            if suffix in IMAGE_EXTENSIONS or suffix in VIDEO_EXTENSIONS:
                self._thumb_mgr.request(path, priority=True)


# ---------------------------------------------------------------------------
# Главный виджет
# ---------------------------------------------------------------------------

class FileTileWidget(QWidget):
    """
    Два фиксированных раздела (Tagged / Untagged) в QScrollArea.
    Каждый раздел — SectionWidget с заголовком, линией и QListView.
    """

    selection_changed = pyqtSignal(list)
    files_changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._shared_model = TileModel(self)   # фиктивная модель только для кэша
        self.thumb_mgr = ThumbnailManager(self._shared_model)
        self.tags_at_start = False
        self._viewer: ImageViewerDialog | None = None

        self._pending_tagged: list[dict] = []
        self._pending_untagged: list[dict] = []
        self._pending_tagged_index = 0
        self._pending_untagged_index = 0
        self._loading = False
        self._batch_size = BATCH_SIZE
        self._load_generation = 0

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        container = QWidget()
        self._container_layout = QVBoxLayout(container)
        self._container_layout.setContentsMargins(4, 4, 4, 4)
        self._container_layout.setSpacing(12)

        self._sec_tagged = SectionWidget("Tagged files", self.thumb_mgr, container)
        self._sec_untagged = SectionWidget("Untagged files", self.thumb_mgr, container)

        for sec in (self._sec_tagged, self._sec_untagged):
            sec.file_double_clicked.connect(self._open_file_by_path)
            sec.selection_changed.connect(self._on_section_selection)
            sec.context_requested.connect(self._show_context_menu)

        self._container_layout.addWidget(self._sec_tagged)
        self._container_layout.addWidget(self._sec_untagged)
        self._container_layout.addStretch(1)

        self._scroll.setWidget(container)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.addWidget(self._scroll)

        self._thumb_request_timer = QTimer(self)
        self._thumb_request_timer.setSingleShot(True)
        self._thumb_request_timer.setInterval(SCROLL_DEBOUNCE_MS)
        self._thumb_request_timer.timeout.connect(self._request_visible_thumbs)

        self._scroll.verticalScrollBar().valueChanged.connect(self._on_scroll)

    def selected_paths(self) -> list[Path]:
        paths: list[Path] = []
        for sec in (self._sec_tagged, self._sec_untagged):
            for idx in sec.list_view.selectedIndexes():
                record = idx.data(Qt.ItemDataRole.UserRole)
                if record and record.get("path"):
                    paths.append(Path(record["path"]))
        return paths

    def set_files_batched(
        self, tagged: list[dict], untagged: list[dict], batch_size: int = BATCH_SIZE
    ) -> None:
        self._load_generation += 1
        generation = self._load_generation
        self._batch_size = max(1, batch_size)
        self.thumb_mgr.cancel_all()
        self._thumb_request_timer.stop()

        self._sec_tagged.set_records([])
        self._sec_untagged.set_records([])

        self._pending_tagged = list(tagged)
        self._pending_untagged = list(untagged)
        self._pending_tagged_index = 0
        self._pending_untagged_index = 0
        self._loading = True
        QTimer.singleShot(0, lambda: self._load_next_batch(generation))

    def _load_next_batch(self, generation: int) -> None:
        if generation != self._load_generation:
            return

        deadline = time.perf_counter() + (BATCH_TIME_BUDGET_MS / 1000)
        while time.perf_counter() < deadline and self._has_pending_records():
            if self._pending_tagged_index < len(self._pending_tagged):
                next_index = min(len(self._pending_tagged), self._pending_tagged_index + self._batch_size)
                batch = self._pending_tagged[self._pending_tagged_index : next_index]
                self._pending_tagged_index = next_index
                self._sec_tagged.append_records(batch)
                self._sync_thumb_manager()
                continue

            if self._pending_untagged_index < len(self._pending_untagged):
                next_index = min(len(self._pending_untagged), self._pending_untagged_index + self._batch_size)
                batch = self._pending_untagged[self._pending_untagged_index : next_index]
                self._pending_untagged_index = next_index
                self._sec_untagged.append_records(batch)
                self._sync_thumb_manager()

        if self._has_pending_records():
            QTimer.singleShot(1, lambda: self._load_next_batch(generation))
            return

        self._loading = False
        tagged_count = self._sec_tagged.model.rowCount()
        untagged_count = self._sec_untagged.model.rowCount()
        if tagged_count == 0:
            self._sec_tagged.set_records([])
        if untagged_count == 0:
            self._sec_untagged.set_records([])
        self._pending_tagged.clear()
        self._pending_untagged.clear()
        self._pending_tagged_index = 0
        self._pending_untagged_index = 0
        self._schedule_visible_thumb_request()

    def _has_pending_records(self) -> bool:
        return (
            self._pending_tagged_index < len(self._pending_tagged)
            or self._pending_untagged_index < len(self._pending_untagged)
        )

    def _sync_thumb_manager(self) -> None:
        pass  # см. _patch_thumb_manager ниже

    def _request_visible_thumbs(self) -> None:
        vp_rect = self._scroll.viewport().rect()
        offset = self._scroll.verticalScrollBar().value()
        self._sec_tagged.request_visible_thumbs(vp_rect, offset)
        self._sec_untagged.request_visible_thumbs(vp_rect, offset)

    def _schedule_visible_thumb_request(self) -> None:
        if not self._loading:
            self._thumb_request_timer.start()

    def _on_scroll(self) -> None:
        self._schedule_visible_thumb_request()

    def shutdown(self) -> None:
        self._load_generation += 1
        self._pending_tagged.clear()
        self._pending_untagged.clear()
        self._pending_tagged_index = 0
        self._pending_untagged_index = 0
        self._thumb_request_timer.stop()
        self._close_viewer()
        self.thumb_mgr.shutdown()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.shutdown()
        super().closeEvent(event)

    def _on_section_selection(self, paths: list[Path]) -> None:
        self.selection_changed.emit(paths)

    def _get_current_ordered_paths(self) -> list[Path]:
        """Returns all tile paths in current visual section order."""
        ordered_paths: list[Path] = []
        for section in (self._sec_tagged, self._sec_untagged):
            for record in section.model._records:
                raw_path = str(record.get("path", ""))
                if raw_path:
                    ordered_paths.append(Path(raw_path))
        return ordered_paths

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

    def _open_file_by_path(self, path_str: str) -> None:
        path = Path(path_str)
        try:
            ensure_current_regular_file(path, self._identity_for_path(path))
        except FileOperationError as exc:
            QMessageBox.warning(self, "Open file", str(exc))
            return

        if path.suffix.lower() in get_supported_image_suffixes():
            self._close_viewer()
            viewer = ImageViewerDialog(self._get_current_ordered_paths(), path, self)
            self._viewer = viewer
            viewer.destroyed.connect(lambda _obj=None, viewer=viewer: self._clear_viewer_reference(viewer))
            viewer.show()
            return
        open_path_with_shell(self, path)

    def _show_context_menu(self, global_pos: QPoint) -> None:
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
        menu.exec(global_pos)

    def _records_by_path(self) -> dict[str, dict]:
        records: dict[str, dict] = {}
        for sec in (self._sec_tagged, self._sec_untagged):
            for record in sec.model._records:
                raw_path = str(record.get("path", ""))
                if not raw_path:
                    continue
                normalized_path = str(Path(raw_path))
                records[normalized_path] = record
        return records

    def _tags_for_path(self, path: Path) -> list[str]:
        record = self._records_by_path().get(str(path), {})
        raw_tags = record.get("tags", FileScanner.parse_tags(path.name))
        if not isinstance(raw_tags, list):
            raw_tags = FileScanner.parse_tags(path.name)
        return normalize_tags(raw_tags)

    def _edit_tags(self) -> None:
        selected_paths = self.selected_paths()
        if not selected_paths:
            QMessageBox.information(self, "Edit tags", "Please select one or more files to edit tags.")
            return

        available_tags = set()
        for record in self._records_by_path().values():
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

        self.files_changed.emit()

    def _rename_with_tags(self, source_path: Path, updated_tags: list[str]) -> tuple[bool, str | None]:
        try:
            new_name = build_tagged_filename(source_path, updated_tags, self.tags_at_start)
            target_path = build_target_path(source_path, new_name)
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
            new_name = build_tagged_filename(source_path, updated_tags, self.tags_at_start)
            target_path = build_target_path(source_path, new_name)
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

    def _rename_files(self) -> None:
        selected_paths = self.selected_paths()
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
        self.files_changed.emit()

    def _get_unique_path(self, target_path: Path) -> Path:
        if not target_path.exists():
            return target_path
        return unique_path(target_path)

    def _delete_files(self) -> None:
        selected_paths = self.selected_paths()
        if not selected_paths:
            return
        reply = QMessageBox.question(
            self,
            "Delete",
            f"Delete selected file(s)?\n\n{selected_paths[0].name}"
            + (f"\n... and {len(selected_paths) - 1} more file(s)." if len(selected_paths) > 1 else ""),
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
            self.files_changed.emit()
        if errors:
            QMessageBox.warning(self, "Delete", "\n".join(errors[:8]))

    def _open_file_location(self) -> None:
        selected_paths = self.selected_paths()
        if not selected_paths:
            return
        try:
            ensure_current_regular_file(selected_paths[0], self._identity_for_path(selected_paths[0]))
            reveal_path_in_explorer(self, selected_paths[0])
        except FileOperationError as exc:
            QMessageBox.warning(self, "Open location", f"Unable to open file location:\n{exc}")

    def _identity_for_path(self, path: Path) -> FileIdentity | None:
        return FileIdentity.from_record(self._records_by_path().get(str(path)))


# ---------------------------------------------------------------------------
# Патч: переопределяем ThumbnailManager чтобы он не зависел от одной модели
# ---------------------------------------------------------------------------

_original_FileTileWidget_init = FileTileWidget.__init__


def _patched_init(self, parent=None):
    _original_FileTileWidget_init(self, parent)

    mgr = self.thumb_mgr
    sec_tagged = self._sec_tagged
    sec_untagged = self._sec_untagged

    def _routed_on_done(generation: int, path: str, image: QImage) -> None:
        if generation != mgr._generation:
            return

        mgr._in_flight.discard(path)
        mgr._active_workers.pop(path, None)

        if not mgr._shutdown and not image.isNull():
            if sec_tagged.model._path_to_row.get(path) is not None:
                sec_tagged.model.update_thumbnail(path, image)
            elif sec_untagged.model._path_to_row.get(path) is not None:
                sec_untagged.model.update_thumbnail(path, image)
            mgr._queue_thumbnail_store(path, image)

        mgr._dispatch()

    mgr._on_done = _routed_on_done

    def _routed_request(path: str, priority: bool = False) -> None:
        if mgr._shutdown or path in mgr._in_flight:
            return

        in_tagged = sec_tagged.model._thumb_cache.get(path) is not None
        in_untagged = sec_untagged.model._thumb_cache.get(path) is not None
        if in_tagged or in_untagged:
            return

        try:
            blob = mgr.cache.get_thumbnail(path)
        except Exception:
            blob = None
        if blob:
            image = QImage.fromData(blob)
            if not image.isNull():
                if sec_tagged.model._path_to_row.get(path) is not None:
                    sec_tagged.model.update_thumbnail(path, image)
                elif sec_untagged.model._path_to_row.get(path) is not None:
                    sec_untagged.model.update_thumbnail(path, image)
                return

        if path not in mgr._queue:
            if len(mgr._queue) >= MAX_THUMB_QUEUE:
                if not priority:
                    return
                mgr._queue.popitem(last=True)
            mgr._queue[path] = True
        if priority:
            mgr._queue.move_to_end(path, last=False)
        mgr._dispatch()

    mgr.request = _routed_request


FileTileWidget.__init__ = _patched_init
