#!/usr/bin/env python3
"""Shared theme tokens and stylesheet for the dense desktop workbench."""

from __future__ import annotations

WINDOW_SIZE = (1920, 1080)
PANE_SPACING = 6
ROW_HEIGHT = 24
CONTROL_HEIGHT = 28
PANEL_BG = "#11161d"
PANEL_BG_ALT = "#0f141b"
PANEL_HEADER = "#161b22"
BORDER = "#21262d"
BORDER_STRONG = "#30363d"
TEXT = "#c9d1d9"
TEXT_BRIGHT = "#f0f6fc"
TEXT_DIM = "#8b949e"
ACCENT = "#4f8cff"
ACCENT_VIOLET = "#6366f1"

APP_STYLE = """
QWidget {{
    background: #0d1117;
    color: {text};
    font-family: Inter, Segoe UI, Arial, sans-serif;
    font-size: 12px;
}}
QMainWindow, QFrame {{
    background: #0d1117;
}}
QLabel#MonoLabel, QLabel#PaneMeta, QLabel#StateStrip, QLabel#PanelHeader,
QPlainTextEdit, QTextBrowser, QTableView, QHeaderView, QLineEdit, QComboBox, QSpinBox {{
    font-family: "JetBrains Mono", Consolas, monospace;
}}
QWidget#CommandBar, QWidget#SettingsPanel, QWidget#SourcesPane {{
    background: {panel_bg};
    border: 1px solid {border};
}}
QWidget#CommandBar {{
    border-bottom: 1px solid {border};
    padding: 0;
}}
QWidget#SettingsPanel {{
    background: {panel_bg_alt};
}}
QGroupBox#PaneBox, QGroupBox#ActivityPane {{
    background: {panel_bg};
    border: 1px solid {border};
    margin-top: 12px;
    padding-top: 10px;
}}
QGroupBox#ActivityPane {{
    background: {panel_bg_alt};
}}
QGroupBox#PaneBox::title, QGroupBox#ActivityPane::title {{
    subcontrol-origin: margin;
    left: 8px;
    padding: 0 4px;
    color: {text_bright};
    background: {panel_bg};
}}
QGroupBox#ActivityPane::title {{
    background: {panel_bg_alt};
}}
QLabel#StateStrip {{
    color: {text_dim};
    padding: 0 4px;
}}
QLabel#PaneMeta {{
    color: {text_dim};
}}
QLabel#PanelHeader {{
    color: {text_bright};
    padding: 0 2px 2px 2px;
}}
QPushButton, QToolButton, QComboBox, QLineEdit, QSpinBox {{
    min-height: {control_height}px;
    max-height: {control_height}px;
    padding: 2px 8px;
    border: 1px solid {border_strong};
    background: {panel_bg_alt};
    color: {text};
    border-radius: 0px;
}}
QPushButton:hover, QToolButton:hover, QComboBox:hover, QLineEdit:hover, QSpinBox:hover {{
    background: {panel_bg};
}}
QPushButton:focus, QToolButton:focus, QComboBox:focus, QLineEdit:focus, QSpinBox:focus {{
    border: 1px solid {accent};
}}
QTableView:focus, QTextBrowser:focus, QPlainTextEdit:focus {{
    border: 1px solid {accent};
}}
QPushButton:disabled, QToolButton:disabled {{
    color: #6e7681;
    border-color: {border};
    background: #0d1117;
}}
QToolButton::menu-indicator {{
    subcontrol-position: right center;
    width: 12px;
}}
QComboBox::drop-down {{
    width: 20px;
    border-left: 1px solid {border};
    background: {panel_bg_alt};
}}
QComboBox QAbstractItemView {{
    background: {panel_bg};
    color: {text};
    border: 1px solid {border};
    selection-background-color: #1c315a;
    selection-color: {text_bright};
}}
QMenu {{
    background: {panel_bg};
    color: {text};
    border: 1px solid {border};
    padding: 3px 0;
}}
QMenu::item {{
    padding: 5px 16px 5px 10px;
}}
QMenu::item:selected {{
    background: {panel_header};
}}
QTabWidget::pane {{
    border: 1px solid {border};
    top: -1px;
    background: {panel_bg};
}}
QTabBar::tab {{
    background: #0d1117;
    border: 1px solid {border};
    padding: 4px 10px;
    min-height: 22px;
    min-width: 150px;
    margin-right: 2px;
    color: {text_dim};
}}
QTabBar::tab:selected {{
    background: {panel_bg};
    border-top: 2px solid {accent_violet};
    color: {text_bright};
}}
QTabBar::tab:hover:!selected {{
    background: {panel_header};
    color: {text};
}}
QTableView {{
    background: {panel_bg_alt};
    alternate-background-color: {panel_bg};
    border: 1px solid {border};
    gridline-color: {border};
    selection-background-color: #233a73;
    selection-color: {text_bright};
}}
QTableCornerButton::section {{
    background: {panel_header};
    border: 1px solid {border};
}}
QHeaderView::section {{
    background: {panel_header};
    color: {text};
    border: 1px solid {border};
    padding: 3px 8px;
}}
QProgressBar {{
    border: 1px solid {border};
    background: {panel_bg_alt};
    text-align: center;
    max-height: 8px;
    min-height: 8px;
}}
QProgressBar::chunk {{
    background: {accent};
}}
QPlainTextEdit, QTextBrowser {{
    background: {panel_bg_alt};
    border: 1px solid {border};
    padding: 6px;
}}
QCheckBox {{
    spacing: 6px;
}}
QCheckBox::indicator {{
    width: 14px;
    height: 14px;
    border: 1px solid {border_strong};
    background: {panel_bg_alt};
}}
QCheckBox::indicator:checked {{
    background: {accent};
}}
QSplitter::handle {{
    background: {border};
}}
QSplitter::handle:hover {{
    background: {accent};
}}
QScrollBar:vertical, QScrollBar:horizontal {{
    background: {panel_bg_alt};
    border: 1px solid {border};
    margin: 0;
}}
QScrollBar:vertical {{
    width: 12px;
}}
QScrollBar:horizontal {{
    height: 12px;
}}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
    background: {border_strong};
    min-height: 24px;
    min-width: 24px;
}}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{
    background: #586272;
}}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{
    background: none;
    border: none;
}}
""".format(
    text=TEXT,
    panel_bg=PANEL_BG,
    panel_bg_alt=PANEL_BG_ALT,
    panel_header=PANEL_HEADER,
    border=BORDER,
    border_strong=BORDER_STRONG,
    text_bright=TEXT_BRIGHT,
    text_dim=TEXT_DIM,
    accent=ACCENT,
    accent_violet=ACCENT_VIOLET,
    control_height=CONTROL_HEIGHT,
)
