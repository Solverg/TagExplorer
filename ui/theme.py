"""Application-owned color themes for TagExplorer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from PyQt6.QtCore import QSettings
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

ThemeName = Literal["dark", "light"]

DEFAULT_THEME: ThemeName = "dark"
THEME_SETTINGS_KEY = "appearance/theme"


@dataclass(frozen=True)
class ThemeSpec:
    name: ThemeName
    window: str
    panel: str
    panel_border: str
    control: str
    control_hover: str
    control_pressed: str
    control_border: str
    control_text: str
    text: str
    muted_text: str
    disabled_text: str
    base: str
    alternate_base: str
    nav: str
    nav_border: str
    menu: str
    menu_selected: str
    highlight: str
    highlighted_text: str
    separator: str
    title_bar: str


THEMES: dict[ThemeName, ThemeSpec] = {
    "dark": ThemeSpec(
        name="dark",
        window="#0b0f15",
        panel="#151a22",
        panel_border="#333b49",
        control="#252c38",
        control_hover="#2f3847",
        control_pressed="#35445a",
        control_border="#3c4657",
        control_text="#f2f6fb",
        text="#f2f6fb",
        muted_text="#b5c1d2",
        disabled_text="#6d7888",
        base="#10151d",
        alternate_base="#181f2a",
        nav="#202631",
        nav_border="#3a4351",
        menu="#1b222e",
        menu_selected="#2c3c55",
        highlight="#4d7dc4",
        highlighted_text="#ffffff",
        separator="#3b4656",
        title_bar="#0b0f15",
    ),
    "light": ThemeSpec(
        name="light",
        window="#e8edf4",
        panel="#f8fafc",
        panel_border="#c9d3e0",
        control="#edf2f7",
        control_hover="#dfe9f6",
        control_pressed="#d3e1f3",
        control_border="#c1ccd9",
        control_text="#172033",
        text="#172033",
        muted_text="#5d6b82",
        disabled_text="#9aa4b2",
        base="#ffffff",
        alternate_base="#f2f5f9",
        nav="#f3f6fa",
        nav_border="#cfd8e3",
        menu="#ffffff",
        menu_selected="#e3edf9",
        highlight="#2f6fd6",
        highlighted_text="#ffffff",
        separator="#c4cedb",
        title_bar="#e8edf4",
    ),
}


def normalize_theme_name(value: object) -> ThemeName:
    """Return a supported theme name, falling back to the app default."""
    return "light" if str(value).casefold() == "light" else DEFAULT_THEME


def load_theme_name(settings: QSettings | None = None) -> ThemeName:
    settings = settings or QSettings()
    return normalize_theme_name(settings.value(THEME_SETTINGS_KEY, DEFAULT_THEME))


def save_theme_name(theme_name: ThemeName, settings: QSettings | None = None) -> None:
    settings = settings or QSettings()
    settings.setValue(THEME_SETTINGS_KEY, normalize_theme_name(theme_name))


def theme_spec(theme_name: ThemeName) -> ThemeSpec:
    return THEMES[normalize_theme_name(theme_name)]


def is_dark(theme_name: ThemeName) -> bool:
    return normalize_theme_name(theme_name) == "dark"


def _qcolor(value: str) -> QColor:
    if value.startswith("rgba(") and value.endswith(")"):
        channels = [part.strip() for part in value[5:-1].split(",")]
        if len(channels) == 4:
            red, green, blue = (int(channel) for channel in channels[:3])
            alpha = float(channels[3])
            if alpha <= 1:
                alpha = round(alpha * 255)
            return QColor(red, green, blue, int(alpha))
    return QColor(value)


def build_palette(theme_name: ThemeName) -> QPalette:
    spec = theme_spec(theme_name)
    palette = QPalette()

    window = _qcolor(spec.window)
    panel = _qcolor(spec.panel)
    base = _qcolor(spec.base)
    alternate_base = _qcolor(spec.alternate_base)
    text = _qcolor(spec.text)
    muted = _qcolor(spec.muted_text)
    disabled = _qcolor(spec.disabled_text)
    control = _qcolor(spec.control)
    highlight = _qcolor(spec.highlight)
    highlighted_text = _qcolor(spec.highlighted_text)

    palette.setColor(QPalette.ColorRole.Window, window)
    palette.setColor(QPalette.ColorRole.WindowText, text)
    palette.setColor(QPalette.ColorRole.Base, base)
    palette.setColor(QPalette.ColorRole.AlternateBase, alternate_base)
    palette.setColor(QPalette.ColorRole.ToolTipBase, panel)
    palette.setColor(QPalette.ColorRole.ToolTipText, text)
    palette.setColor(QPalette.ColorRole.Text, text)
    palette.setColor(QPalette.ColorRole.Button, control)
    palette.setColor(QPalette.ColorRole.ButtonText, text)
    palette.setColor(QPalette.ColorRole.BrightText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.Link, highlight)
    palette.setColor(QPalette.ColorRole.Highlight, highlight)
    palette.setColor(QPalette.ColorRole.HighlightedText, highlighted_text)
    palette.setColor(QPalette.ColorRole.Light, panel.lighter(120))
    palette.setColor(QPalette.ColorRole.Midlight, panel)
    palette.setColor(QPalette.ColorRole.Mid, muted)
    palette.setColor(QPalette.ColorRole.Dark, muted.darker(125))
    palette.setColor(QPalette.ColorRole.Shadow, QColor("#000000"))

    disabled_group = QPalette.ColorGroup.Disabled
    palette.setColor(disabled_group, QPalette.ColorRole.WindowText, disabled)
    palette.setColor(disabled_group, QPalette.ColorRole.Text, disabled)
    palette.setColor(disabled_group, QPalette.ColorRole.ButtonText, disabled)
    palette.setColor(disabled_group, QPalette.ColorRole.Highlight, muted)
    palette.setColor(disabled_group, QPalette.ColorRole.HighlightedText, window)

    try:
        palette.setColor(QPalette.ColorRole.PlaceholderText, disabled)
    except AttributeError:
        pass

    return palette


def apply_application_theme(app: QApplication, theme_name: ThemeName) -> None:
    """Apply the selected theme without consulting the OS color scheme."""
    normalized = normalize_theme_name(theme_name)
    app.setStyle("Fusion")
    app.setPalette(build_palette(normalized))
    app.setStyleSheet(build_app_stylesheet(normalized))


def secondary_action_button_stylesheet(theme_name: ThemeName = DEFAULT_THEME) -> str:
    spec = theme_spec(theme_name)
    hover_bg = "#313b4b" if is_dark(theme_name) else "#dfe9f6"
    hover_border = "#4a5870" if is_dark(theme_name) else "#b5c5d8"
    pressed_bg = "#39485f" if is_dark(theme_name) else "#d3e1f3"

    return f"""
        QPushButton {{
            border: 1px solid {spec.control_border};
            border-radius: 5px;
            padding: 0px 13px;
            background-color: {spec.control};
            color: {spec.muted_text};
            font-size: 11px;
            font-weight: 500;
        }}
        QPushButton:hover {{
            background-color: {hover_bg};
            border-color: {hover_border};
            color: {spec.text};
        }}
        QPushButton:pressed {{
            background-color: {pressed_bg};
            color: {spec.text};
        }}
    """


def destructive_secondary_action_button_stylesheet(theme_name: ThemeName = DEFAULT_THEME) -> str:
    spec = theme_spec(theme_name)
    if is_dark(theme_name):
        hover_bg = "rgba(220, 60, 60, 0.24)"
        hover_border = "rgba(248, 113, 113, 0.38)"
        hover_text = "#ffb4b4"
        pressed_bg = "rgba(180, 40, 40, 0.38)"
        pressed_text = "#ff9a9a"
    else:
        hover_bg = "rgba(225, 29, 72, 0.12)"
        hover_border = "rgba(225, 29, 72, 0.34)"
        hover_text = "#9f1239"
        pressed_bg = "rgba(225, 29, 72, 0.20)"
        pressed_text = "#7f1d1d"

    return f"""
        QPushButton {{
            border: 1px solid {spec.control_border};
            border-radius: 5px;
            padding: 0px 13px;
            background-color: {spec.control};
            color: {spec.muted_text};
            font-size: 11px;
            font-weight: 500;
        }}
        QPushButton:hover {{
            background-color: {hover_bg};
            border-color: {hover_border};
            color: {hover_text};
        }}
        QPushButton:pressed {{
            background-color: {pressed_bg};
            color: {pressed_text};
        }}
    """


def type_filter_button_stylesheet(theme_name: ThemeName = DEFAULT_THEME) -> str:
    spec = theme_spec(theme_name)
    return f"""
        QPushButton {{
            text-align: left;
            border: none;
            background: transparent;
            font-weight: bold;
            color: {spec.text};
            padding: 4px;
        }}
        QPushButton:hover {{
            color: {spec.control_text};
            background: {spec.control_hover};
            border-radius: 4px;
        }}
    """


def build_app_stylesheet(theme_name: ThemeName) -> str:
    spec = theme_spec(theme_name)
    return f"""
        QMainWindow, QDialog {{
            background-color: {spec.window};
            color: {spec.text};
        }}

        QSplitter {{
            background-color: {spec.window};
        }}

        QWidget {{
            color: {spec.text};
            font-weight: normal;
        }}

        #LeftPanel, #CenterPanel, #PreviewPanel {{
            background-color: {spec.panel};
            border: 1px solid {spec.panel_border};
            border-radius: 0px;
        }}

        #LeftNavSplitter {{
            background: transparent;
        }}

        #LeftNavSplitter::handle {{
            background-color: {spec.separator};
            border-radius: 0px;
        }}

        #PlacesTree, #FolderTree {{
            background-color: {spec.nav};
            border: 1px solid {spec.nav_border};
            border-radius: 2px;
            color: {spec.text};
            padding: 4px;
        }}

        QTreeView, QTreeWidget, QListView, QScrollArea {{
            background-color: {spec.base};
            alternate-background-color: {spec.alternate_base};
            color: {spec.text};
            border: 1px solid {spec.nav_border};
            border-radius: 0px;
            selection-background-color: {spec.highlight};
            selection-color: {spec.highlighted_text};
        }}

        QTreeView::item, QTreeWidget::item, QListView::item {{
            padding: 3px;
            border-radius: 2px;
        }}

        QTreeView::item:selected, QTreeWidget::item:selected, QListView::item:selected {{
            background-color: {spec.highlight};
            color: {spec.highlighted_text};
        }}

        QHeaderView::section {{
            background-color: {spec.alternate_base};
            color: {spec.text};
            border: none;
            border-bottom: 1px solid {spec.nav_border};
            border-right: 1px solid {spec.nav_border};
            padding: 4px 6px;
            font-weight: normal;
        }}

        QPushButton {{
            background-color: {spec.control};
            border: 1px solid {spec.control_border};
            border-radius: 4px;
            padding: 4px;
            color: {spec.control_text};
        }}

        QPushButton:hover {{
            background-color: {spec.control_hover};
        }}

        QPushButton:pressed {{
            background-color: {spec.control_pressed};
        }}

        QPushButton:checked {{
            background-color: {spec.highlight};
            color: {spec.highlighted_text};
        }}

        QPushButton#primary {{
            background-color: {spec.highlight};
            border: 1px solid {spec.highlight};
            color: {spec.highlighted_text};
            font-weight: 600;
        }}

        QPushButton#primary:hover {{
            background-color: {QColor(spec.highlight).lighter(112).name()};
        }}

        QLineEdit {{
            background-color: {spec.base};
            color: {spec.text};
            border: 1px solid {spec.nav_border};
            border-radius: 2px;
            padding: 4px 6px;
        }}

        QCheckBox::indicator {{
            width: 14px;
            height: 14px;
            border: 1px solid {spec.control_border};
            background-color: {spec.base};
        }}

        QCheckBox::indicator:hover {{
            border-color: {spec.muted_text};
            background-color: {spec.alternate_base};
        }}

        QCheckBox::indicator:checked {{
            border: 1px solid {spec.highlight};
            background-color: {spec.highlight};
        }}

        QCheckBox::indicator:disabled {{
            border-color: {spec.disabled_text};
            background-color: {spec.panel};
        }}

        QScrollArea#ActiveFilterScrollArea {{
            background-color: transparent;
            border: none;
        }}

        QWidget#ActiveFilterContent {{
            background-color: transparent;
            border: none;
        }}

        QScrollArea#TagCloudScrollArea {{
            background-color: transparent;
            border: none;
        }}

        QWidget#TagCloudContent {{
            background-color: transparent;
            border: none;
        }}

        QScrollArea#ActiveFilterScrollArea QScrollBar:horizontal {{
            background-color: transparent;
            border: none;
            height: 8px;
        }}

        QScrollArea#ActiveFilterScrollArea QScrollBar::handle:horizontal {{
            background-color: {spec.control_border};
            border: none;
            min-width: 24px;
        }}

        QScrollArea#ActiveFilterScrollArea QScrollBar::handle:horizontal:hover {{
            background-color: {spec.muted_text};
        }}

        QScrollBar:vertical, QScrollBar:horizontal {{
            background-color: {spec.base};
            border: 1px solid {spec.nav_border};
            margin: 0px;
        }}

        QScrollBar:vertical {{
            width: 14px;
        }}

        QScrollBar:horizontal {{
            height: 14px;
        }}

        QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
            background-color: {spec.control_border};
            border: 2px solid {spec.base};
            border-radius: 0px;
            min-height: 22px;
            min-width: 22px;
        }}

        QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{
            background-color: {spec.muted_text};
        }}

        QScrollBar::add-line, QScrollBar::sub-line {{
            width: 0px;
            height: 0px;
            border: none;
            background: transparent;
        }}

        QScrollBar::add-page, QScrollBar::sub-page {{
            background: transparent;
        }}

        QMenuBar {{
            background-color: {spec.menu};
            color: {spec.text};
            font-weight: normal;
        }}

        QMenuBar::item {{
            background: transparent;
            padding: 4px 8px;
        }}

        QMenuBar::item:selected {{
            background-color: {spec.menu_selected};
        }}

        QMenu {{
            background-color: {spec.menu};
            color: {spec.text};
            border: 1px solid {spec.nav_border};
            font-weight: normal;
        }}

        QMenu::item {{
            padding: 6px 28px 6px 12px;
        }}

        QMenu::item:selected {{
            background-color: {spec.menu_selected};
        }}

        QMenu::indicator:checked {{
            background-color: {spec.highlight};
            border: 1px solid {spec.highlight};
        }}

        QCheckBox, QLabel, QStatusBar {{
            color: {spec.text};
            font-weight: normal;
        }}

        QStatusBar {{
            background-color: {spec.window};
        }}

        QFrame[frameShape="4"], QFrame[frameShape="5"] {{
            color: {spec.separator};
        }}

        QProgressBar {{
            border: 1px solid {spec.nav_border};
            border-radius: 4px;
            background-color: {spec.base};
            text-align: center;
            color: {spec.text};
        }}

        QProgressBar::chunk {{
            background-color: {spec.highlight};
            border-radius: 3px;
        }}
    """
