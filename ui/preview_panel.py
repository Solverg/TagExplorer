"""Asynchronous preview panel for selected files."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QFileInfo, QSize, QThread, Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QFileIconProvider, QLabel, QSizePolicy, QVBoxLayout, QWidget

from core.media_safety import (
    IMAGE_PREVIEW_TARGET_EDGE,
    MediaSafetyError,
    read_limited_image,
    read_limited_video_frame,
)
from core.scanner import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS


class PreviewWorker(QThread):
    """Loads preview data in background thread."""

    loaded = pyqtSignal(int, dict)

    def __init__(self, generation: int, path: Path) -> None:
        super().__init__()
        self.generation = generation
        self.path = path

    def run(self) -> None:
        payload = {"kind": "other", "path": self.path}
        ext = self.path.suffix.lower()
        preview_size = QSize(IMAGE_PREVIEW_TARGET_EDGE, IMAGE_PREVIEW_TARGET_EDGE)

        try:
            if ext in IMAGE_EXTENSIONS:
                if self.isInterruptionRequested():
                    return
                image = read_limited_image(self.path, preview_size)
                if self.isInterruptionRequested():
                    return
                payload = {"kind": "image", "path": self.path, "image": image}
            elif ext in VIDEO_EXTENSIONS:
                image, duration = read_limited_video_frame(self.path, preview_size)
                if self.isInterruptionRequested():
                    return
                payload = {
                    "kind": "video",
                    "path": self.path,
                    "image": image,
                    "duration": duration,
                }
            else:
                payload = {"kind": "other", "path": self.path}
        except MediaSafetyError as exc:
            payload = {"kind": "other", "path": self.path, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            payload = {"kind": "other", "path": self.path, "error": str(exc)}

        self._emit_loaded(payload)

    def _emit_loaded(self, payload: dict) -> None:
        if not self.isInterruptionRequested():
            self.loaded.emit(self.generation, payload)


class PreviewPanel(QWidget):
    """Right-side panel showing image/video preview and metadata."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(70)
        self.icon_provider = QFileIconProvider()
        self.worker: PreviewWorker | None = None
        self._retired_workers: list[PreviewWorker] = []
        self._preview_generation = 0
        self._selected_preview_path: Path | None = None
        self._current_pixmap: QPixmap | None = None

        layout = QVBoxLayout(self)
        self.image_label = QLabel("Select a file to preview")
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setMinimumSize(1, 1)
        self.image_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.image_label.setWordWrap(True)

        self.info_label = QLabel("")
        self.info_label.setWordWrap(True)
        self.info_label.setMinimumSize(1, 1)
        self.info_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

        layout.addWidget(self.image_label, 1)
        layout.addWidget(self.info_label)

    def show_selection(self, paths: list[Path]) -> None:
        """Update preview for selected paths."""
        self._preview_generation += 1

        if len(paths) != 1:
            self._selected_preview_path = None
            self._cancel_worker(wait_ms=50)
            self.image_label.setText("Select a single file to preview")
            self.image_label.setPixmap(QPixmap())
            self.info_label.setText("")
            self._current_pixmap = None
            return

        self._cancel_worker(wait_ms=50)

        self.image_label.setText("Loading preview...")
        self.image_label.setPixmap(QPixmap())
        self.info_label.setText("")
        self._current_pixmap = None

        self._selected_preview_path = paths[0]
        self.worker = PreviewWorker(self._preview_generation, paths[0])
        self.worker.loaded.connect(self._on_preview_loaded)
        self.worker.finished.connect(lambda worker=self.worker: self._on_worker_finished(worker))
        self.worker.start()

    def _cancel_worker(self, wait_ms: int = 0) -> None:
        worker = self.worker
        self.worker = None
        if worker is None:
            return

        try:
            worker.requestInterruption()
            running = worker.isRunning()
        except RuntimeError:
            self._forget_retired_worker(worker)
            return

        if running:
            if not any(retired is worker for retired in self._retired_workers):
                self._retired_workers.append(worker)
                worker.finished.connect(lambda retired=worker: self._forget_retired_worker(retired))
            if wait_ms > 0 and worker.wait(wait_ms):
                self._forget_retired_worker(worker)

    def _forget_retired_worker(self, worker: PreviewWorker) -> None:
        self._retired_workers = [retired for retired in self._retired_workers if retired is not worker]

    def _on_worker_finished(self, worker: PreviewWorker) -> None:
        if self.worker is worker:
            self.worker = None
        self._forget_retired_worker(worker)
        try:
            worker.deleteLater()
        except RuntimeError:
            return

    def _on_preview_loaded(self, generation: int, payload: dict) -> None:
        if generation != self._preview_generation:
            return
        path: Path = payload["path"]
        if self._selected_preview_path != path:
            return

        try:
            stat = path.stat()
            size_text = f"{stat.st_size:,} bytes"
            modified_text = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        except OSError:
            size_text = "Unknown"
            modified_text = "Unknown"

        if payload.get("kind") in {"image", "video"} and payload.get("image") is not None:
            self._current_pixmap = QPixmap.fromImage(payload["image"])
            self._update_image_preview()
        else:
            self._current_pixmap = None
            icon = self.icon_provider.icon(QFileInfo(str(path)))
            self.image_label.setPixmap(icon.pixmap(96, 96))
            self.image_label.setText(payload.get("error", "No preview available"))

        duration = payload.get("duration")
        duration_text = f"\nDuration: {duration:.1f} s" if duration is not None else ""
        self.info_label.setText(
            f"Name: {path.name}\nPath: {path}\nSize: {size_text}\nModified: {modified_text}{duration_text}"
        )

    def shutdown(self) -> None:
        self._preview_generation += 1
        self._selected_preview_path = None
        self._cancel_worker(wait_ms=1000)
        for worker in list(self._retired_workers):
            try:
                worker.requestInterruption()
                if worker.isRunning() and not worker.wait(3000):
                    worker.terminate()
                    worker.wait(1000)
            except RuntimeError:
                pass
            self._forget_retired_worker(worker)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.shutdown()
        super().closeEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_image_preview()

    def _update_image_preview(self) -> None:
        if self._current_pixmap is None or self._current_pixmap.isNull():
            return

        width = max(1, self.image_label.width() - 4)
        height = max(1, self.image_label.height() - 4)
        scaled = self._current_pixmap.scaled(
            width,
            height,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.image_label.setPixmap(scaled)
        self.image_label.setText("")
