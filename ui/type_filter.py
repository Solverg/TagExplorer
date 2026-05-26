"""File type filter widget."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui.theme import DEFAULT_THEME, ThemeName, normalize_theme_name, type_filter_button_stylesheet


class CollapsibleTypeFilter(QWidget):
    """A collapsible panel containing checkboxes for filtering by file type."""

    filters_changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme_name: ThemeName = DEFAULT_THEME
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 5, 0, 5)
        self.layout.setSpacing(4)

        self.toggle_btn = QPushButton("▶ File types")
        self.toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_btn.setStyleSheet(type_filter_button_stylesheet(self._theme_name))
        self.toggle_btn.clicked.connect(self.toggle_content)
        self.layout.addWidget(self.toggle_btn)

        self.content_widget = QFrame()
        self.content_widget.setVisible(False)
        self.content_layout = QHBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(16, 0, 0, 0)

        self.checkboxes: dict[str, QCheckBox] = {}
        types = {
            "Images": "image",
            "Audio": "audio",
            "Video": "video",
            "Documents": "document",
            "Other": "other",
        }

        for label, type_id in types.items():
            cb = QCheckBox(label)
            cb.stateChanged.connect(self.filters_changed.emit)
            self.content_layout.addWidget(cb)
            self.checkboxes[type_id] = cb

        self.content_layout.addStretch(1)
        self.layout.addWidget(self.content_widget)

    def apply_theme(self, theme_name: ThemeName) -> None:
        self._theme_name = normalize_theme_name(theme_name)
        self.toggle_btn.setStyleSheet(type_filter_button_stylesheet(self._theme_name))

    def toggle_content(self) -> None:
        """Show/hide collapsible content."""
        is_visible = self.content_widget.isVisible()
        self.content_widget.setVisible(not is_visible)
        self.toggle_btn.setText("▼ File types" if not is_visible else "▶ File types")

    def selected_types(self) -> list[str]:
        """Return internal IDs for currently selected types."""
        return [type_id for type_id, cb in self.checkboxes.items() if cb.isChecked()]
