"""Tag filter panel widgets."""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QPropertyAnimation, QEasingCurve, QRect, Qt, QTimer, pyqtSignal, pyqtProperty
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (
    QAbstractScrollArea,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.tags import tag_sort_key
from ui.theme import (
    DEFAULT_THEME,
    ThemeName,
    destructive_secondary_action_button_stylesheet,
    normalize_theme_name,
)


class ModeToggle(QWidget):
    """Pill-shaped AND / OR toggle that slides a thumb between two labels."""

    toggled = pyqtSignal(str)  # emits "AND" or "OR"

    _THUMB_W = 44
    _THUMB_H = 22
    _PADDING = 3
    _LABEL_W = 44
    _LABEL_H = 22
    _BORDER = 1  # track border; adding to geometry so thumb never clips

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # True  → AND (thumb on the left)
        # False → OR  (thumb on the right)
        self._is_and = False

        total_w = self._LABEL_W * 2 + self._PADDING * 2 + self._BORDER * 2
        total_h = self._LABEL_H + self._PADDING * 2 + self._BORDER * 2
        self.setFixedSize(total_w, total_h)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Toggle filter mode: AND / OR")

        # Animated thumb x-position (left edge of thumb inside widget)
        self._thumb_x: float = self._BORDER + self._PADDING + self._LABEL_W  # starts at OR
        self._anim = QPropertyAnimation(self, b"thumb_x", self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)

    # ── pyqtProperty so QPropertyAnimation can drive it ──────────────────────
    @pyqtProperty(float)
    def thumb_x(self) -> float:  # type: ignore[override]
        return self._thumb_x

    @thumb_x.setter  # type: ignore[override]
    def thumb_x(self, value: float) -> None:
        self._thumb_x = value
        self.update()

    # ── public API ────────────────────────────────────────────────────────────
    def set_mode(self, mode: str) -> None:
        """Set 'AND' or 'OR' without emitting the signal."""
        self._is_and = mode.upper() == "AND"
        self._snap_thumb()

    def mode(self) -> str:
        return "AND" if self._is_and else "OR"

    # ── interaction ───────────────────────────────────────────────────────────
    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._is_and = not self._is_and
            self._animate_thumb()
            self.toggled.emit(self.mode())
        super().mousePressEvent(event)

    # ── animation helpers ─────────────────────────────────────────────────────
    def _target_x(self) -> float:
        offset = self._BORDER + self._PADDING
        return float(offset if self._is_and else offset + self._LABEL_W)

    def _snap_thumb(self) -> None:
        self._thumb_x = self._target_x()
        self.update()

    def _animate_thumb(self) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._thumb_x)
        self._anim.setEndValue(self._target_x())
        self._anim.start()

    # ── painting ──────────────────────────────────────────────────────────────
    def paintEvent(self, event) -> None:  # type: ignore[override]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        is_dark = self.palette().window().color().lightness() < 128

        # ── track ────────────────────────────────────────────────────────────
        if is_dark:
            track_bg   = QColor("#252b38")
            track_bdr  = QColor("#4a5568")
        else:
            track_bg   = QColor("#dde3ec")
            track_bdr  = QColor("#b0bac8")

        radius = self.height() / 2
        # Inset by half border so stroke doesn't get clipped
        b = self._BORDER / 2
        track_rect = self.rect().adjusted(
            int(b + 0.5), int(b + 0.5), -int(b + 0.5), -int(b + 0.5)
        )
        p.setPen(QPen(track_bdr, self._BORDER))
        p.setBrush(track_bg)
        p.drawRoundedRect(track_rect, radius, radius)

        # ── sliding thumb ────────────────────────────────────────────────────
        thumb_x = int(self._thumb_x)
        thumb_rect = QRect(thumb_x, self._BORDER + self._PADDING, self._THUMB_W, self._THUMB_H)

        if is_dark:
            thumb_bg  = QColor("#3d4d63")   # muted slate-steel
            thumb_bdr = QColor("#5a6e8a")
        else:
            thumb_bg  = QColor("#4a6080")
            thumb_bdr = QColor("#334d6e")

        thumb_r = self._THUMB_H / 2
        p.setPen(QPen(thumb_bdr, 1))
        p.setBrush(thumb_bg)
        p.drawRoundedRect(thumb_rect, thumb_r, thumb_r)

        # ── labels ────────────────────────────────────────────────────────────
        font = p.font()
        font.setPointSize(8)
        font.setBold(True)
        p.setFont(font)

        lx = self._BORDER + self._PADDING
        ly = self._BORDER + self._PADDING
        and_rect = QRect(lx, ly, self._LABEL_W, self._LABEL_H)
        or_rect  = QRect(lx + self._LABEL_W, ly, self._LABEL_W, self._LABEL_H)

        def _label_color(rect: QRect) -> QColor:
            # white if covered by thumb, else dim
            overlap = rect.intersected(thumb_rect)
            if overlap.width() > rect.width() * 0.5:
                return QColor("#ffffff")
            return QColor("#94a3b8" if is_dark else "#64748b")

        p.setPen(_label_color(and_rect))
        p.drawText(and_rect, Qt.AlignmentFlag.AlignCenter, "AND")
        p.setPen(_label_color(or_rect))
        p.drawText(or_rect, Qt.AlignmentFlag.AlignCenter, "OR")

        p.end()


class TagFilterButton(QPushButton):
    """Tag pill with a dedicated exclude mini-button on the right edge."""

    exclude_clicked = pyqtSignal()
    _EXCLUDE_ZONE_W = 28       # width of the right zone reserved for the ×
    _EXCLUDE_HIT_PADDING = 4

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._exclude_hovered = False
        self._exclude_pressed = False
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Click to include tag · click 🚫 to exclude from results")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def _exclude_button_rect(self) -> QRect:
        """Square zone on the right where the × is drawn."""
        size = self.height()
        return QRect(self.width() - size, 0, size, size)

    def _exclude_hit_rect(self) -> QRect:
        return self._exclude_button_rect().adjusted(
            -self._EXCLUDE_HIT_PADDING,
            -self._EXCLUDE_HIT_PADDING,
            self._EXCLUDE_HIT_PADDING,
            self._EXCLUDE_HIT_PADDING,
        )

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton and self._exclude_hit_rect().contains(event.position().toPoint()):
            self._exclude_pressed = True
            event.accept()
            self.update()
            return
        self._exclude_pressed = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        exclude_hovered = self._exclude_hit_rect().contains(event.position().toPoint())
        if exclude_hovered != self._exclude_hovered:
            self._exclude_hovered = exclude_hovered
            self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if self._exclude_pressed:
            self._exclude_pressed = False
            if event.button() == Qt.MouseButton.LeftButton and self._exclude_hit_rect().contains(event.position().toPoint()):
                self.exclude_clicked.emit()
            event.accept()
            self.update()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        self._exclude_hovered = False
        self._exclude_pressed = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        state   = str(self.property("tagState") or "none")
        is_dark = self.palette().window().color().lightness() < 128
        w, h    = self.width(), self.height()
        radius  = h / 2.0

        # ── resolve base colors from stylesheet property ──────────────────
        # The tag base color is baked into the stylesheet bg; we read it back
        # from the palette or fall back to a neutral. We paint everything
        # ourselves so the stylesheet only needs to supply the base bg tint.
        bg_color   = self.palette().button().color()
        bg_color.setAlphaF(0.0)   # we override fully below

        # ── per-state pill background & border ───────────────────────────
        if state == "exclude":
            pill_bg  = QColor(185, 28,  28,  int(0.70 * 255) if is_dark else int(0.22 * 255))
            pill_bdr = QColor(248, 113, 113, int(0.90 * 255) if is_dark else int(0.70 * 255))
            text_col = QColor("#ffffff" if is_dark else "#7f1d1d")
        elif state == "include":
            # base color tint comes from the stylesheet; we draw a lighter overlay
            pill_bg  = QColor(255, 255, 255, int(0.10 * 255))
            pill_bdr = QColor(180, 210, 255, int(0.80 * 255))
            text_col = QColor("#ffffff")
        else:
            pill_bg  = QColor(255, 255, 255, int(0.06 * 255) if is_dark else int(0.55 * 255))
            pill_bdr = QColor(255, 255, 255, int(0.15 * 255) if is_dark else int(0.28 * 255))
            text_col = QColor("#e8edf5" if is_dark else "#1f2a37")

        # ── hover / press tinting ────────────────────────────────────────
        hovered = self.underMouse() and not self._exclude_hovered
        pressed = self.isDown() and not self._exclude_pressed

        if pressed:
            pill_bg = pill_bg.lighter(130)
        elif hovered:
            pill_bg = pill_bg.lighter(115)

        # ── draw pill body ───────────────────────────────────────────────
        from PyQt6.QtCore import QRectF
        r = self.rect()
        inset = 0.5
        pill_rect_f = QRectF(r.x() + inset, r.y() + inset, r.width() - inset, r.height() - inset)

        # fill via stylesheet base color is gone — draw solid tinted bg
        # First draw the tag's base color (from the QPalette button role
        # which the stylesheet sets), then overlay our state tint
        base_hex = str(self.property("baseColor") or "#2A3140")
        base = QColor(base_hex)
        base.setAlphaF(1.0)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(base)
        p.drawRoundedRect(pill_rect_f, radius, radius)

        p.setBrush(pill_bg)
        p.drawRoundedRect(pill_rect_f, radius, radius)

        # border
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(pill_bdr, 1.0))
        p.drawRoundedRect(pill_rect_f, radius, radius)

        # ── separator line before × zone ────────────────────────────────
        sep_x = w - h
        sep_color = QColor(pill_bdr)
        sep_color.setAlphaF(pill_bdr.alphaF() * 0.6)
        p.setPen(QPen(sep_color, 1))
        margin = int(h * 0.25)
        p.drawLine(sep_x, margin, sep_x, h - margin)

        # ── tag label ────────────────────────────────────────────────────
        font = p.font()
        font.setPointSizeF(font.pointSizeF())
        font.setWeight(600 if state != "none" else 400)
        p.setFont(font)
        p.setPen(text_col)
        label_rect = QRect(int(h * 0.45), 0, sep_x - int(h * 0.45), h)
        p.drawText(label_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())

        # ── × button zone ────────────────────────────────────────────────
        x_rect = QRect(sep_x, 0, h, h)

        if self._exclude_hovered or self._exclude_pressed:
            x_bg = QColor(220, 38, 38, int(0.55 * 255) if not self._exclude_pressed else int(0.75 * 255))
            p.setBrush(x_bg)
            p.setPen(Qt.PenStyle.NoPen)
            # right half pill only
            p.setClipRect(x_rect)
            p.drawRoundedRect(pill_rect_f, radius, radius)
            p.setClipping(False)

        # draw no-entry icon: circle + diagonal slash
        cx, cy = x_rect.center().x(), x_rect.center().y()
        icon_r = int(h * 0.22)
        icon_color = QColor("#ff8080" if self._exclude_hovered or self._exclude_pressed
                            else ("#888888" if is_dark else "#777777"))
        pen = QPen(icon_color, 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        from PyQt6.QtCore import QRectF as _QRectF
        p.drawEllipse(_QRectF(cx - icon_r, cy - icon_r, icon_r * 2, icon_r * 2))
        slash = int(icon_r * 0.68)
        p.drawLine(cx - slash, cy + slash, cx + slash, cy - slash)

        p.end()


class ActiveFilterRow(QWidget):
    """Fixed-width active filter row with manually positioned horizontal chips."""

    _ROW_HEIGHT = 36
    _CONTENT_HEIGHT = 22
    _CHIP_HEIGHT = 21

    def __init__(self, label_text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(0)
        self.setFixedHeight(self._ROW_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        label = QLabel(label_text)
        label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        layout.addWidget(label, 0, Qt.AlignmentFlag.AlignVCenter)

        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("ActiveFilterScrollArea")
        self.scroll_area.setWidgetResizable(False)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.scroll_area.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setMinimumWidth(0)
        self.scroll_area.setFixedHeight(self._ROW_HEIGHT)
        self.scroll_area.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.scroll_area.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.scroll_area.viewport().setAutoFillBackground(False)

        self.content = QWidget()
        self.content.setObjectName("ActiveFilterContent")
        self.content.setFixedHeight(self._CONTENT_HEIGHT)
        self.content.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.content.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.scroll_area.setWidget(self.content)
        layout.addWidget(self.scroll_area, 1)

    def set_tags(
        self,
        tags: list[str],
        remove_callback,
        stylesheet: str,
    ) -> None:
        scroll_bar = self.scroll_area.horizontalScrollBar()
        scroll_value = scroll_bar.value()

        for child in self.content.findChildren(QPushButton):
            child.hide()
            child.setParent(None)
            child.deleteLater()

        x = 0
        spacing = 6
        for tag in tags:
            chip = QPushButton(f"{tag}  ×", self.content)
            chip.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            chip.setToolTip("Remove this filter")
            chip.clicked.connect(lambda _checked=False, tag=tag: remove_callback(tag))
            chip.setStyleSheet(stylesheet)
            chip.adjustSize()
            chip.setFixedSize(chip.sizeHint().width(), self._CHIP_HEIGHT)
            chip.move(x, max(0, (self._CONTENT_HEIGHT - self._CHIP_HEIGHT) // 2))
            chip.show()
            x += chip.width() + spacing

        width = max(0, x - spacing)
        self.content.setFixedSize(width, self._CONTENT_HEIGHT)
        self.content.updateGeometry()
        scroll_bar.setValue(min(scroll_value, scroll_bar.maximum()))
        QTimer.singleShot(
            0,
            lambda value=scroll_value, bar=scroll_bar: bar.setValue(min(value, bar.maximum())),
        )


class TagPanel(QWidget):
    """Displays available tags as selectable pill-style buttons."""

    filters_changed = pyqtSignal(list, str, list)
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
        self._theme_name: ThemeName = DEFAULT_THEME
        self._tag_buttons: dict[str, TagFilterButton] = {}
        self._tag_base_colors: dict[str, str] = {}
        self._tag_states: dict[str, str] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("Tag filter mode:"))

        self.mode_toggle = ModeToggle()
        self.mode_toggle.set_mode("OR")
        self.mode_toggle.toggled.connect(lambda _mode: self._emit_state())

        top_row.addWidget(self.mode_toggle)
        top_row.addStretch(1)

        self.clear_button = QPushButton("Clear all")
        self.clear_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.clear_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_button.setFixedHeight(28)
        self.clear_button.setStyleSheet(destructive_secondary_action_button_stylesheet(self._theme_name))
        self.clear_button.clicked.connect(self.clear_selection)
        top_row.addWidget(self.clear_button)
        layout.addLayout(top_row)

        self.active_filters_widget = QWidget()
        self.active_filters_widget.setMinimumWidth(0)
        self.active_filters_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        active_filters_layout = QVBoxLayout(self.active_filters_widget)
        active_filters_layout.setContentsMargins(8, 0, 8, 0)
        active_filters_layout.setSpacing(1)

        self.include_filters_row = ActiveFilterRow("Include:")
        self.exclude_filters_row = ActiveFilterRow("Exclude:")

        active_filters_layout.addWidget(self.include_filters_row)
        active_filters_layout.addWidget(self.exclude_filters_row)
        self.active_filters_widget.setVisible(False)
        layout.addWidget(self.active_filters_widget)

        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("TagCloudScrollArea")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.scroll_area.viewport().setAutoFillBackground(False)

        self.tags_container = QWidget()
        self.tags_container.setObjectName("TagCloudContent")
        self.tags_container.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.tags_layout = QGridLayout(self.tags_container)
        self.tags_layout.setHorizontalSpacing(8)
        self.tags_layout.setVerticalSpacing(8)
        self.tags_layout.setContentsMargins(8, 4, 8, 0)
        self.scroll_area.setWidget(self.tags_container)
        layout.addWidget(self.scroll_area)

    def apply_theme(self, theme_name: ThemeName) -> None:
        self._theme_name = normalize_theme_name(theme_name)
        self.clear_button.setStyleSheet(destructive_secondary_action_button_stylesheet(self._theme_name))
        self._refresh_tag_styles()
        self._update_active_filters()
        self.mode_toggle.update()

    def set_tags(self, tags: list[str]) -> None:
        """Render sorted unique tags as selectable rounded buttons."""
        while self.tags_layout.count():
            item = self.tags_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._tag_buttons.clear()
        self._tag_base_colors.clear()
        self._tag_states.clear()

        columns = 3
        for index, tag in enumerate(sorted(set(tags), key=tag_sort_key)):
            button = TagFilterButton(tag)
            button.setCheckable(True)
            button.setMinimumHeight(28)
            button.clicked.connect(lambda _checked=False, tag=tag: self._on_tag_clicked(tag))
            button.exclude_clicked.connect(lambda tag=tag: self._on_tag_exclude_clicked(tag))
            base_color = self._BASE_TAG_COLORS[index % len(self._BASE_TAG_COLORS)]
            self._tag_base_colors[tag] = base_color
            self._tag_states[tag] = "none"
            button.setProperty("tagState", "none")
            button.setProperty("baseColor", base_color)
            button.setStyleSheet(self._build_tag_stylesheet(base_color, "none"))
            row = index // columns
            col = index % columns
            self.tags_layout.addWidget(button, row, col)
            self._tag_buttons[tag] = button

        self._update_active_filters()
        self._emit_state()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() in {QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange}:
            self._refresh_tag_styles()
            self._update_active_filters()

    def selected_tags(self) -> list[str]:
        """Return selected tags in stable alphabetical order from initial tag mapping."""
        return [tag for tag in self._tag_buttons if self._tag_states.get(tag) == "include"]

    def excluded_tags(self) -> list[str]:
        """Return tags that should be excluded from results."""
        return [tag for tag in self._tag_buttons if self._tag_states.get(tag) == "exclude"]

    def mode(self) -> str:
        """Return current filter mode: AND or OR."""
        return self.mode_toggle.mode()

    def clear_selection(self) -> None:
        """Uncheck all tag buttons."""
        for tag in self._tag_buttons:
            self._set_tag_state(tag, "none")
        self._reorder_tags()
        self._emit_state()

    def _on_tag_clicked(self, tag: str) -> None:
        """Handle tag click by reordering tags and emitting state."""
        current_state = self._tag_states.get(tag, "none")
        next_state = "none" if current_state == "include" else "include"

        self._set_tag_state(tag, next_state)
        self._reorder_tags()
        self._emit_state()

    def _on_tag_exclude_clicked(self, tag: str) -> None:
        """Handle the emoji mini-button by toggling exclusion for a tag."""
        current_state = self._tag_states.get(tag, "none")
        next_state = "none" if current_state == "exclude" else "exclude"

        self._set_tag_state(tag, next_state)
        self._reorder_tags()
        self._emit_state()

    def _reorder_tags(self) -> None:
        """Rebuild the grid with only tags that are still available to choose."""
        scroll_bar = self.scroll_area.verticalScrollBar()
        scroll_value = scroll_bar.value()

        available_tags = [
            tag
            for tag in self._tag_buttons
            if self._tag_states.get(tag, "none") == "none"
        ]
        available_tags.sort(key=tag_sort_key)

        columns = 3
        for button in self._tag_buttons.values():
            self.tags_layout.removeWidget(button)
            button.setVisible(False)

        for index, tag in enumerate(available_tags):
            button = self._tag_buttons[tag]
            button.setVisible(True)
            row = index // columns
            col = index % columns
            self.tags_layout.addWidget(button, row, col)

        scroll_bar.setValue(min(scroll_value, scroll_bar.maximum()))
        QTimer.singleShot(
            0,
            lambda value=scroll_value, bar=scroll_bar: bar.setValue(min(value, bar.maximum())),
        )

    def _set_tag_state(self, tag: str, state: str) -> None:
        self._tag_states[tag] = state
        button = self._tag_buttons[tag]
        button.blockSignals(True)
        button.setChecked(state != "none")
        button.blockSignals(False)
        button.setProperty("tagState", state)
        self._refresh_tag_button(tag)

    def _remove_filter(self, tag: str) -> None:
        self._set_tag_state(tag, "none")
        self._reorder_tags()
        self._emit_state()

    def _emit_state(self) -> None:
        self._update_active_filters()
        self.filters_changed.emit(self.selected_tags(), self.mode(), self.excluded_tags())

    def _update_active_filters(self) -> None:
        included_tags = self.selected_tags()
        excluded_tags = self.excluded_tags()

        self.include_filters_row.set_tags(
            included_tags,
            self._remove_filter,
            self._build_chip_stylesheet("include"),
        )
        self.exclude_filters_row.set_tags(
            excluded_tags,
            self._remove_filter,
            self._build_chip_stylesheet("exclude"),
        )
        self.include_filters_row.setVisible(bool(included_tags))
        self.exclude_filters_row.setVisible(bool(excluded_tags))
        self.active_filters_widget.setVisible(bool(included_tags or excluded_tags))

    @staticmethod
    def _hex_to_rgba(hex_color: str, alpha: float) -> str:
        """Convert a hex color to an RGBA value for Qt stylesheets."""
        clean_hex = hex_color.lstrip("#")
        red = int(clean_hex[0:2], 16)
        green = int(clean_hex[2:4], 16)
        blue = int(clean_hex[4:6], 16)
        return f"rgba({red}, {green}, {blue}, {alpha})"

    def _refresh_tag_styles(self) -> None:
        for tag in self._tag_buttons:
            self._refresh_tag_button(tag)

    def _refresh_tag_button(self, tag: str) -> None:
        button = self._tag_buttons[tag]
        state = self._tag_states.get(tag, "none")
        base_color = self._tag_base_colors.get(tag, self._BASE_TAG_COLORS[0])
        button.setText(tag)
        button.setProperty("baseColor", base_color)
        button.setStyleSheet(self._build_tag_stylesheet(base_color, state))

    def _build_tag_stylesheet(self, base_color: str, state: str) -> str:
        """Suppress Qt's native button rendering; paintEvent handles everything."""
        return """
            QPushButton {
                border: none;
                border-radius: 0px;
                padding: 0px;
                background-color: transparent;
                color: transparent;
            }
        """

    def _build_chip_stylesheet(self, state: str) -> str:
        is_dark = self.palette().window().color().lightness() < 128
        if state == "exclude":
            background = "rgba(220, 38, 38, 0.72)" if is_dark else "rgba(220, 38, 38, 0.14)"
            hover_background = "rgba(239, 68, 68, 0.82)" if is_dark else "rgba(220, 38, 38, 0.22)"
            border_color = "rgba(248, 113, 113, 0.95)" if is_dark else "rgba(185, 28, 28, 0.55)"
            text_color = "#ffffff" if is_dark else "#7f1d1d"
        else:
            background = "rgba(59, 130, 246, 0.55)" if is_dark else "rgba(37, 99, 235, 0.14)"
            hover_background = "rgba(96, 165, 250, 0.66)" if is_dark else "rgba(37, 99, 235, 0.22)"
            border_color = "rgba(147, 197, 253, 0.95)" if is_dark else "rgba(37, 99, 235, 0.55)"
            text_color = "#ffffff" if is_dark else "#1e3a8a"

        return f"""
            QPushButton {{
                border: 1px solid {border_color};
                border-radius: 5px;
                padding: 0px 7px;
                background-color: {background};
                color: {text_color};
                text-align: center;
                font-weight: 650;
            }}
            QPushButton:hover {{
                background-color: {hover_background};
            }}
        """
