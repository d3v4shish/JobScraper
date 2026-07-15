#!/usr/bin/env python3
"""Focused pane widgets for the desktop workbench shell."""

from __future__ import annotations

from typing import Any, Dict, Sequence

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableView,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
)

from .models import BusyStrip
from .theme import PANE_SPACING


def create_menu_button(text: str, parent: QWidget) -> tuple[QToolButton, QMenu]:
    """Create a compact text-only tool button with an attached menu."""
    button = QToolButton(parent)
    button.setText(text)
    button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
    button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    menu = QMenu(button)
    button.setMenu(menu)
    return button, menu


class CommandBar(QWidget):
    """Top command strip with high-frequency actions and global state."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the compact action row shown above the workbench."""
        super().__init__(parent)
        self.setObjectName("CommandBar")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(PANE_SPACING)
        self.scrape_button = QPushButton("Run Scrape", self)
        self.stop_button = QPushButton("Stop", self)
        self.stop_button.setEnabled(False)
        self.reload_button = QPushButton("Reload", self)
        self.tools_button, self.tools_menu = create_menu_button("Tools", self)
        self.command_summary = QLabel("", self)
        self.command_summary.setObjectName("StateStrip")
        self.command_summary.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.command_summary.setMinimumWidth(0)
        self.command_summary.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.global_busy = BusyStrip(self)
        self.global_busy.setMinimumWidth(240)
        for widget in [self.scrape_button, self.stop_button, self.reload_button, self.tools_button]:
            layout.addWidget(widget, 0)
        layout.addStretch(1)
        layout.addWidget(self.command_summary, 1)
        layout.addWidget(self.global_busy, 0)


class SettingsPanel(QWidget):
    """Compact settings drawer for paths, matching profile, and AI settings."""

    def __init__(
        self,
        *,
        db_path: str,
        sources_path: str,
        watchlist_path: str,
        log_path: str,
        reports_path: str,
        local_ai_config: Dict[str, Any],
        parent: QWidget | None = None,
    ) -> None:
        """Build the hidden settings drawer used by the command bar."""
        super().__init__(parent)
        self.setObjectName("SettingsPanel")
        grid = QGridLayout(self)
        grid.setContentsMargins(8, 8, 8, 8)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)

        self.db_path_edit = QLineEdit(db_path, self)
        self.sources_path_edit = QLineEdit(sources_path, self)
        self.watchlist_path_edit = QLineEdit(watchlist_path, self)
        self.log_path_edit = QLineEdit(log_path, self)
        self.log_path_edit.setReadOnly(True)
        self.reports_path_edit = QLineEdit(reports_path, self)
        self.reports_path_edit.setReadOnly(True)
        self.remote_checkbox = QCheckBox("Remote", self)
        self.remote_checkbox.setChecked(True)
        self.india_checkbox = QCheckBox("India office/hybrid", self)
        self.india_checkbox.setChecked(True)
        self.interests_edit = QLineEdit(self)
        self.exclude_edit = QLineEdit(self)
        self.concurrency_spin = QSpinBox(self)
        self.concurrency_spin.setRange(1, 64)
        self.concurrency_spin.setValue(6)
        self.concurrency_spin.setMaximumWidth(88)
        self.http_concurrency_spin = QSpinBox(self)
        self.http_concurrency_spin.setRange(1, 128)
        self.http_concurrency_spin.setValue(32)
        self.http_concurrency_spin.setMaximumWidth(88)
        self.hn_parser_combo = QComboBox(self)
        self.hn_parser_combo.addItem("Auto", "auto")
        self.hn_parser_combo.addItem("Local AI", "local_ai")
        self.hn_parser_combo.addItem("Local Heuristics", "local")
        self.hn_parser_combo.addItem("OpenAI", "openai")
        self.hn_parser_combo.setMinimumWidth(140)
        self.local_ai_url_edit = QLineEdit(str(local_ai_config.get("base_url") or ""), self)
        self.local_ai_model_edit = QLineEdit(str(local_ai_config.get("model") or ""), self)
        self.local_ai_model_edit.setPlaceholderText("model")
        self.local_ai_url_edit.setPlaceholderText("http://127.0.0.1:11434")
        self.local_ai_url_edit.setMinimumWidth(260)
        self.local_ai_model_edit.setMinimumWidth(150)
        self.ai_status_label = QLabel("Checking AI availability...", self)
        self.ai_status_label.setObjectName("PaneMeta")
        self.ai_status_label.setMinimumWidth(0)
        self.ai_status_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.ai_status_button = QPushButton("Check AI", self)

        grid.setColumnStretch(1, 7)
        grid.setColumnStretch(3, 7)
        grid.setColumnStretch(5, 8)
        grid.setColumnStretch(7, 5)
        grid.setColumnStretch(9, 6)
        grid.setColumnStretch(11, 3)

        grid.addWidget(QLabel("DB", self), 0, 0)
        grid.addWidget(self.db_path_edit, 0, 1, 1, 3)
        grid.addWidget(QLabel("Sources", self), 0, 4)
        grid.addWidget(self.sources_path_edit, 0, 5, 1, 3)
        grid.addWidget(QLabel("Logs", self), 0, 8)
        grid.addWidget(self.log_path_edit, 0, 9, 1, 3)

        grid.addWidget(QLabel("Location", self), 1, 0)
        location_row = QHBoxLayout()
        location_row.setContentsMargins(0, 0, 0, 0)
        location_row.addWidget(self.remote_checkbox)
        location_row.addWidget(self.india_checkbox)
        location_row.addStretch(1)
        location_widget = QWidget(self)
        location_widget.setLayout(location_row)
        grid.addWidget(location_widget, 1, 1)
        grid.addWidget(QLabel("Tech Interests", self), 1, 2)
        grid.addWidget(self.interests_edit, 1, 3, 1, 3)
        grid.addWidget(QLabel("Exclude", self), 1, 6)
        grid.addWidget(self.exclude_edit, 1, 7)
        grid.addWidget(QLabel("Local AI", self), 1, 8)
        local_ai_row = QHBoxLayout()
        local_ai_row.setContentsMargins(0, 0, 0, 0)
        local_ai_row.setSpacing(6)
        local_ai_row.addWidget(self.local_ai_url_edit, 1)
        local_ai_row.addWidget(self.local_ai_model_edit, 0)
        local_ai_widget = QWidget(self)
        local_ai_widget.setLayout(local_ai_row)
        grid.addWidget(local_ai_widget, 1, 9, 1, 3)

        grid.addWidget(QLabel("Concurrency", self), 2, 0)
        grid.addWidget(self.concurrency_spin, 2, 1)
        grid.addWidget(QLabel("HTTP Cap", self), 2, 2)
        grid.addWidget(self.http_concurrency_spin, 2, 3)
        grid.addWidget(QLabel("HN Parse", self), 2, 4)
        grid.addWidget(self.hn_parser_combo, 2, 5)
        grid.addWidget(QLabel("AI Status", self), 2, 6)
        grid.addWidget(self.ai_status_label, 2, 7, 1, 4)
        grid.addWidget(self.ai_status_button, 2, 11)
        grid.addWidget(QLabel("Watchlist", self), 3, 0)
        grid.addWidget(self.watchlist_path_edit, 3, 1, 1, 5)
        grid.addWidget(QLabel("Reports", self), 3, 6)
        grid.addWidget(self.reports_path_edit, 3, 7, 1, 5)


class CompaniesPane(QGroupBox):
    """Company filter pane with bulk-selection actions and dense table view."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the company sidebar used to narrow the jobs view."""
        super().__init__("Companies", parent)
        self.setObjectName("PaneBox")
        self.setMinimumWidth(220)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 10, 6, 6)
        layout.setSpacing(PANE_SPACING)
        self.busy = BusyStrip(self)
        layout.addWidget(self.busy)
        button_row = QHBoxLayout()
        button_row.setSpacing(6)
        self.selection_button, self.selection_menu = create_menu_button("Selection", self)
        button_row.addWidget(self.selection_button)
        button_row.addStretch(1)
        layout.addLayout(button_row)
        self.table = QTableView(self)
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table, 1)


class JobsPane(QGroupBox):
    """Primary jobs pane with filters, summary, and the main result table."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the central jobs scanning pane."""
        super().__init__("Jobs", parent)
        self.setObjectName("PaneBox")
        self.setMinimumWidth(620)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 10, 6, 6)
        layout.setSpacing(PANE_SPACING)
        self.busy = BusyStrip(self)
        layout.addWidget(self.busy)
        filters_grid = QGridLayout()
        filters_grid.setContentsMargins(0, 0, 0, 0)
        filters_grid.setHorizontalSpacing(8)
        filters_grid.setVerticalSpacing(6)
        self.matching_only_checkbox = QCheckBox("Matching only", self)
        self.matching_only_checkbox.setChecked(True)
        self.open_only_checkbox = QCheckBox("Open only", self)
        self.open_only_checkbox.setChecked(True)
        self.group_by_company_checkbox = QCheckBox("Group by company", self)
        self.founding_only_checkbox = QCheckBox("Founding engineer", self)
        self.portal_filter_combo = QComboBox(self)
        self.portal_filter_combo.addItem("All sources", "")
        self.source_filter_combo = QComboBox(self)
        self.source_filter_combo.addItem("All source rows", 0)
        self.source_tag_combo = QComboBox(self)
        self.source_tag_combo.addItem("All tags", "")
        for tag in ("security", "systems", "india", "remote", "big-tech", "marketplace", "quant", "ai"):
            self.source_tag_combo.addItem(tag, tag)
        self.stack_filter_combo = QComboBox(self)
        self.stack_filter_combo.addItem("All stacks", "")
        self.hn_review_combo = QComboBox(self)
        self.hn_review_combo.addItem("All HN jobs", "")
        self.hn_review_combo.addItem("Parsed companies", "parsed")
        self.hn_review_combo.addItem("Fallback bucket", "fallback")
        self.hn_review_combo.setEnabled(False)
        self.search_edit = QLineEdit(self)
        self.search_edit.setPlaceholderText("Search title, company, location, description")
        self.filter_preset_combo = QComboBox(self)
        self.filter_preset_combo.addItem("Filter presets", "")
        self.save_filter_preset_button = QPushButton("Save", self)
        self.delete_filter_preset_button = QPushButton("Delete", self)
        filters_grid.addWidget(self.matching_only_checkbox, 0, 0)
        filters_grid.addWidget(self.open_only_checkbox, 0, 1)
        filters_grid.addWidget(self.group_by_company_checkbox, 0, 2)
        filters_grid.addWidget(self.founding_only_checkbox, 0, 3)
        filters_grid.addWidget(QLabel("Source", self), 0, 4)
        filters_grid.addWidget(self.portal_filter_combo, 0, 5)
        filters_grid.addWidget(QLabel("Stack", self), 0, 6)
        filters_grid.addWidget(self.stack_filter_combo, 0, 7)
        filters_grid.addWidget(QLabel("Source Row", self), 1, 0)
        filters_grid.addWidget(self.source_filter_combo, 1, 1, 1, 2)
        filters_grid.addWidget(QLabel("HN View", self), 1, 3)
        filters_grid.addWidget(self.hn_review_combo, 1, 4)
        filters_grid.addWidget(QLabel("Tag", self), 1, 5)
        filters_grid.addWidget(self.source_tag_combo, 1, 6)
        filters_grid.addWidget(QLabel("Search", self), 2, 0)
        filters_grid.addWidget(self.search_edit, 2, 1, 1, 4)
        filters_grid.addWidget(QLabel("Preset", self), 2, 5)
        filters_grid.addWidget(self.filter_preset_combo, 2, 6)
        preset_buttons = QHBoxLayout()
        preset_buttons.setContentsMargins(0, 0, 0, 0)
        preset_buttons.setSpacing(4)
        preset_buttons.addWidget(self.save_filter_preset_button)
        preset_buttons.addWidget(self.delete_filter_preset_button)
        preset_widget = QWidget(self)
        preset_widget.setLayout(preset_buttons)
        filters_grid.addWidget(preset_widget, 2, 7)
        layout.addLayout(filters_grid)
        self.summary_label = QLabel("", self)
        self.summary_label.setObjectName("PaneMeta")
        self.summary_label.setMinimumWidth(0)
        self.summary_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.summary_label)
        self.table = QTableView(self)
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table, 1)


class DescriptionPane(QGroupBox):
    """Selected-job description pane for the Workbench surface."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the selected-job description pane."""
        super().__init__("Selected Job", parent)
        self.setObjectName("PaneBox")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 10, 6, 6)
        layout.setSpacing(PANE_SPACING)
        self.header = QLabel("No job selected", self)
        self.header.setObjectName("PanelHeader")
        self.header.setMinimumWidth(0)
        self.header.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.header)
        self.description_busy = BusyStrip(self)
        layout.addWidget(self.description_busy)
        self.description_browser = QTextBrowser(self)
        self.description_browser.setOpenExternalLinks(True)
        layout.addWidget(self.description_browser, 1)


class AnalyticsPane(QWidget):
    """Top-level analytics tab for the current jobs scope."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the analytics workspace."""
        super().__init__(parent)
        self.setObjectName("AnalyticsPane")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(PANE_SPACING)
        self.analytics_busy = BusyStrip(self)
        layout.addWidget(self.analytics_busy)
        self.analytics_browser = QTextBrowser(self)
        self.analytics_browser.setOpenExternalLinks(True)
        layout.addWidget(self.analytics_browser, 1)


class RoadmapPane(QWidget):
    """Top-level topic roadmap tab used for study planning."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the roadmap workspace."""
        super().__init__(parent)
        self.setObjectName("RoadmapPane")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(PANE_SPACING)
        self.roadmap_busy = BusyStrip(self)
        layout.addWidget(self.roadmap_busy)
        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(6)
        controls.addWidget(QLabel("Scope", self))
        self.roadmap_scope_combo = QComboBox(self)
        self.roadmap_scope_combo.addItem("Selected jobs/companies", "selected")
        self.roadmap_scope_combo.addItem("Current filters", "filters")
        self.roadmap_scope_combo.addItem("Whole DB", "all")
        self.roadmap_refresh_button = QPushButton("Refresh", self)
        controls.addWidget(self.roadmap_scope_combo)
        controls.addWidget(self.roadmap_refresh_button)
        controls.addStretch(1)
        layout.addLayout(controls)
        self.roadmap_summary_label = QLabel("", self)
        self.roadmap_summary_label.setObjectName("PaneMeta")
        self.roadmap_summary_label.setMinimumWidth(0)
        self.roadmap_summary_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.roadmap_summary_label)
        self.roadmap_browser = QTextBrowser(self)
        self.roadmap_browser.setOpenExternalLinks(True)
        layout.addWidget(self.roadmap_browser, 1)


class ActivityPane(QGroupBox):
    """Collapsible activity log pane for scrape and worker events."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the low-visibility activity pane shown beneath the workbench."""
        super().__init__("Activity", parent)
        self.setObjectName("ActivityPane")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 10, 6, 6)
        self.activity_log = QPlainTextEdit(self)
        self.activity_log.setReadOnly(True)
        self.activity_log.setMaximumBlockCount(2000)
        self.activity_log.setCenterOnScroll(False)
        layout.addWidget(self.activity_log)


class SourcesPane(QWidget):
    """Sources admin tab with public-source diagnostics and source table."""

    def __init__(
        self,
        parent: QWidget | None = None,
    ) -> None:
        """Build the source-admin tab for public source diagnostics."""
        super().__init__(parent)
        self.setObjectName("SourcesPane")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(PANE_SPACING)
        self.busy = BusyStrip(self)
        layout.addWidget(self.busy)

        source_row = QHBoxLayout()
        source_row.setContentsMargins(0, 0, 0, 0)
        source_row.setSpacing(6)
        self.sources_label = QLabel("Source Diagnostics", self)
        self.sources_label.setObjectName("PaneMeta")
        source_row.addWidget(self.sources_label, 0)
        source_row.addWidget(QLabel("Health", self), 0)
        self.source_health_filter_combo = QComboBox(self)
        for label, value in (
            ("All health", ""),
            ("Healthy", "healthy"),
            ("Disabled", "disabled"),
            ("Blocked", "blocked"),
            ("Parser failure", "parser failure"),
            ("New", "new"),
        ):
            self.source_health_filter_combo.addItem(label, value)
        source_row.addWidget(self.source_health_filter_combo, 0)
        source_row.addStretch(1)
        self.last_scrape_report_label = QLabel("Last scrape: not run in this session", self)
        self.last_scrape_report_label.setObjectName("PaneMeta")
        self.last_scrape_report_label.setMinimumWidth(0)
        self.last_scrape_report_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        source_row.addWidget(self.last_scrape_report_label, 1)
        self.source_probe_button = QPushButton("Probe Watchlist", self)
        source_row.addWidget(self.source_probe_button, 0)
        self.source_focus_button = QPushButton("Focus In Workbench", self)
        self.source_focus_button.setEnabled(False)
        source_row.addWidget(self.source_focus_button, 0)
        self.source_edit_button = QPushButton("Edit Source", self)
        self.source_edit_button.setEnabled(False)
        source_row.addWidget(self.source_edit_button, 0)
        layout.addLayout(source_row)
        self.table = QTableView(self)
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table, 1)
