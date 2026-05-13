"""Built-in image viewer for sequential preview."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QCloseEvent, QImageReader, QKeyEvent, QMovie, QPixmap
from PyQt6.QtWidgets import QDialog, QLabel, QSizePolicy, QVBoxLayout, QWidget

from core.media_safety import IMAGE_PREVIEW_TARGET_EDGE, MediaSafetyError, read_limited_image


def get_supported_image_suffixes() -> set[str]:
    """Return dynamically supported image suffixes for current Qt build."""
    return {f".{bytes(fmt).decode(errors='ignore').lower()}" for fmt in QImageReader.supportedImageFormats()}


class ImageViewerDialog(QDialog):
    """A dialog for viewing images sequentially as they appear in the file list."""

    def __init__(self, paths: list[Path], start_path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("TagExplorer Image Viewer")
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.WindowMaximizeButtonHint
            | Qt.WindowType.WindowMinimizeButtonHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(900, 700)

        supported_formats = get_supported_image_suffixes()
        self.image_paths = [path for path in paths if path.suffix.lower() in supported_formats]

        try:
            self.current_index = self.image_paths.index(start_path)
        except ValueError:
            self.current_index = 0

        self._current_pixmap: QPixmap | None = None
        self._current_movie: QMovie | None = None
        self._movie_source_size: QSize | None = None

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.image_label.setMinimumSize(100, 100)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.image_label)

        self._load_image()

    def _load_image(self) -> None:
        if not self.image_paths:
            self.image_label.setText("No images to display")
            return

        path = self.image_paths[self.current_index]
        self.setWindowTitle(f"Viewer - {path.name} ({self.current_index + 1} of {len(self.image_paths)})")

        self._clear_current_media()
        self.image_label.clear()

        if path.suffix.lower() == ".gif":
            self._load_movie(path)
            return

        try:
            image = read_limited_image(
                path,
                QSize(IMAGE_PREVIEW_TARGET_EDGE, IMAGE_PREVIEW_TARGET_EDGE),
            )
        except MediaSafetyError as exc:
            self.image_label.setText(str(exc))
            return

        self._current_pixmap = QPixmap.fromImage(image)
        self._update_pixmap_scale()

    def _load_movie(self, path: Path) -> None:
        try:
            first_frame = read_limited_image(
                path,
                QSize(IMAGE_PREVIEW_TARGET_EDGE, IMAGE_PREVIEW_TARGET_EDGE),
            )
        except MediaSafetyError as exc:
            self.image_label.setText(str(exc))
            return

        movie = QMovie(str(path))
        if not movie.isValid():
            self._current_pixmap = QPixmap.fromImage(first_frame)
            self._update_pixmap_scale()
            return

        movie.setParent(self)
        movie.finished.connect(self._restart_movie)
        self._current_movie = movie
        self._movie_source_size = first_frame.size()
        self.image_label.setMovie(movie)
        self._update_movie_scale()
        movie.start()

    def _clear_current_media(self) -> None:
        movie = self._current_movie
        self._current_movie = None
        self._movie_source_size = None
        self._current_pixmap = None
        if movie is not None:
            try:
                movie.finished.disconnect(self._restart_movie)
            except TypeError:
                pass
            movie.stop()
            movie.deleteLater()

    def _update_pixmap_scale(self) -> None:
        if self._current_pixmap and not self._current_pixmap.isNull():
            scaled = self._current_pixmap.scaled(
                self.image_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.image_label.setPixmap(scaled)

    def _update_movie_scale(self) -> None:
        if self._current_movie is None or self._movie_source_size is None:
            return

        target_size = QSize(
            max(1, self.image_label.width()),
            max(1, self.image_label.height()),
        )
        scaled_size = self._movie_source_size.scaled(
            target_size,
            Qt.AspectRatioMode.KeepAspectRatio,
        )
        if scaled_size.isValid():
            self._current_movie.setScaledSize(scaled_size)

    def _restart_movie(self) -> None:
        if self._current_movie is None:
            return
        if self._current_movie.frameCount() == 1:
            return

        self._current_movie.jumpToFrame(0)
        self._current_movie.start()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        if self._current_movie is not None:
            self._update_movie_scale()
        else:
            self._update_pixmap_scale()

    def closeEvent(self, event: QCloseEvent) -> None:  # type: ignore[override]
        self._clear_current_media()
        super().closeEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        if event.key() in (Qt.Key.Key_Right, Qt.Key.Key_D):
            self._next_image()
            return
        if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_A):
            self._prev_image()
            return
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)

    def _next_image(self) -> None:
        if self.current_index < len(self.image_paths) - 1:
            self.current_index += 1
            self._load_image()

    def _prev_image(self) -> None:
        if self.current_index > 0:
            self.current_index -= 1
            self._load_image()
