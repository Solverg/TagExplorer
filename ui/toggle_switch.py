"""Small custom toggle switch widgets."""

from __future__ import annotations

from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, QRectF, Qt, pyqtProperty, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QWidget


class ToggleSwitch(QWidget):
    """Animated on/off switch with a QPushButton-like toggled signal."""

    toggled = pyqtSignal(bool)

    _WIDTH = 44
    _HEIGHT = 24
    _PADDING = 3

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._checked = False
        self._thumb_progress = 0.0
        self._pressed = False

        self.setFixedSize(self._WIDTH, self._HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._animation = QPropertyAnimation(self, b"thumb_progress", self)
        self._animation.setDuration(150)
        self._animation.setEasingCurve(QEasingCurve.Type.InOutCubic)

    @pyqtProperty(float)
    def thumb_progress(self) -> float:  # type: ignore[override]
        return self._thumb_progress

    @thumb_progress.setter  # type: ignore[override]
    def thumb_progress(self, value: float) -> None:
        self._thumb_progress = max(0.0, min(1.0, value))
        self.update()

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, checked: bool) -> None:
        checked = bool(checked)
        if self._checked == checked:
            self._thumb_progress = 1.0 if checked else 0.0
            self.update()
            return

        self._checked = checked
        self._animate_thumb()
        self.toggled.emit(self._checked)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed = True
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if self._pressed:
            self._pressed = False
            if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
                self.setChecked(not self._checked)
            self.update()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        self._pressed = False
        self.update()
        super().leaveEvent(event)

    def _animate_thumb(self) -> None:
        self._animation.stop()
        self._animation.setStartValue(self._thumb_progress)
        self._animation.setEndValue(1.0 if self._checked else 0.0)
        self._animation.start()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        track_rect = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        radius = track_rect.height() / 2.0

        is_dark = self.palette().window().color().lightness() < 128

        if self._checked:
            track_color = QColor(96, 165, 250, 150)
            border_color = QColor(147, 197, 253, 210)
            thumb_color = QColor("#e8f2ff")
        elif is_dark:
            track_color = QColor(255, 255, 255, 16)
            border_color = QColor(255, 255, 255, 34)
            thumb_color = QColor("#9aaabb")
        else:
            track_color = QColor(15, 23, 42, 18)
            border_color = QColor(15, 23, 42, 46)
            thumb_color = QColor("#5d6b82")

        if self._pressed:
            track_color = track_color.lighter(118)

        painter.setPen(QPen(border_color, 1.0))
        painter.setBrush(track_color)
        painter.drawRoundedRect(track_rect, radius, radius)

        thumb_size = self.height() - self._PADDING * 2
        min_x = self._PADDING
        max_x = self.width() - self._PADDING - thumb_size
        thumb_x = min_x + (max_x - min_x) * self._thumb_progress
        thumb_rect = QRectF(thumb_x, self._PADDING, thumb_size, thumb_size)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(thumb_color)
        painter.drawEllipse(thumb_rect)
        painter.end()
