"""Tag filter panel widgets."""

from __future__ import annotations

from PyQt6.QtCore import QEvent, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.tags import tag_sort_key


class TagPanel(QWidget):
    """Displays available tags as selectable pill-style buttons."""

    filters_changed = pyqtSignal(list, str)
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

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tag_buttons: dict[str, QPushButton] = {}
        self._tag_base_colors: dict[str, str] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("Tag filter mode:"))
        self.and_button = QRadioButton("AND")
        self.or_button = QRadioButton("OR")
        self.or_button.setChecked(True)

        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.and_button)
        self.mode_group.addButton(self.or_button)
        self.mode_group.buttonToggled.connect(lambda _btn, _checked: self._emit_state())

        top_row.addWidget(self.and_button)
        top_row.addWidget(self.or_button)
        top_row.addStretch(1)

        self.clear_button = QPushButton("Clear all")
        self.clear_button.clicked.connect(self.clear_selection)
        top_row.addWidget(self.clear_button)
        layout.addLayout(top_row)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)

        self.tags_container = QWidget()
        self.tags_layout = QGridLayout(self.tags_container)
        self.tags_layout.setHorizontalSpacing(8)
        self.tags_layout.setVerticalSpacing(8)
        self.tags_layout.setContentsMargins(8, 0, 8, 0)
        self.scroll_area.setWidget(self.tags_container)
        layout.addWidget(self.scroll_area)

    def set_tags(self, tags: list[str]) -> None:
        """Render sorted unique tags as selectable rounded buttons."""
        while self.tags_layout.count():
            item = self.tags_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._tag_buttons.clear()
        self._tag_base_colors.clear()

        columns = 3
        for index, tag in enumerate(sorted(set(tags), key=tag_sort_key)):
            button = QPushButton(tag)
            button.setCheckable(True)
            button.clicked.connect(self._on_tag_clicked)
            base_color = self._BASE_TAG_COLORS[index % len(self._BASE_TAG_COLORS)]
            self._tag_base_colors[tag] = base_color
            button.setStyleSheet(self._build_tag_stylesheet(base_color))
            row = index // columns
            col = index % columns
            self.tags_layout.addWidget(button, row, col)
            self._tag_buttons[tag] = button

        self._emit_state()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() in {QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange}:
            self._refresh_tag_styles()

    def selected_tags(self) -> list[str]:
        """Return selected tags in stable alphabetical order from initial tag mapping."""
        return [tag for tag, button in self._tag_buttons.items() if button.isChecked()]

    def mode(self) -> str:
        """Return current filter mode: AND or OR."""
        return "AND" if self.and_button.isChecked() else "OR"

    def clear_selection(self) -> None:
        """Uncheck all tag buttons."""
        for button in self._tag_buttons.values():
            button.blockSignals(True)
            button.setChecked(False)
            button.blockSignals(False)
        self._reorder_tags()
        self._emit_state()

    def _on_tag_clicked(self) -> None:
        """Handle tag click by reordering tags and emitting state."""
        self._reorder_tags()
        self._emit_state()

    def _reorder_tags(self) -> None:
        """Rebuild the grid so selected tags appear first, then unselected tags."""
        checked_tags: list[str] = []
        unchecked_tags: list[str] = []

        for tag, button in self._tag_buttons.items():
            if button.isChecked():
                checked_tags.append(tag)
            else:
                unchecked_tags.append(tag)

        checked_tags.sort(key=tag_sort_key)
        unchecked_tags.sort(key=tag_sort_key)
        ordered_tags = checked_tags + unchecked_tags

        columns = 3
        for index, tag in enumerate(ordered_tags):
            button = self._tag_buttons[tag]
            self.tags_layout.removeWidget(button)
            row = index // columns
            col = index % columns
            self.tags_layout.addWidget(button, row, col)

    def _emit_state(self) -> None:
        self.filters_changed.emit(self.selected_tags(), self.mode())

    @staticmethod
    def _hex_to_rgba(hex_color: str, alpha: float) -> str:
        """Convert a hex color to an RGBA value for Qt stylesheets."""
        clean_hex = hex_color.lstrip("#")
        red = int(clean_hex[0:2], 16)
        green = int(clean_hex[2:4], 16)
        blue = int(clean_hex[4:6], 16)
        return f"rgba({red}, {green}, {blue}, {alpha})"

    def _refresh_tag_styles(self) -> None:
        for tag, button in self._tag_buttons.items():
            base_color = self._tag_base_colors.get(tag, self._BASE_TAG_COLORS[0])
            button.setStyleSheet(self._build_tag_stylesheet(base_color))

    def _build_tag_stylesheet(self, base_color: str) -> str:
        is_dark = self.palette().window().color().lightness() < 128
        base_alpha = 0.9 if is_dark else 0.18
        hover_alpha = min(base_alpha + 0.08, 0.95)
        selected_alpha = 0.95 if is_dark else 0.28
        text_color = "#f7f9fc" if is_dark else "#1f2a37"
        selected_text_color = "#ffffff" if is_dark else "#0f172a"
        border_color = "rgba(255, 255, 255, 0.2)" if is_dark else "rgba(15, 23, 42, 0.24)"
        hover_border_color = "rgba(255, 255, 255, 0.34)" if is_dark else "rgba(15, 23, 42, 0.38)"
        checked_border = "rgba(120, 200, 255, 0.95)" if is_dark else "rgba(31, 111, 235, 0.95)"
        checked_background = self._hex_to_rgba(base_color, selected_alpha)
        base_background = self._hex_to_rgba(base_color, base_alpha)
        hover_background = self._hex_to_rgba(base_color, hover_alpha)

        return f"""
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
                border-color: {hover_border_color};
            }}
        """
