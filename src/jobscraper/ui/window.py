#!/usr/bin/env python3
"""Main desktop workbench window for the jobs application."""

from __future__ import annotations

import logging
import json
import os
import sys
from collections import OrderedDict, defaultdict, deque
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, Deque, Dict, List, Optional, Sequence

from PyQt6.QtCore import QThreadPool, QTimer, Qt, QUrl
from PyQt6.QtGui import QAction, QDesktopServices, QIcon
from PyQt6.QtWidgets import QApplication, QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QTabWidget, QVBoxLayout, QWidget

from .. import paths
from ..bootstrap import load_settings, save_settings
from ..scraping import core
from ..storage import db
from ..storage import fs
from ..ai import client as ai_client
from .panes import ActivityPane, AnalyticsPane, CommandBar, CompaniesPane, DescriptionPane, JobsPane, RoadmapPane, SettingsPanel, SourcesPane
from .renderers import html_shell
from .tasks import (
    build_roadmap_payload,
    delete_storage_category_task,
    export_jobs_task,
    import_sources_task,
    initialize_database_task,
    load_ai_status_task,
    load_analytics_view_task,
    load_job_detail_view_task,
    load_jobs_view_task,
    load_source_config_rows,
    load_storage_category_rows_task,
    preview_source_import_task,
    probe_watchlist_and_import_task,
    save_source_config_edit_task,
    validate_source_edit_values,
)
from .theme import APP_STYLE, PANE_SPACING, WINDOW_SIZE
from .utils import compact_text, stable_signature
from .workbench_ui import apply_tooltips, configure_table_views
from .workers import BackgroundTask, ScrapeWorker


DEFAULT_DB = paths.default_db_path()
DEFAULT_SOURCES = paths.default_sources_path()
DEFAULT_SOURCE_WATCHLIST = paths.default_source_watchlist_path()
MAIN_TAB_WORKBENCH = 0
MAIN_TAB_ANALYTICS = 1
MAIN_TAB_ROADMAP = 2
MAIN_TAB_SOURCES = 3


class MainWindow(QMainWindow):
    """Desktop workbench shell coordinating async pane refresh and details state."""

    def __init__(self, settings: Optional[Dict[str, Any]] = None) -> None:
        """Build the main workbench and defer heavy loading until first paint."""
        super().__init__()
        self.settings = dict(settings or load_settings())
        self.db_path = Path(str(self.settings.get("db_path") or DEFAULT_DB))
        self.sources_path = Path(str(self.settings.get("sources_path") or DEFAULT_SOURCES))
        self.source_watchlist_path = Path(str(self.settings.get("source_watchlist_path") or DEFAULT_SOURCE_WATCHLIST))

        self.logger = logging.getLogger(__name__)
        startup_started = perf_counter()
        self.setWindowTitle("JobScraper")
        self.setWindowIcon(QIcon(str(paths.app_icon_path())))
        self.resize(*WINDOW_SIZE)
        self.thread_pool = QThreadPool(self)
        self.thread_pool.setMaxThreadCount(max(4, QThreadPool.globalInstance().maxThreadCount()))

        self.data_epoch = 0
        self.request_tokens: Dict[str, int] = defaultdict(int)
        self.request_signatures: Dict[str, str] = {}
        self.active_tasks: Dict[str, Dict[str, Any]] = {}
        self.pane_tasks: Dict[str, Dict[str, Dict[str, Any]]] = defaultdict(dict)
        self.current_sources: List[Dict[str, Any]] = []
        self.current_jobs: List[Dict[str, Any]] = []
        self.current_selected_job_id: Optional[int] = None
        self.current_selected_job_preview: Optional[Dict[str, Any]] = None
        self.current_selected_job_detail: Optional[Dict[str, Any]] = None
        self.current_analytics_payload: Optional[Dict[str, Any]] = None
        self.current_roadmap_payload: Optional[Dict[str, Any]] = None
        self.current_ai_status: Optional[Dict[str, Any]] = None
        self.loaded_payload_signatures: Dict[str, str] = {}
        self.browser_html_signatures: Dict[str, str] = {}
        self.analytics_cache: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        self.db_ready = False
        self.dirty_tabs = {"description", "analytics", "roadmap"}
        self.reset_company_filter_on_counts = False
        self.scrape_worker: Optional[ScrapeWorker] = None
        self.activity_history: Deque[str] = deque(maxlen=2000)
        self.pending_activity_lines: List[str] = []
        self.activity_needs_full_sync = False
        self.activity_flush_timer = QTimer(self)
        self.activity_flush_timer.setSingleShot(True)
        self.activity_flush_timer.setInterval(140)
        self.activity_flush_timer.timeout.connect(self.flush_activity_log)
        self.scrape_progress_state: Dict[str, Any] = {}
        self.scrape_progress_timer = QTimer(self)
        self.scrape_progress_timer.setSingleShot(True)
        self.scrape_progress_timer.setInterval(180)
        self.scrape_progress_timer.timeout.connect(self.flush_scrape_progress)
        self.export_cancel_state: Optional[Dict[str, bool]] = None

        self.jobs_refresh_timer = QTimer(self)
        self.jobs_refresh_timer.setSingleShot(True)
        self.jobs_refresh_timer.setInterval(250)
        self.jobs_refresh_timer.timeout.connect(self._apply_jobs_refresh)

        self.setStyleSheet(APP_STYLE)
        build_started = perf_counter()
        self._build_ui()
        self._apply_tooltips()
        build_finished = perf_counter()
        self._configure_views()
        views_finished = perf_counter()
        self._connect_signals()
        signals_finished = perf_counter()
        self.logger.info("ui_startup build_ui_ms=%.1f configure_views_ms=%.1f connect_signals_ms=%.1f total_ms=%.1f", (build_finished-build_started)*1000.0, (views_finished-build_finished)*1000.0, (signals_finished-views_finished)*1000.0, (signals_finished-startup_started)*1000.0)
        QTimer.singleShot(0, self.startup_initialize)

    def _create_menu_action(self, menu: QMenu, text: str, slot: Callable[..., None]) -> QAction:
        """Create and wire one menu action."""
        action = QAction(text, self)
        action.triggered.connect(slot)
        menu.addAction(action)
        return action

    def _create_panel_dialog(self, *, title: str, panel: QWidget, width: int, height: int) -> QDialog:
        """Wrap one low-frequency pane in a reusable modeless dialog."""
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setModal(False)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(PANE_SPACING, PANE_SPACING, PANE_SPACING, PANE_SPACING)
        layout.setSpacing(PANE_SPACING)
        layout.addWidget(panel)
        dialog.resize(width, height)
        return dialog

    def _build_ui(self) -> None:
        """Construct the dense desktop-first shell and pane layout."""
        central = QWidget(self)
        root = QVBoxLayout(central)
        root.setContentsMargins(PANE_SPACING, PANE_SPACING, PANE_SPACING, PANE_SPACING)
        root.setSpacing(PANE_SPACING)

        self.command_bar = CommandBar(self)
        self.scrape_button = self.command_bar.scrape_button
        self.stop_button = self.command_bar.stop_button
        self.reload_button = self.command_bar.reload_button
        self.tools_button = self.command_bar.tools_button
        self.tools_menu = self.command_bar.tools_menu
        self.import_action = self._create_menu_action(self.tools_menu, "Import Sources...", self.import_sources)
        self.export_action = self._create_menu_action(self.tools_menu, "Export Filtered JSON...", self.export_filtered_json)
        self.cancel_export_action = self._create_menu_action(self.tools_menu, "Cancel Export", self.cancel_export)
        self.cancel_export_action.setEnabled(False)
        self.tools_menu.addSeparator()
        self.activity_action = self._create_menu_action(self.tools_menu, "Activity...", self.toggle_activity)
        self.storage_action = self._create_menu_action(self.tools_menu, "Storage...", self.show_storage_manager)
        self.tutorial_action = self._create_menu_action(self.tools_menu, "Getting Started...", self.show_startup_tutorial)
        self.open_logs_action = self._create_menu_action(self.tools_menu, "Open Logs Folder", self.open_logs_folder)
        self.open_backups_action = self._create_menu_action(self.tools_menu, "Open Backups Folder", self.open_backups_folder)
        self.open_reports_action = self._create_menu_action(self.tools_menu, "Open Reports Folder", self.open_reports_folder)
        self.open_watchlist_action = self._create_menu_action(self.tools_menu, "Open Source Watchlist", self.open_source_watchlist)
        self.copy_diagnostics_action = self._create_menu_action(self.tools_menu, "Copy Diagnostics Summary", self.copy_diagnostics_summary)
        self.settings_action = self._create_menu_action(self.tools_menu, "Settings...", self.toggle_settings)
        self.command_summary = self.command_bar.command_summary
        self.global_busy = self.command_bar.global_busy
        root.addWidget(self.command_bar)

        local_ai_config = ai_client.local_ai_config()
        self.settings_panel = SettingsPanel(
            db_path=str(self.db_path),
            sources_path=str(self.sources_path),
            watchlist_path=str(self.source_watchlist_path),
            log_path=str(paths.log_path()),
            reports_path=str(paths.reports_dir()),
            local_ai_config=local_ai_config,
            parent=self,
        )
        self.db_path_edit = self.settings_panel.db_path_edit
        self.sources_path_edit = self.settings_panel.sources_path_edit
        self.watchlist_path_edit = self.settings_panel.watchlist_path_edit
        self.remote_checkbox = self.settings_panel.remote_checkbox
        self.india_checkbox = self.settings_panel.india_checkbox
        self.interests_edit = self.settings_panel.interests_edit
        self.exclude_edit = self.settings_panel.exclude_edit
        self.concurrency_spin = self.settings_panel.concurrency_spin
        self.http_concurrency_spin = self.settings_panel.http_concurrency_spin
        self.hn_parser_combo = self.settings_panel.hn_parser_combo
        self.local_ai_url_edit = self.settings_panel.local_ai_url_edit
        self.local_ai_model_edit = self.settings_panel.local_ai_model_edit
        self.ai_status_label = self.settings_panel.ai_status_label
        self.ai_status_button = self.settings_panel.ai_status_button
        self.log_path_edit = self.settings_panel.log_path_edit
        self.reports_path_edit = self.settings_panel.reports_path_edit
        self.interests_edit.setText(", ".join(core.DEFAULT_INTEREST_TERMS))
        self.exclude_edit.setText(", ".join(core.DEFAULT_EXCLUDE_WORDS))
        self.http_concurrency_spin.setValue(int(self.settings.get("http_concurrency") or 32))
        self.local_ai_url_edit.setText(str(self.settings.get("local_ai_base_url") or local_ai_config.get("base_url") or ""))
        self.local_ai_model_edit.setText(str(self.settings.get("local_ai_model") or local_ai_config.get("model") or ""))
        self.settings_dialog = self._create_panel_dialog(
            title="Settings",
            panel=self.settings_panel,
            width=1320,
            height=240,
        )

        self.main_tabs = QTabWidget(self)
        self.main_tabs.setDocumentMode(True)
        root.addWidget(self.main_tabs, 1)

        self.workbench_tab = QWidget(self.main_tabs)
        self.main_tabs.addTab(self.workbench_tab, "Workbench")
        workbench_layout = QVBoxLayout(self.workbench_tab)
        workbench_layout.setContentsMargins(0, 0, 0, 0)
        workbench_layout.setSpacing(PANE_SPACING)

        body_splitter = QSplitter(Qt.Orientation.Horizontal, self.workbench_tab)
        body_splitter.setHandleWidth(2)
        body_splitter.setChildrenCollapsible(False)
        workbench_layout.addWidget(body_splitter, 1)

        self.company_panel = CompaniesPane(body_splitter)
        self.company_busy = self.company_panel.busy
        self.company_selection_button = self.company_panel.selection_button
        self.company_selection_menu = self.company_panel.selection_menu
        self.company_all_action = self._create_menu_action(self.company_selection_menu, "Select All", lambda checked=False: self.company_model.set_all(True))
        self.company_none_action = self._create_menu_action(self.company_selection_menu, "Clear All", lambda checked=False: self.company_model.set_all(False))
        self.company_selected_action = self._create_menu_action(self.company_selection_menu, "Use Selected Job Companies", self.select_companies_from_jobs)
        self.company_table = self.company_panel.table

        self.jobs_panel = JobsPane(body_splitter)
        self.jobs_busy = self.jobs_panel.busy
        self.matching_only_checkbox = self.jobs_panel.matching_only_checkbox
        self.open_only_checkbox = self.jobs_panel.open_only_checkbox
        self.group_by_company_checkbox = self.jobs_panel.group_by_company_checkbox
        self.founding_only_checkbox = self.jobs_panel.founding_only_checkbox
        self.portal_filter_combo = self.jobs_panel.portal_filter_combo
        self.source_filter_combo = self.jobs_panel.source_filter_combo
        self.source_tag_combo = self.jobs_panel.source_tag_combo
        self.stack_filter_combo = self.jobs_panel.stack_filter_combo
        self.hn_review_combo = self.jobs_panel.hn_review_combo
        self.search_edit = self.jobs_panel.search_edit
        self.filter_preset_combo = self.jobs_panel.filter_preset_combo
        self.save_filter_preset_button = self.jobs_panel.save_filter_preset_button
        self.delete_filter_preset_button = self.jobs_panel.delete_filter_preset_button
        self.jobs_summary_label = self.jobs_panel.summary_label
        self.jobs_table = self.jobs_panel.table

        self.description_panel = DescriptionPane(body_splitter)
        self.description_header = self.description_panel.header
        self.description_busy = self.description_panel.description_busy
        self.description_browser = self.description_panel.description_browser
        body_splitter.setSizes([280, 820, 500])

        self.analytics_tab = AnalyticsPane(parent=self.main_tabs)
        self.main_tabs.addTab(self.analytics_tab, "Analytics")
        self.analytics_busy = self.analytics_tab.analytics_busy
        self.analytics_browser = self.analytics_tab.analytics_browser

        self.roadmap_tab = RoadmapPane(parent=self.main_tabs)
        self.main_tabs.addTab(self.roadmap_tab, "Topic Roadmap")
        self.roadmap_busy = self.roadmap_tab.roadmap_busy
        self.roadmap_scope_combo = self.roadmap_tab.roadmap_scope_combo
        self.roadmap_refresh_button = self.roadmap_tab.roadmap_refresh_button
        self.roadmap_summary_label = self.roadmap_tab.roadmap_summary_label
        self.roadmap_browser = self.roadmap_tab.roadmap_browser

        self.activity_group = ActivityPane(self.workbench_tab)
        self.activity_log = self.activity_group.activity_log
        self.activity_dialog = self._create_panel_dialog(
            title="Activity",
            panel=self.activity_group,
            width=1180,
            height=360,
        )

        self.sources_tab = SourcesPane(parent=self.main_tabs)
        self.main_tabs.addTab(self.sources_tab, "Sources")
        self.sources_busy = self.sources_tab.busy
        self.source_health_filter_combo = self.sources_tab.source_health_filter_combo
        self.last_scrape_report_label = self.sources_tab.last_scrape_report_label
        self.source_probe_button = self.sources_tab.source_probe_button
        self.source_focus_button = self.sources_tab.source_focus_button
        self.source_edit_button = self.sources_tab.source_edit_button
        self.open_source_in_browser_action = self._create_menu_action(self.tools_menu, "Open Selected Source URL", self.open_selected_source_in_browser)
        self.sources_label = self.sources_tab.sources_label
        self.sources_table = self.sources_tab.table

        self.setCentralWidget(central)

    def _apply_tooltips(self) -> None:
        """Attach concise help text to interactive controls in the workbench."""
        apply_tooltips(self)

    def _configure_views(self) -> None:
        """Attach models and tune the table views for dense 1080p use."""
        configure_table_views(self)

    def _connect_signals(self) -> None:
        """Wire user actions to the request graph and worker entry points."""
        self.reload_button.clicked.connect(lambda: self.reload_all(force=True))
        self.scrape_button.clicked.connect(self.run_scrape)
        self.stop_button.clicked.connect(self.stop_scrape)
        self.main_tabs.currentChanged.connect(self.on_main_tab_changed)
        self.company_model.checksChanged.connect(self.on_company_filter_changed)
        self.company_table.clicked.connect(self.on_company_table_clicked)

        for widget in [
            self.matching_only_checkbox,
            self.open_only_checkbox,
            self.group_by_company_checkbox,
            self.founding_only_checkbox,
        ]:
            widget.stateChanged.connect(self.schedule_jobs_refresh)
        self.portal_filter_combo.currentIndexChanged.connect(self.on_primary_filter_changed)
        self.source_filter_combo.currentIndexChanged.connect(self.on_source_filter_changed)
        self.source_tag_combo.currentIndexChanged.connect(self.on_primary_filter_changed)
        self.stack_filter_combo.currentIndexChanged.connect(self.schedule_jobs_refresh)
        self.hn_review_combo.currentIndexChanged.connect(self.schedule_jobs_refresh)
        self.search_edit.textChanged.connect(self.schedule_jobs_refresh)
        self.filter_preset_combo.currentIndexChanged.connect(self.load_selected_filter_preset)
        self.save_filter_preset_button.clicked.connect(self.save_current_filter_preset)
        self.delete_filter_preset_button.clicked.connect(self.delete_current_filter_preset)
        self.jobs_table.selectionModel().selectionChanged.connect(self.on_jobs_selection_changed)

        self.roadmap_scope_combo.currentIndexChanged.connect(self.on_roadmap_scope_changed)
        self.roadmap_refresh_button.clicked.connect(lambda: self.refresh_topic_roadmap(force=True))
        self.sources_table.selectionModel().selectionChanged.connect(self.on_source_selection_changed)
        self.sources_table.doubleClicked.connect(lambda _index: self.focus_selected_source_in_workbench())
        self.source_health_filter_combo.currentIndexChanged.connect(lambda _index: self.apply_source_table_filter())
        self.source_probe_button.clicked.connect(self.probe_watchlist_and_import)
        self.source_focus_button.clicked.connect(self.focus_selected_source_in_workbench)
        self.source_edit_button.clicked.connect(self.edit_selected_source)
        self.refresh_filter_preset_options()

        for widget in [self.db_path_edit, self.sources_path_edit, self.watchlist_path_edit, self.interests_edit, self.exclude_edit, self.local_ai_url_edit, self.local_ai_model_edit]:
            widget.textChanged.connect(self.update_command_summary)
        self.db_path_edit.editingFinished.connect(self.on_path_settings_changed)
        self.sources_path_edit.editingFinished.connect(self.on_path_settings_changed)
        self.watchlist_path_edit.editingFinished.connect(self.on_path_settings_changed)
        self.local_ai_url_edit.editingFinished.connect(self.on_local_ai_settings_changed)
        self.local_ai_model_edit.editingFinished.connect(self.on_local_ai_settings_changed)
        self.ai_status_button.clicked.connect(lambda: self.refresh_ai_status(force=True))
        self.remote_checkbox.stateChanged.connect(self.update_command_summary)
        self.india_checkbox.stateChanged.connect(self.update_command_summary)
        self.concurrency_spin.valueChanged.connect(self.update_command_summary)
        self.http_concurrency_spin.valueChanged.connect(self.update_command_summary)
        self.hn_parser_combo.currentIndexChanged.connect(self.update_command_summary)

    def set_workspace_ready(self, ready: bool) -> None:
        """Enable or disable interactive data actions while startup work is incomplete."""
        self.db_ready = ready
        self.main_tabs.setEnabled(ready)
        self.scrape_button.setEnabled(ready and self.scrape_worker is None)
        self.reload_button.setEnabled(ready)
        self.tools_button.setEnabled(ready)

    def startup_initialize(self) -> None:
        """Open and migrate SQLite off the GUI thread before the first data refresh."""
        self.update_command_summary()
        self.refresh_ai_status(force=False)
        self.set_workspace_ready(False)
        self.queue_request(
            key="db_init",
            pane="sources",
            label="Opening database",
            signature={"db_path": str(self.db_path)},
            fn=lambda: initialize_database_task(self.db_path),
            on_success=self.on_database_ready,
            force=True,
        )

    def on_database_ready(self, _payload: Dict[str, Any]) -> None:
        """Enable the workspace and kick off the initial pane refresh once SQLite is ready."""
        self.set_workspace_ready(True)
        self.reload_active_surface(force=True)
        QTimer.singleShot(0, self.maybe_show_startup_tutorial)

    def toggle_settings(self) -> None:
        """Open the settings dialog and bring it to the front."""
        self.settings_dialog.show()
        self.settings_dialog.raise_()
        self.settings_dialog.activateWindow()

    def open_logs_folder(self) -> None:
        """Open the workspace logs directory in the system file browser."""
        fs.ensure_dir(paths.logs_dir())
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.logs_dir())))

    def open_backups_folder(self) -> None:
        """Open the workspace SQLite backups directory in the system file browser."""
        fs.ensure_dir(paths.backups_dir())
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.backups_dir())))

    def open_reports_folder(self) -> None:
        """Open the workspace reports directory in the system file browser."""
        fs.ensure_dir(paths.reports_dir())
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.reports_dir())))

    def open_source_watchlist(self) -> None:
        """Open the candidate source watchlist file in the system handler."""
        fs.ensure_dir(self.source_watchlist_path.parent)
        if not self.source_watchlist_path.exists() and paths.bundled_source_watchlist_path().exists():
            fs.copy_file(paths.bundled_source_watchlist_path(), self.source_watchlist_path)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.source_watchlist_path)))

    def copy_diagnostics_summary(self) -> None:
        """Copy a compact local diagnostics summary for support/debugging."""
        summary = "\n".join(
            [
                "JobScraper diagnostics",
                f"DB: {self.db_path}",
                f"Sources: {self.sources_path}",
                f"Source watchlist: {self.source_watchlist_path}",
                f"Logs: {paths.log_path()}",
                f"Backups: {paths.backups_dir()}",
                f"Reports: {paths.reports_dir()}",
                f"Workspace: {paths.default_workspace_root()}",
                f"Main tab: {self.main_tabs.tabText(self.main_tabs.currentIndex())}",
                f"Loaded sources: {len(self.current_sources)}",
                f"Loaded jobs: {len(self.current_jobs)}",
            ]
        )
        QApplication.clipboard().setText(summary)
        self.append_activity("Diagnostics summary copied to clipboard.")

    @staticmethod
    def _path_size(path: Path) -> int:
        """Return a best-effort byte size for one file or directory tree."""
        if not path.exists():
            return 0
        if path.is_file():
            try:
                return path.stat().st_size
            except OSError:
                return 0
        total = 0
        try:
            children = list(path.rglob("*"))
        except OSError:
            return 0
        for child in children:
            if not child.is_file():
                continue
            try:
                total += child.stat().st_size
            except OSError:
                continue
        return total

    @staticmethod
    def _format_bytes(size: int) -> str:
        value = float(max(0, int(size)))
        for suffix in ("B", "KB", "MB", "GB"):
            if value < 1024.0 or suffix == "GB":
                if suffix == "B":
                    return f"{int(value)} {suffix}"
                return f"{value:.1f} {suffix}"
            value /= 1024.0
        return f"{value:.1f} GB"

    def storage_categories(self) -> List[Dict[str, Any]]:
        """Return visible storage categories with deletion policy."""
        root = paths.default_workspace_root()
        return [
            {"key": "db", "label": "DB", "path": self.db_path, "deletable": False},
            {"key": "sources", "label": "Active sources", "path": self.sources_path, "deletable": False},
            {"key": "watchlist", "label": "Active watchlist", "path": self.source_watchlist_path, "deletable": False},
            {"key": "settings", "label": "Settings", "path": paths.settings_path(), "deletable": False},
            {"key": "logs", "label": "Logs", "path": paths.logs_dir(), "deletable": True},
            {"key": "backups", "label": "Backups", "path": paths.backups_dir(), "deletable": True},
            {"key": "reports", "label": "Reports", "path": paths.reports_dir(), "deletable": True},
            {"key": "exports", "label": "Exports", "path": paths.exports_dir(), "deletable": True},
            {"key": "caches", "label": "Caches", "path": root / "cache", "deletable": True},
        ]

    def storage_category_rows(self) -> List[Dict[str, Any]]:
        """Return storage categories with current byte sizes."""
        return load_storage_category_rows_task(self.storage_categories())

    def delete_storage_category(self, key: str) -> bool:
        """Delete files for one generated storage category."""
        categories = {str(item["key"]): item for item in self.storage_categories()}
        category = categories.get(str(key))
        if not category or not bool(category.get("deletable")):
            return False
        try:
            result = delete_storage_category_task(category)
        except RuntimeError as exc:
            QMessageBox.warning(self, "Storage", str(exc))
            return False
        if bool(result.get("deleted")):
            self.append_activity(f"Deleted generated storage category: {category['label']} ({result.get('path')})")
            return True
        return False

    def _build_storage_manager_dialog(self) -> QDialog:
        """Build the storage visibility dialog without scanning sizes on the GUI thread."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Storage")
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(PANE_SPACING, PANE_SPACING, PANE_SPACING, PANE_SPACING)
        layout.setSpacing(PANE_SPACING)
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        layout.addLayout(grid)
        grid.addWidget(QLabel("Category", dialog), 0, 0)
        grid.addWidget(QLabel("Size", dialog), 0, 1)
        grid.addWidget(QLabel("Path", dialog), 0, 2)
        size_labels: Dict[str, QLabel] = {}
        path_labels: Dict[str, QLabel] = {}
        buttons: Dict[str, QPushButton] = {}
        categories = self.storage_categories()
        for row_index, category in enumerate(categories, start=1):
            category_key = str(category["key"])
            grid.addWidget(QLabel(str(category["label"]), dialog), row_index, 0)
            size_label = QLabel("Scanning...", dialog)
            path_label = QLabel(compact_text(str(category["path"]), 110), dialog)
            button = QPushButton("Delete", dialog)
            button.setEnabled(bool(category.get("deletable")) and Path(category["path"]).exists())
            button.clicked.connect(lambda _checked=False, key=category_key, dlg=dialog: self.queue_storage_category_delete(dlg, key))
            size_labels[category_key] = size_label
            path_labels[category_key] = path_label
            buttons[category_key] = button
            grid.addWidget(size_label, row_index, 1)
            grid.addWidget(path_label, row_index, 2)
            grid.addWidget(button, row_index, 3)
        buttons_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, dialog)
        buttons_box.rejected.connect(dialog.reject)
        layout.addWidget(buttons_box)
        dialog.resize(980, 360)
        setattr(dialog, "_storage_closed", False)
        setattr(dialog, "_storage_size_labels", size_labels)
        setattr(dialog, "_storage_path_labels", path_labels)
        setattr(dialog, "_storage_buttons", buttons)
        dialog.finished.connect(lambda _code, dlg=dialog: setattr(dlg, "_storage_closed", True))
        return dialog

    def refresh_storage_manager(self, dialog: QDialog, *, force: bool = False) -> None:
        """Refresh storage sizes in the background for one open storage dialog."""
        if getattr(dialog, "_storage_closed", False):
            return
        size_labels = getattr(dialog, "_storage_size_labels", {})
        for label in size_labels.values():
            label.setText("Scanning...")
        categories = self.storage_categories()
        self.queue_request(
            key="storage_scan",
            pane="global",
            label="Scanning storage",
            signature=[(str(category["key"]), str(category["path"])) for category in categories],
            fn=lambda categories=categories: load_storage_category_rows_task(categories),
            on_success=lambda rows, dlg=dialog: self.on_storage_rows_loaded(dlg, rows),
            force=force,
        )

    def on_storage_rows_loaded(self, dialog: QDialog, rows: Sequence[Dict[str, Any]]) -> None:
        """Update one storage dialog with the latest background size scan."""
        if getattr(dialog, "_storage_closed", False):
            return
        size_labels = getattr(dialog, "_storage_size_labels", {})
        path_labels = getattr(dialog, "_storage_path_labels", {})
        buttons = getattr(dialog, "_storage_buttons", {})
        by_key = {str(row["key"]): row for row in rows}
        for category_key, row in by_key.items():
            if category_key in size_labels:
                size_labels[category_key].setText(str(row.get("size_label") or "0 B"))
            if category_key in path_labels:
                path_labels[category_key].setText(compact_text(str(row.get("path") or ""), 110))
            if category_key in buttons:
                buttons[category_key].setEnabled(bool(row.get("deletable")) and Path(row["path"]).exists())

    def queue_storage_category_delete(self, dialog: QDialog, category_key: str) -> None:
        """Delete one generated storage category off the GUI thread."""
        categories = {str(item["key"]): item for item in self.storage_categories()}
        category = categories.get(str(category_key))
        if not category:
            return
        if QMessageBox.question(self, "Storage", f"Delete generated files in {category['label']}?\n\n{category['path']}") != QMessageBox.StandardButton.Yes:
            return
        size_labels = getattr(dialog, "_storage_size_labels", {})
        buttons = getattr(dialog, "_storage_buttons", {})
        if category_key in size_labels:
            size_labels[category_key].setText("Deleting...")
        self.queue_request(
            key=f"storage_delete:{category_key}",
            pane="global",
            label=f"Deleting {category['label']}",
            signature={"key": category_key, "path": str(category["path"]), "exists": Path(category["path"]).exists()},
            fn=lambda category=dict(category): delete_storage_category_task(category),
            on_success=lambda result, dlg=dialog: self.on_storage_category_deleted(dlg, result),
            force=True,
            control=buttons.get(category_key),
        )

    def on_storage_category_deleted(self, dialog: QDialog, result: Dict[str, Any]) -> None:
        """Record one completed storage deletion and refresh visible size data."""
        if bool(result.get("deleted")):
            self.append_activity(
                f"Deleted generated storage category: {result.get('label') or result.get('key')} ({result.get('path')})"
            )
        self.refresh_storage_manager(dialog, force=True)

    def show_storage_manager(self) -> None:
        """Open generated-file storage visibility and cleanup dialog."""
        dialog = self._build_storage_manager_dialog()
        self.refresh_storage_manager(dialog, force=True)
        dialog.exec()

    def mark_startup_tutorial_dismissed(self) -> None:
        """Persist that the first-run tutorial has been seen."""
        self.settings["first_run_tutorial_dismissed"] = True
        save_settings(self.settings)

    def maybe_show_startup_tutorial(self) -> None:
        """Show the startup guide once for a new workspace."""
        if bool(self.settings.get("first_run_tutorial_dismissed")):
            return
        self.show_startup_tutorial(mark_dismissed=True)

    def show_startup_tutorial(self, _checked: bool = False, *, mark_dismissed: bool = False) -> None:
        """Open a short non-modal getting-started guide."""
        existing = getattr(self, "tutorial_dialog", None)
        if existing is not None and existing.isVisible():
            existing.raise_()
            existing.activateWindow()
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Getting Started")
        dialog.setModal(False)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(PANE_SPACING, PANE_SPACING, PANE_SPACING, PANE_SPACING)
        title = QLabel("JobScraper workspace flow", dialog)
        title.setObjectName("PanelHeader")
        layout.addWidget(title)
        guide = QPlainTextEdit(dialog)
        guide.setReadOnly(True)
        guide.setPlainText(
            "\n".join(
                [
                    "1. Import or edit sources from Tools and the Sources tab.",
                    "2. Run Scrape to refresh enabled public sources.",
                    "3. Filter jobs by company, source family, source row, tag, stack, and search.",
                    "4. Select a job row to inspect normalized detail and links.",
                    "5. Export the current filtered jobs view from Tools.",
                    "6. Use Sources health groups to find blocked, failing, new, and healthy rows.",
                    "7. Open Storage from Tools to review and delete generated logs, reports, backups, exports, and caches.",
                ]
            )
        )
        layout.addWidget(guide, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, dialog)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if mark_dismissed:
            dialog.finished.connect(lambda _code: self.mark_startup_tutorial_dismissed())
        self.tutorial_dialog = dialog
        dialog.resize(760, 380)
        dialog.show()

    def persist_settings(self) -> None:
        """Persist the current runtime path and Local AI settings."""
        self.settings["db_path"] = str(self.db_path)
        self.settings["sources_path"] = str(self.sources_path)
        self.settings["source_watchlist_path"] = str(self.source_watchlist_path)
        self.settings["http_concurrency"] = int(self.http_concurrency_spin.value())
        self.settings["local_ai_base_url"] = self.local_ai_url_edit.text().strip()
        self.settings["local_ai_model"] = self.local_ai_model_edit.text().strip()
        save_settings(self.settings)

    @staticmethod
    def validate_runtime_paths(db_path: Path, sources_path: Path, watchlist_path: Path) -> List[str]:
        """Return validation errors for editable runtime file paths."""
        errors: List[str] = []
        for label, path in (("DB", db_path), ("Sources", sources_path), ("Watchlist", watchlist_path)):
            if not str(path).strip():
                errors.append(f"{label} path is empty.")
                continue
            if path.exists() and path.is_dir():
                errors.append(f"{label} path points to a directory: {path}")
            parent = path.parent
            if parent.exists() and not parent.is_dir():
                errors.append(f"{label} parent is not a directory: {parent}")
        for label, path in (("Sources", sources_path), ("Watchlist", watchlist_path)):
            if path.suffix.lower() != ".json":
                errors.append(f"{label} path must be a .json file: {path}")
        if db_path.suffix.lower() and db_path.suffix.lower() not in {".sqlite", ".sqlite3", ".db"}:
            errors.append(f"DB path must be a SQLite file: {db_path}")
        return errors

    def on_path_settings_changed(self) -> None:
        """Persist DB and source path edits from the Settings dialog."""
        new_db_path = Path(self.db_path_edit.text().strip() or str(self.db_path))
        new_sources_path = Path(self.sources_path_edit.text().strip() or str(self.sources_path))
        new_watchlist_path = Path(self.watchlist_path_edit.text().strip() or str(self.source_watchlist_path))
        errors = self.validate_runtime_paths(new_db_path, new_sources_path, new_watchlist_path)
        if errors:
            self.db_path_edit.setText(str(self.db_path))
            self.sources_path_edit.setText(str(self.sources_path))
            self.watchlist_path_edit.setText(str(self.source_watchlist_path))
            QMessageBox.warning(self, "Settings", "\n".join(errors))
            self.update_command_summary()
            return
        if new_db_path == self.db_path and new_sources_path == self.sources_path and new_watchlist_path == self.source_watchlist_path:
            self.update_command_summary()
            return
        if self.scrape_worker is not None or self.active_tasks:
            self.db_path_edit.setText(str(self.db_path))
            self.sources_path_edit.setText(str(self.sources_path))
            self.watchlist_path_edit.setText(str(self.source_watchlist_path))
            QMessageBox.warning(self, "Settings", "Wait for active background work to finish before changing DB or source paths.")
            return
        self.db_path = new_db_path
        self.sources_path = new_sources_path
        self.source_watchlist_path = new_watchlist_path
        self.persist_settings()
        self.data_epoch += 1
        self.loaded_payload_signatures.clear()
        self.analytics_cache.clear()
        self.current_sources = []
        self.current_jobs = []
        self.current_selected_job_id = None
        self.current_selected_job_preview = None
        self.current_selected_job_detail = None
        self.append_activity(f"Workspace paths updated: db={self.db_path} sources={self.sources_path} watchlist={self.source_watchlist_path}")
        self.update_command_summary()
        self.startup_initialize()

    def apply_local_ai_settings(self) -> None:
        """Push Local AI settings from the UI into the current process environment."""
        base_url = self.local_ai_url_edit.text().strip()
        model = self.local_ai_model_edit.text().strip()
        if base_url:
            os.environ["LOCAL_AI_BASE_URL"] = base_url
        else:
            os.environ.pop("LOCAL_AI_BASE_URL", None)
        if model:
            os.environ["LOCAL_AI_MODEL"] = model
        else:
            os.environ.pop("LOCAL_AI_MODEL", None)
        self.persist_settings()

    def ai_status_signature(self) -> Dict[str, Any]:
        """Return the inputs that affect the visible AI availability summary."""
        return {
            "openai_enabled": bool(os.getenv("OPENAI_API_KEY", "").strip()),
            "openai_base_url": os.getenv("OPENAI_BASE_URL", "").strip(),
            "openai_model": os.getenv("OPENAI_MODEL", "").strip(),
            "local_ai_base_url": self.local_ai_url_edit.text().strip(),
            "local_ai_model": self.local_ai_model_edit.text().strip(),
            "local_ai_provider": os.getenv("LOCAL_AI_PROVIDER", "").strip(),
        }

    def refresh_ai_status(self, *, force: bool = False) -> None:
        """Check OpenAI and Local AI availability without blocking the UI."""
        signature = self.ai_status_signature()
        if (
            not force
            and self.current_ai_status is not None
            and self.request_signatures.get("ai_status") == stable_signature(signature)
        ):
            self.render_ai_status(self.current_ai_status, log_activity=False)
            return
        self.ai_status_label.setText("Checking AI availability...")
        self.queue_request(
            key="ai_status",
            pane="global",
            label="Checking AI availability",
            signature=signature,
            fn=load_ai_status_task,
            on_success=self.on_ai_status_loaded,
            force=force,
            control=self.ai_status_button,
        )

    def render_ai_status(self, payload: Dict[str, Any], *, log_activity: bool) -> None:
        """Apply one AI availability snapshot to the settings UI."""
        summary = str(payload.get("summary") or "AI availability unavailable")
        detail = str(payload.get("detail") or "").strip()
        self.ai_status_label.setText(summary)
        self.ai_status_label.setToolTip(detail or summary)
        if log_activity:
            self.append_activity(f"AI availability: {payload.get('activity') or summary}")

    def on_ai_status_loaded(self, payload: Dict[str, Any]) -> None:
        """Render one completed AI availability snapshot into the settings panel."""
        self.current_ai_status = dict(payload)
        self.render_ai_status(self.current_ai_status, log_activity=True)

    def on_local_ai_settings_changed(self) -> None:
        """Apply Local AI settings used by optional parser flows."""
        self.apply_local_ai_settings()
        self.update_command_summary()
        self.refresh_ai_status(force=False)

    def workbench_visible(self) -> bool:
        """Return whether the main workbench tab is currently active."""
        return self.main_tabs.currentIndex() == MAIN_TAB_WORKBENCH

    def analysis_visible(self) -> bool:
        """Return whether one of the derived analysis tabs is currently active."""
        return self.main_tabs.currentIndex() in {MAIN_TAB_ANALYTICS, MAIN_TAB_ROADMAP}

    def sources_visible(self) -> bool:
        """Return whether the Sources admin tab is currently active."""
        return self.main_tabs.currentIndex() == MAIN_TAB_SOURCES

    def pane_is_visible(self, pane: str) -> bool:
        """Return whether the named pane is currently visible to the operator."""
        if pane == "sources":
            return self.sources_visible()
        if pane in {"company", "jobs"}:
            return self.workbench_visible()
        if pane == "description":
            return self.workbench_visible()
        if pane == "analytics":
            return self.main_tabs.currentIndex() == MAIN_TAB_ANALYTICS
        if pane == "roadmap":
            return self.main_tabs.currentIndex() == MAIN_TAB_ROADMAP
        return True

    def _browser_scroll_is_near_bottom(self) -> bool:
        """Return whether the activity log is already close enough to auto-scroll."""
        scrollbar = self.activity_log.verticalScrollBar()
        return scrollbar.value() >= max(0, scrollbar.maximum() - max(32, scrollbar.pageStep() // 3))

    def sync_activity_dialog(self) -> None:
        """Project the buffered activity history into the visible dialog on demand."""
        if self.activity_needs_full_sync or self.pending_activity_lines:
            self.activity_log.setPlainText("\n".join(self.activity_history))
            self.activity_log.verticalScrollBar().setValue(self.activity_log.verticalScrollBar().maximum())
            self.pending_activity_lines.clear()
            self.activity_needs_full_sync = False
            return
        self.flush_activity_log()

    def toggle_activity(self) -> None:
        """Open the activity dialog and bring it to the front."""
        self.sync_activity_dialog()
        self.activity_dialog.show()
        self.activity_dialog.raise_()
        self.activity_dialog.activateWindow()

    def append_activity(self, line: str) -> None:
        """Append one line to the bounded runtime log view."""
        text = str(line or "").rstrip()
        if not text:
            return
        self.activity_history.append(text)
        if self.activity_dialog.isVisible():
            self.pending_activity_lines.append(text)
            if not self.activity_flush_timer.isActive():
                self.activity_flush_timer.start()
        else:
            self.activity_needs_full_sync = True

    def flush_activity_log(self) -> None:
        """Flush buffered runtime lines into the visible activity widget."""
        if not self.activity_dialog.isVisible() or not self.pending_activity_lines:
            return
        near_bottom = self._browser_scroll_is_near_bottom()
        lines = self.pending_activity_lines[:]
        self.pending_activity_lines.clear()
        self.activity_log.appendPlainText("\n".join(lines))
        if near_bottom:
            self.activity_log.verticalScrollBar().setValue(self.activity_log.verticalScrollBar().maximum())

    def update_command_summary(self) -> None:
        """Refresh the compact state strip above the workbench."""
        location_bits = []
        if self.remote_checkbox.isChecked():
            location_bits.append("remote")
        if self.india_checkbox.isChecked():
            location_bits.append("india hybrid/office")
        local_ai_model = self.local_ai_model_edit.text().strip()
        summary = (
            f"{Path(self.db_path_edit.text() or str(self.db_path)).name} | "
            f"{Path(self.sources_path_edit.text() or str(self.sources_path)).name} | "
            f"location: {', '.join(location_bits) or 'none'} | "
            f"interests: {len([x for x in self.parse_csv(self.interests_edit.text()) if x])} | "
            f"exclude: {len([x for x in self.parse_csv(self.exclude_edit.text()) if x])} | "
            f"concurrency: {self.concurrency_spin.value()} | "
            f"http: {self.http_concurrency_spin.value()} | "
            f"hn-parse: {str(self.hn_parser_combo.currentText() or 'Auto').strip()}"
            + (f" | local-ai: {compact_text(local_ai_model, 24)}" if local_ai_model else "")
        )
        self.command_summary.setText(summary)

    def parse_csv(self, value: str) -> List[str]:
        """Split a comma-separated text field into trimmed non-empty values."""
        return [item.strip() for item in str(value or "").split(",") if item.strip()]

    def current_filters(self) -> Dict[str, Any]:
        """Return the current jobs-view filter state used by list and summary queries."""
        current_source_id = int(self.source_filter_combo.currentData() or 0)
        hn_mode = str(self.hn_review_combo.currentData() or "") if self.hn_review_combo.isEnabled() else ""
        companies = [] if self.reset_company_filter_on_counts else self.company_model.checked_companies()
        return {
            "matching_only": self.matching_only_checkbox.isChecked(),
            "open_only": self.open_only_checkbox.isChecked(),
            "search": self.search_edit.text().strip(),
            "stack": str(self.stack_filter_combo.currentData() or ""),
            "source_tag": str(self.source_tag_combo.currentData() or ""),
            "companies": companies,
            "portal": str(self.portal_filter_combo.currentData() or ""),
            "source_id": current_source_id,
            "hn_mode": hn_mode,
            "founding_only": self.founding_only_checkbox.isChecked(),
        }

    def current_filter_preset(self) -> Dict[str, Any]:
        """Return the persisted filter preset shape."""
        filters = self.current_filters()
        filters["group_by_company"] = self.group_by_company_checkbox.isChecked()
        return filters

    def filter_presets(self) -> Dict[str, Dict[str, Any]]:
        """Return saved filter presets from workspace settings."""
        raw = self.settings.get("filter_presets")
        if not isinstance(raw, dict):
            return {}
        return {str(name): dict(value) for name, value in raw.items() if isinstance(value, dict)}

    def refresh_filter_preset_options(self, selected: str = "") -> None:
        """Reload the filter preset selector from persisted settings."""
        presets = self.filter_presets()
        self.filter_preset_combo.blockSignals(True)
        self.filter_preset_combo.clear()
        self.filter_preset_combo.addItem("Filter presets", "")
        selected_index = 0
        for index, name in enumerate(sorted(presets), start=1):
            self.filter_preset_combo.addItem(name, name)
            if name == selected:
                selected_index = index
        self.filter_preset_combo.setCurrentIndex(selected_index)
        self.filter_preset_combo.blockSignals(False)

    def save_current_filter_preset(self) -> None:
        """Prompt for a preset name and persist the current filters."""
        name, ok = QInputDialog.getText(self, "Save Filter Preset", "Name")
        name = str(name or "").strip()
        if not ok or not name:
            return
        presets = self.filter_presets()
        presets[name] = self.current_filter_preset()
        self.settings["filter_presets"] = presets
        save_settings(self.settings)
        self.refresh_filter_preset_options(selected=name)

    def delete_current_filter_preset(self) -> None:
        """Delete the selected saved filter preset."""
        name = str(self.filter_preset_combo.currentData() or "")
        if not name:
            return
        presets = self.filter_presets()
        if name in presets:
            presets.pop(name, None)
            self.settings["filter_presets"] = presets
            save_settings(self.settings)
        self.refresh_filter_preset_options()

    @staticmethod
    def _set_combo_data(combo: Any, value: Any) -> None:
        for index in range(combo.count()):
            if combo.itemData(index) == value or str(combo.itemData(index) or "") == str(value or ""):
                combo.setCurrentIndex(index)
                return
        combo.setCurrentIndex(0)

    def load_selected_filter_preset(self) -> None:
        """Apply the selected saved filter preset."""
        name = str(self.filter_preset_combo.currentData() or "")
        if not name:
            return
        preset = self.filter_presets().get(name)
        if not preset:
            self.refresh_filter_preset_options()
            return
        widgets = [
            self.matching_only_checkbox,
            self.open_only_checkbox,
            self.group_by_company_checkbox,
            self.founding_only_checkbox,
            self.portal_filter_combo,
            self.source_filter_combo,
            self.source_tag_combo,
            self.stack_filter_combo,
            self.hn_review_combo,
            self.search_edit,
        ]
        for widget in widgets:
            widget.blockSignals(True)
        try:
            self.matching_only_checkbox.setChecked(bool(preset.get("matching_only", True)))
            self.open_only_checkbox.setChecked(bool(preset.get("open_only", True)))
            self.group_by_company_checkbox.setChecked(bool(preset.get("group_by_company", False)))
            self.founding_only_checkbox.setChecked(bool(preset.get("founding_only", False)))
            self._set_combo_data(self.portal_filter_combo, str(preset.get("portal") or ""))
            self.refresh_source_filter_options()
            self.source_filter_combo.blockSignals(True)
            self._set_combo_data(self.source_filter_combo, int(preset.get("source_id") or 0))
            self.refresh_hn_review_state()
            self._set_combo_data(self.source_tag_combo, str(preset.get("source_tag") or ""))
            self._set_combo_data(self.stack_filter_combo, str(preset.get("stack") or ""))
            if self.hn_review_combo.isEnabled():
                self._set_combo_data(self.hn_review_combo, str(preset.get("hn_mode") or ""))
            self.search_edit.setText(str(preset.get("search") or ""))
            self.company_model.set_checked_companies(list(preset.get("companies") or []))
        finally:
            for widget in widgets:
                widget.blockSignals(False)
        self.reload_stacks(force=True)
        self.reload_company_counts(force=True)
        self.schedule_jobs_refresh()

    def current_non_company_filters(self) -> Dict[str, Any]:
        """Return the active filters without company narrowing for sidebar counts."""
        filters = self.current_filters()
        filters["companies"] = []
        return filters

    def current_source_row_filter(self) -> Optional[Dict[str, Any]]:
        """Return the currently selected source-row filter or None for the global view."""
        source_id = int(self.source_filter_combo.currentData() or 0)
        if source_id <= 0:
            return None
        for row in self.current_sources:
            if int(row.get("id") or 0) == source_id:
                return dict(row)
        return None

    def source_scope_is_hackernews(self) -> bool:
        """Return whether the current jobs scope is narrowed to Hacker News rows."""
        source_row = self.current_source_row_filter()
        if source_row:
            return str(source_row.get("portal") or "").strip().lower() == "hackernews"
        return str(self.portal_filter_combo.currentData() or "").strip().lower() == "hackernews"

    def selected_job_ids(self) -> List[int]:
        """Return deduplicated selected real-job IDs from the jobs table."""
        ids: List[int] = []
        selection = self.jobs_table.selectionModel()
        if selection is None:
            return ids
        for index in selection.selectedRows():
            job_id = self.jobs_model.job_id_at(index.row())
            if job_id:
                ids.append(job_id)
        seen = set()
        ordered: List[int] = []
        for job_id in ids:
            if job_id not in seen:
                seen.add(job_id)
                ordered.append(job_id)
        return ordered

    def set_browser_html(self, key: str, browser: Any, html: str) -> None:
        """Avoid redundant QTextBrowser updates when the rendered HTML is unchanged."""
        signature = stable_signature([key, html])
        if self.browser_html_signatures.get(key) == signature:
            return
        self.browser_html_signatures[key] = signature
        browser.setHtml(html)

    def reload_workbench_surface(self, *, force: bool = False) -> None:
        """Refresh only the visible workbench data surfaces."""
        self.reload_stacks(force=force)
        self.reload_company_counts(force=force)
        self.reload_jobs(force=force)
        self.reload_selected_job_detail(force=force)

    def reload_analysis_surface(self, *, force: bool = False) -> None:
        """Refresh only the visible analysis surfaces."""
        self.refresh_visible_analysis_tab(force=force)

    def reload_sources_surface(self, *, force: bool = False) -> None:
        """Refresh only the Sources-tab state needed for the active admin view."""
        self.reload_sources(force=force)

    def reload_active_surface(self, *, force: bool = False) -> None:
        """Refresh only the currently visible top-level tab."""
        if self.sources_visible():
            self.reload_sources_surface(force=force)
            return
        if self.analysis_visible():
            self.reload_analysis_surface(force=force)
            return
        self.reload_workbench_surface(force=force)

    def initial_load(self) -> None:
        """Backward-compatible alias for the startup initialization flow."""
        self.startup_initialize()

    def reload_all(self, *, force: bool = False) -> None:
        """Refresh every top-level pane after a data-epoch or source-state change."""
        self.mark_analysis_dirty("description")
        self.mark_analysis_dirty("analytics")
        self.mark_analysis_dirty("roadmap")
        self.reload_active_surface(force=force)

    def schedule_jobs_refresh(self) -> None:
        """Debounce jobs-pane refreshes after noisy filter changes."""
        self.jobs_refresh_timer.start()

    def _apply_jobs_refresh(self) -> None:
        """Apply the debounced jobs refresh and invalidate dependent detail/analysis panes."""
        self.reload_company_counts(force=True)
        self.reload_jobs(force=True)
        self.mark_analysis_dirty("description")
        self.mark_analysis_dirty("analytics")
        self.mark_analysis_dirty("roadmap")
        self.refresh_visible_analysis_tab(force=False)

    def on_primary_filter_changed(self) -> None:
        """Refresh dependent filters when a primary jobs filter changes."""
        self.refresh_source_filter_options()
        self.refresh_hn_review_state()
        self.reload_stacks(force=True)
        self.schedule_jobs_refresh()

    def on_source_filter_changed(self) -> None:
        """Refresh dependent filters when the source-row scope changes."""
        self.refresh_hn_review_state()
        self.reload_stacks(force=True)
        self.schedule_jobs_refresh()

    def on_company_filter_changed(self) -> None:
        """React to company sidebar changes without rebuilding unrelated panes."""
        self.reload_jobs(force=True)
        self.mark_analysis_dirty("description")
        self.mark_analysis_dirty("analytics")
        self.mark_analysis_dirty("roadmap")
        self.refresh_visible_analysis_tab(force=False)

    def on_company_table_clicked(self, index: Any) -> None:
        """Treat company-row clicks as the primary company filter interaction.

        Clicking the checkbox column keeps multi-select behavior. Clicking the
        company name or count columns narrows the jobs view to only that company.
        """
        if not index or not index.isValid():
            return
        row = int(index.row())
        if index.column() == 0:
            return
        company = self.company_model.company_at(row)
        if not company:
            return
        self.company_model.set_checked_companies([company])

    def on_roadmap_scope_changed(self) -> None:
        """Invalidate and optionally refresh the roadmap after scope changes."""
        self.mark_analysis_dirty("roadmap")
        if self.main_tabs.currentIndex() == MAIN_TAB_ROADMAP:
            self.refresh_topic_roadmap(force=True)

    def on_main_tab_changed(self, index: int) -> None:
        """Lazy-load the newly visible top-level tab only when it enters view."""
        self.logger.info("main_tab_changed index=%s", index)
        if index == MAIN_TAB_SOURCES:
            self.reload_sources_surface(force=False)
            return
        if index == MAIN_TAB_ANALYTICS:
            self.refresh_analytics(force=False)
            return
        if index == MAIN_TAB_ROADMAP:
            self.refresh_topic_roadmap(force=False)
            return
        self.reload_workbench_surface(force=False)

    def on_analysis_tab_changed(self, index: int) -> None:
        """Compatibility hook for older nested analysis tab wiring."""
        self.logger.info("analysis_tab_changed index=%s", index)
        self.refresh_visible_analysis_tab(force=False)

    def mark_analysis_dirty(self, key: str) -> None:
        """Mark one description or analysis pane as stale until it is next shown or forced."""
        self.dirty_tabs.add(key)

    def refresh_visible_analysis_tab(self, *, force: bool = False) -> None:
        """Refresh only the currently visible derived-content tab."""
        index = self.main_tabs.currentIndex()
        if index == MAIN_TAB_ANALYTICS:
            self.refresh_analytics(force=force)
        elif index == MAIN_TAB_ROADMAP:
            self.refresh_topic_roadmap(force=force)

    def next_token(self, key: str) -> int:
        """Advance the stale-result token for a request-graph key."""
        self.request_tokens[key] += 1
        return self.request_tokens[key]

    def start_task(self, task_id: str, pane: str, label: str, *, determinate: bool = False, total: int = 0, control: Optional[Any] = None) -> None:
        self.active_tasks[task_id] = {
            "pane": pane,
            "label": label,
            "determinate": determinate,
            "total": total,
            "value": 0,
            "control": control,
        }
        self.pane_tasks[pane][task_id] = self.active_tasks[task_id]
        if control is not None:
            control.setEnabled(False)
        self.refresh_busy_ui()

    def update_task_progress(self, task_id: str, value: int, *, label: Optional[str] = None, total: Optional[int] = None) -> None:
        """Update one active task's progress metadata and repaint busy strips."""
        task = self.active_tasks.get(task_id)
        if not task:
            return
        if label is not None:
            task["label"] = label
        if total is not None:
            task["total"] = total
            task["determinate"] = total > 0
        task["value"] = value
        self.refresh_busy_ui()

    def finish_task(self, task_id: str) -> None:
        """Clear one tracked task and restore any disabled control it owned."""
        task = self.active_tasks.pop(task_id, None)
        if not task:
            return
        pane = str(task.get("pane") or "global")
        self.pane_tasks[pane].pop(task_id, None)
        control = task.get("control")
        if control is not None:
            control.setEnabled(True)
        self.refresh_busy_ui()

    def refresh_busy_ui(self) -> None:
        """Project active task state into the global and pane-level busy indicators."""
        if self.active_tasks:
            task_id = next(reversed(self.active_tasks))
            task = self.active_tasks[task_id]
            label = f"{len(self.active_tasks)} active | {task.get('label') or ''}"
            if task.get("determinate") and int(task.get("total") or 0) > 0:
                self.global_busy.set_busy(label, determinate=True, total=int(task.get("total") or 0), value=int(task.get("value") or 0))
            else:
                self.global_busy.set_busy(label)
        else:
            self.global_busy.clear()

        pane_map = {
            "sources": self.sources_busy,
            "company": self.company_busy,
            "jobs": self.jobs_busy,
            "description": self.description_busy,
            "analytics": self.analytics_busy,
            "roadmap": self.roadmap_busy,
        }
        for pane, strip in pane_map.items():
            tasks = self.pane_tasks.get(pane) or {}
            if not tasks:
                strip.clear()
                continue
            task_id = next(reversed(tasks))
            task = tasks[task_id]
            if task.get("determinate") and int(task.get("total") or 0) > 0:
                strip.set_busy(str(task.get("label") or ""), determinate=True, total=int(task.get("total") or 0), value=int(task.get("value") or 0))
            else:
                strip.set_busy(str(task.get("label") or ""))

    def queue_request(
        self,
        *,
        key: str,
        pane: str,
        label: str,
        signature: Any,
        fn: Callable[[], Any],
        on_success: Callable[[Any], None],
        force: bool = False,
        control: Optional[Any] = None,
    ) -> None:
        """Schedule one pane refresh in the shared worker pool and ignore stale completions."""
        sig = stable_signature(signature)
        if not force and self.request_signatures.get(key) == sig:
            return
        self.request_signatures[key] = sig
        self.logger.info("queue_request key=%s pane=%s force=%s", key, pane, force)
        token = self.next_token(key)
        task_id = f"{key}:{token}"
        self.start_task(task_id, pane, label, control=control)
        task = BackgroundTask(key=key, token=token, fn=fn)
        task.signals.finished.connect(lambda finished_key, finished_token, result, cb=on_success, tid=task_id: self.handle_request_finished(tid, finished_key, finished_token, cb, result))
        task.signals.failed.connect(lambda failed_key, failed_token, error, tid=task_id: self.handle_request_failed(tid, failed_key, failed_token, error))
        self.thread_pool.start(task)

    def handle_request_finished(
        self,
        task_id: str,
        key: str,
        token: int,
        callback: Callable[[Any], None],
        result: Any,
    ) -> None:
        self.finish_task(task_id)
        if token != self.request_tokens.get(key):
            self.logger.info("drop_stale_result key=%s token=%s", key, token)
            return
        self.logger.info("request_finished key=%s token=%s", key, token)
        callback(result)

    def handle_request_failed(self, task_id: str, key: str, token: int, error: str) -> None:
        self.finish_task(task_id)
        if token != self.request_tokens.get(key):
            self.logger.info("drop_stale_error key=%s token=%s", key, token)
            return
        self.logger.error("request_failed key=%s token=%s error=%s", key, token, compact_text(error, 240))
        self.append_activity(error)
        QMessageBox.warning(self, "Background Task Failed", compact_text(error, 2000))

    def selected_source_row(self) -> Optional[Dict[str, Any]]:
        """Return the currently selected source row from the Sources tab."""
        selection_model = self.sources_table.selectionModel()
        if selection_model is None:
            return None
        rows = selection_model.selectedRows()
        if not rows:
            return None
        row_index = rows[0].row()
        if not (0 <= row_index < len(self.sources_model.rows)):
            return None
        return dict(self.sources_model.rows[row_index])

    @staticmethod
    def _source_identity(row: Dict[str, Any]) -> tuple[str, str, str, str]:
        return (
            str(row.get("company") or "").strip().casefold(),
            str(row.get("ats") or "").strip().casefold(),
            str(row.get("token") or "").strip(),
            str(row.get("url") or "").strip(),
        )

    def _load_active_source_config_rows(self) -> List[Dict[str, Any]]:
        """Load the editable source JSON rows, copying bundled defaults if needed."""
        return load_source_config_rows(self.sources_path)

    @staticmethod
    def validate_source_edit_values(source: Dict[str, Any]) -> str:
        """Return an error string for invalid editable source values."""
        return validate_source_edit_values(source)

    def save_source_config_edit(self, selected: Dict[str, Any], edited_source: Dict[str, Any]) -> Dict[str, Any]:
        """Write one source JSON edit after validation and backup."""
        return save_source_config_edit_task(self.db_path, self.sources_path, selected, edited_source)

    def queue_source_config_edit(self, selected: Dict[str, Any], edited_source: Dict[str, Any]) -> None:
        """Persist one source edit in the worker pool instead of the GUI thread."""
        self.queue_request(
            key="source_edit",
            pane="sources",
            label="Saving source edit",
            signature={
                "db_path": str(self.db_path),
                "sources_path": str(self.sources_path),
                "selected": self._source_identity(selected),
                "edited": dict(edited_source),
            },
            fn=lambda: save_source_config_edit_task(self.db_path, self.sources_path, selected, edited_source),
            on_success=self.on_source_config_edit_saved,
            force=True,
            control=self.source_edit_button,
        )

    def on_source_config_edit_saved(self, result: Dict[str, Any]) -> None:
        """Refresh source-dependent panes after one source edit is applied."""
        source = dict(result.get("source") or {})
        self.data_epoch += 1
        self.loaded_payload_signatures.clear()
        self.mark_analysis_dirty("description")
        self.mark_analysis_dirty("analytics")
        self.mark_analysis_dirty("roadmap")
        self.append_activity(
            f"Updated source row: {source.get('company') or 'source'} "
            f"backup={result.get('backup_path')}"
        )
        self.reload_sources(force=True)
        self.reload_company_counts(force=True)
        self.reload_jobs(force=True)

    def edit_selected_source(self) -> None:
        """Edit the selected source row in the active source JSON."""
        selected = self.selected_source_row()
        if not selected:
            QMessageBox.information(self, "Edit Source", "Select a source row first.")
            return
        try:
            rows = self._load_active_source_config_rows()
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            QMessageBox.warning(self, "Edit Source", f"Could not read source file:\n{exc}")
            return
        target_identity = self._source_identity(selected)
        target_index = next((index for index, row in enumerate(rows) if self._source_identity(row) == target_identity), -1)
        if target_index < 0:
            QMessageBox.warning(self, "Edit Source", "Selected source row was not found in the active source file.")
            return
        source = dict(rows[target_index])

        dialog = QDialog(self)
        dialog.setWindowTitle("Edit Source")
        form = QFormLayout(dialog)
        enabled_box = QCheckBox("Enabled", dialog)
        enabled_box.setChecked(bool(source.get("enabled", True)))
        ats_edit = QLineEdit(str(source.get("ats") or ""), dialog)
        url_edit = QLineEdit(str(source.get("url") or source.get("entry_url") or ""), dialog)
        tags_edit = QLineEdit(", ".join(str(tag) for tag in source.get("tags") or []), dialog)
        notes_edit = QPlainTextEdit(str(source.get("notes") or ""), dialog)
        notes_edit.setFixedHeight(80)
        form.addRow("Enabled", enabled_box)
        form.addRow("ATS/source type", ats_edit)
        form.addRow("URL", url_edit)
        form.addRow("Tags", tags_edit)
        form.addRow("Notes", notes_edit)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel, dialog)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        ats = ats_edit.text().strip().lower()
        url = url_edit.text().strip()
        source["enabled"] = enabled_box.isChecked()
        source["ats"] = ats
        source["url"] = url
        source["entry_url"] = url or str(source.get("entry_url") or "")
        source["tags"] = self.parse_csv(tags_edit.text())
        source["notes"] = notes_edit.toPlainText().strip()
        self.queue_source_config_edit(selected, source)

    def reload_sources(self, *, force: bool = False) -> None:
        """Load the source-admin table from SQLite."""
        signature = {"epoch": self.data_epoch}
        self.queue_request(
            key="sources",
            pane="sources",
            label="Loading sources",
            signature=signature,
            fn=lambda: db.list_sources(self.db_path),
            on_success=self.on_sources_loaded,
            force=force,
        )

    def on_sources_loaded(self, rows: Sequence[Dict[str, Any]]) -> None:
        """Store source rows and rebuild dependent source filters."""
        previous_source_id = int((self.selected_source_row() or {}).get("id") or 0)
        self.current_sources = [dict(row) for row in rows]
        self.refresh_portal_filter_options()
        self.refresh_source_filter_options()
        self.refresh_hn_review_state()
        self.apply_source_table_filter(previous_source_id=previous_source_id)

    def selected_source_health_filter(self) -> str:
        """Return the active source-health table filter."""
        return str(self.source_health_filter_combo.currentData() or "").strip().lower()

    def source_health_summary_text(self) -> str:
        """Build a compact source-health summary for the Sources tab header."""
        counts: Dict[str, int] = defaultdict(int)
        for row in self.current_sources:
            counts[str(row.get("source_health_group") or "new").strip().lower()] += 1
        preferred = ("healthy", "new", "blocked", "parser failure", "disabled")
        parts = [f"{group}={counts[group]}" for group in preferred if counts.get(group)]
        visible = len(self.sources_model.rows)
        total = len(self.current_sources)
        scope = f"showing {visible}/{total}" if visible != total else f"{total} sources"
        return f"Source Diagnostics | {scope}" + (f" | {', '.join(parts)}" if parts else "")

    def apply_source_table_filter(self, *, previous_source_id: Optional[int] = None) -> None:
        """Filter the source diagnostics table by health group while preserving selection."""
        if previous_source_id is None:
            previous_source_id = int((self.selected_source_row() or {}).get("id") or 0)
        group_filter = self.selected_source_health_filter()
        if group_filter:
            rows = [
                row
                for row in self.current_sources
                if str(row.get("source_health_group") or "").strip().lower() == group_filter
            ]
        else:
            rows = list(self.current_sources)
        self.sources_model.set_rows(rows)
        selection = self.sources_table.selectionModel()
        if selection is not None:
            row_index = -1
            if previous_source_id:
                for idx, row in enumerate(self.sources_model.rows):
                    if int(row.get("id") or 0) == previous_source_id:
                        row_index = idx
                        break
            if row_index < 0 and self.sources_model.rows:
                row_index = 0
            if row_index >= 0:
                selection.select(
                    self.sources_model.index(row_index, 0),
                    selection.SelectionFlag.ClearAndSelect | selection.SelectionFlag.Rows,
                )
                self.sources_table.scrollTo(
                    self.sources_model.index(row_index, 0),
                    QAbstractItemView.ScrollHint.PositionAtTop,
                )
                self.on_source_selection_changed()
                return
        self.on_source_selection_changed()

    def on_source_selection_changed(self) -> None:
        """Update source-diagnostics affordances from the current source-row selection."""
        row = self.selected_source_row()
        self.source_focus_button.setEnabled(bool(row))
        self.source_edit_button.setEnabled(bool(row))
        self.open_source_in_browser_action.setEnabled(bool(row))
        if row:
            source_name = str(row.get("company") or "source")
            portal = str(row.get("portal") or "company_boards") or "company_boards"
            group = str(row.get("source_health_group") or "new").strip()
            quality = int(row.get("source_quality_score") or 0)
            detail = compact_text(str(row.get("last_error") or row.get("last_status") or row.get("discovery_notes") or ""), 180)
            label = f"Selected source: {source_name} | {portal} | {group} | quality={quality}"
            if detail:
                label += f" | {detail}"
            self.sources_tab.sources_label.setText(label)
            return
        self.sources_tab.sources_label.setText(self.source_health_summary_text())

    def focus_selected_source_in_workbench(self) -> None:
        """Project the selected source row into the Workbench filters and switch tabs."""
        row = self.selected_source_row()
        if not row:
            QMessageBox.information(self, "Focus In Workbench", "Select a source row first.")
            return
        portal_value = str(row.get("portal") or "").strip().lower()
        source_id = int(row.get("id") or 0)
        target_portal = portal_value or "company_boards"
        self.reset_company_filter_on_counts = True
        self.main_tabs.setCurrentIndex(MAIN_TAB_WORKBENCH)
        self.portal_filter_combo.blockSignals(True)
        for idx in range(self.portal_filter_combo.count()):
            if str(self.portal_filter_combo.itemData(idx) or "") == target_portal:
                self.portal_filter_combo.setCurrentIndex(idx)
                break
        self.portal_filter_combo.blockSignals(False)
        self.refresh_source_filter_options()
        self.source_filter_combo.blockSignals(True)
        for idx in range(self.source_filter_combo.count()):
            if int(self.source_filter_combo.itemData(idx) or 0) == source_id:
                self.source_filter_combo.setCurrentIndex(idx)
                break
        self.source_filter_combo.blockSignals(False)
        self.refresh_hn_review_state()
        self.reload_stacks(force=True)
        self.schedule_jobs_refresh()

    def refresh_portal_filter_options(self) -> None:
        """Rebuild the source-family combo from the currently loaded source rows."""
        current = str(self.portal_filter_combo.currentData() or "")
        portals = sorted({str(row.get("portal") or "").strip() for row in self.current_sources if str(row.get("portal") or "").strip()})
        self.portal_filter_combo.blockSignals(True)
        self.portal_filter_combo.clear()
        self.portal_filter_combo.addItem("All sources", "")
        self.portal_filter_combo.addItem("Company boards", "company_boards")
        for portal in portals:
            self.portal_filter_combo.addItem(portal.title(), portal)
        index = 0
        for idx in range(self.portal_filter_combo.count()):
            if str(self.portal_filter_combo.itemData(idx) or "") == current:
                index = idx
                break
        self.portal_filter_combo.setCurrentIndex(index)
        self.portal_filter_combo.blockSignals(False)

    def refresh_source_filter_options(self) -> None:
        """Rebuild the source-row combo from the current portal scope."""
        current_source_id = int(self.source_filter_combo.currentData() or 0)
        selected_portal = str(self.portal_filter_combo.currentData() or "").strip().lower()
        rows = []
        for row in self.current_sources:
            portal = str(row.get("portal") or "").strip().lower()
            if selected_portal == "company_boards":
                if portal:
                    continue
            elif selected_portal and portal != selected_portal:
                continue
            rows.append(dict(row))
        rows.sort(
            key=lambda item: (
                str(item.get("company") or "").lower(),
                str(item.get("entry_kind") or "").lower(),
                str(item.get("ats") or "").lower(),
            )
        )
        self.source_filter_combo.blockSignals(True)
        self.source_filter_combo.clear()
        self.source_filter_combo.addItem("All source rows", 0)
        selected_index = 0
        for index, row in enumerate(rows, start=1):
            label = str(row.get("company") or "Source")
            entry_kind = str(row.get("entry_kind") or row.get("ats") or "").replace("_", " ").strip()
            counts = f"{int(row.get('matching_count') or 0)}/{int(row.get('open_count') or 0)}"
            suffix = f" | {entry_kind}" if entry_kind else ""
            self.source_filter_combo.addItem(f"{label}{suffix} | {counts}", int(row.get("id") or 0))
            if int(row.get("id") or 0) == current_source_id:
                selected_index = index
        self.source_filter_combo.setCurrentIndex(selected_index)
        self.source_filter_combo.blockSignals(False)

    def refresh_hn_review_state(self) -> None:
        """Enable the HN review toggle only when the current source scope is Hacker News."""
        is_hn = self.source_scope_is_hackernews()
        if not is_hn:
            self.hn_review_combo.blockSignals(True)
            self.hn_review_combo.setCurrentIndex(0)
            self.hn_review_combo.blockSignals(False)
        self.hn_review_combo.setEnabled(is_hn)

    def reload_stacks(self, *, force: bool = False) -> None:
        """Reload stack/tag options for the current non-search jobs scope."""
        signature = {
            "epoch": self.data_epoch,
            "matching": self.matching_only_checkbox.isChecked(),
            "open": self.open_only_checkbox.isChecked(),
            "portal": str(self.portal_filter_combo.currentData() or ""),
            "source_id": int(self.source_filter_combo.currentData() or 0),
            "hn_mode": str(self.hn_review_combo.currentData() or "") if self.hn_review_combo.isEnabled() else "",
            "source_tag": str(self.source_tag_combo.currentData() or ""),
            "founding": self.founding_only_checkbox.isChecked(),
        }
        self.queue_request(
            key="stacks",
            pane="jobs",
            label="Loading stack filters",
            signature=signature,
            fn=lambda: db.list_stack_names(
                self.db_path,
                matching_only=self.matching_only_checkbox.isChecked(),
                open_only=self.open_only_checkbox.isChecked(),
                portal=str(self.portal_filter_combo.currentData() or ""),
                source_id=int(self.source_filter_combo.currentData() or 0),
                source_tag=str(self.source_tag_combo.currentData() or ""),
                hn_mode=str(self.hn_review_combo.currentData() or "") if self.hn_review_combo.isEnabled() else "",
                founding_only=self.founding_only_checkbox.isChecked(),
            ),
            on_success=self.on_stacks_loaded,
            force=force,
        )

    def on_stacks_loaded(self, names: Sequence[str]) -> None:
        """Refresh the stack filter combo while preserving the selected value."""
        current = str(self.stack_filter_combo.currentData() or "")
        self.stack_filter_combo.blockSignals(True)
        self.stack_filter_combo.clear()
        self.stack_filter_combo.addItem("All stacks", "")
        for name in names:
            self.stack_filter_combo.addItem(str(name), str(name))
        index = 0
        for idx in range(self.stack_filter_combo.count()):
            if str(self.stack_filter_combo.itemData(idx) or "") == current:
                index = idx
                break
        self.stack_filter_combo.setCurrentIndex(index)
        self.stack_filter_combo.blockSignals(False)

    def reload_company_counts(self, *, force: bool = False) -> None:
        """Reload company sidebar counts for the current non-company filters."""
        filters = self.current_non_company_filters()
        signature = {"epoch": self.data_epoch, **filters}
        self.queue_request(
            key="company_counts",
            pane="company",
            label="Loading company counts",
            signature=signature,
            fn=lambda: db.list_company_counts(
                self.db_path,
                matching_only=filters["matching_only"],
                open_only=filters["open_only"],
                search=filters["search"],
                stack=filters["stack"],
                portal=filters["portal"],
                source_id=filters["source_id"],
                source_tag=filters["source_tag"],
                hn_mode=filters["hn_mode"],
                founding_only=filters["founding_only"],
            ),
            on_success=self.on_company_counts_loaded,
            force=force,
        )

    def on_company_counts_loaded(self, rows: Sequence[Dict[str, Any]]) -> None:
        """Replace the company sidebar model with the latest counts."""
        if self.reset_company_filter_on_counts:
            self.company_model.set_rows(rows, preserve=[])
            self.reset_company_filter_on_counts = False
            return
        self.company_model.set_rows(rows)

    def reload_jobs(self, *, force: bool = False) -> None:
        """Refresh the jobs pane from SQLite using a lightweight summary query."""
        filters = self.current_filters()
        grouped = self.group_by_company_checkbox.isChecked()
        signature = {"epoch": self.data_epoch, **filters, "grouped": grouped}
        self.queue_request(
            key="jobs",
            pane="jobs",
            label="Loading jobs",
            signature=signature,
            fn=lambda: load_jobs_view_task(
                self.db_path,
                matching_only=filters["matching_only"],
                open_only=filters["open_only"],
                companies=filters["companies"],
                portal=filters["portal"],
                source_id=filters["source_id"],
                source_tag=filters["source_tag"],
                hn_mode=filters["hn_mode"],
                founding_only=filters["founding_only"],
                search=filters["search"],
                stack=filters["stack"],
                group_by_company=grouped,
                limit=2000,
            ),
            on_success=self.on_jobs_loaded,
            force=force,
        )

    def on_jobs_loaded(self, payload: Dict[str, Any]) -> None:
        """Update the jobs model, summary text, and restored selection."""
        previous_selected = self.current_selected_job_id
        self.current_jobs = [dict(row) for row in payload.get("jobs") or []]
        rows_changed = self.jobs_model.set_rows(payload.get("rows") or [], signature=payload.get("row_signature"))
        if rows_changed:
            self.apply_job_spans()
        filters = self.current_filters()
        source_row = self.current_source_row_filter()
        source_scope = compact_text(str(source_row.get("company") or "all"), 40) if source_row else "all"
        checked_companies = len(filters["companies"])
        company_scope = "all" if checked_companies >= max(1, self.company_model.rowCount()) else str(checked_companies)
        self.jobs_summary_label.setText(
            f"{len(self.current_jobs)} jobs | companies selected: {company_scope} | "
            f"source family: {filters['portal'] or 'all'} | source row: {source_scope} | "
            f"tag: {filters['source_tag'] or 'all'} | hn: {filters['hn_mode'] or 'all'} | "
            f"stack: {filters['stack'] or 'all'} | search: {filters['search'] or 'none'}"
        )
        self.restore_job_selection(previous_selected)

    def apply_job_spans(self) -> None:
        """Apply full-row spans for grouped company header rows."""
        span_signature = tuple(
            (row_index, int(row.get("count") or 0))
            for row_index, row in enumerate(self.jobs_model.rows)
            if row.get("row_type") == "group"
        )
        if span_signature == getattr(self, "_job_span_signature", None):
            return
        self.jobs_table.clearSpans()
        for row_index, row in enumerate(self.jobs_model.rows):
            if row.get("row_type") == "group":
                self.jobs_table.setSpan(row_index, 0, 1, self.jobs_model.columnCount())
        self._job_span_signature = span_signature

    def restore_job_selection(self, preferred_job_id: Optional[int]) -> None:
        """Restore a stable job selection after the jobs model is refreshed."""
        selection = self.jobs_table.selectionModel()
        if selection is None:
            return
        row = -1
        if preferred_job_id:
            row = self.jobs_model.row_for_job_id(preferred_job_id)
        if row < 0:
            for idx, item in enumerate(self.jobs_model.rows):
                if item.get("row_type") == "job":
                    row = idx
                    break
        if row >= 0:
            index = self.jobs_model.index(row, 0)
            selection.select(index, selection.SelectionFlag.ClearAndSelect | selection.SelectionFlag.Rows)
            visual_rect = self.jobs_table.visualRect(index)
            if not visual_rect.isValid() or not self.jobs_table.viewport().rect().intersects(visual_rect):
                self.jobs_table.scrollTo(index, QAbstractItemView.ScrollHint.PositionAtCenter)
        else:
            self.current_selected_job_id = None
            self.current_selected_job_preview = None
            self.current_selected_job_detail = None
            self.current_analytics_payload = None
            self.current_roadmap_payload = None
            self.update_selection_headers(None)
            self.set_browser_html("description", self.description_browser, html_shell("Description", "<p class='meta'>No job selected.</p>"))
            self.set_browser_html("roadmap", self.roadmap_browser, html_shell("Topic Roadmap", "<p class='meta'>No roadmap available.</p>"))

    def on_jobs_selection_changed(self) -> None:
        """Drive details-and-analysis invalidation from the current jobs-table selection."""
        selection = self.jobs_table.selectionModel()
        if selection is None:
            return
        indexes = selection.selectedRows()
        if not indexes:
            return
        job_id = self.jobs_model.job_id_at(indexes[0].row())
        if not job_id:
            return
        self.current_selected_job_id = job_id
        self.current_selected_job_preview = self.jobs_model.preview_for_job_id(job_id)
        self.current_selected_job_detail = None
        self.update_selection_headers(self.current_selected_job_preview)
        self.mark_analysis_dirty("description")
        if self.pane_is_visible("description"):
            self.reload_selected_job_detail(force=False)
        if str(self.roadmap_scope_combo.currentData() or "selected") == "selected":
            self.mark_analysis_dirty("roadmap")
            if self.pane_is_visible("roadmap"):
                self.refresh_topic_roadmap(force=False)

    def update_selection_headers(self, job: Optional[Dict[str, Any]]) -> None:
        """Render compact selected-job header for the Workbench description surface."""
        if not job:
            self.description_header.setText("No job selected")
            return
        header_text = " | ".join(
            part for part in [
                compact_text(job.get("company"), 32),
                compact_text(job.get("title"), 72),
                compact_text(job.get("location"), 72),
            ] if part
        )
        self.description_header.setText(header_text)

    def reload_selected_job_detail(self, *, force: bool = False) -> None:
        """Load the selected-job detail payload in the background."""
        if not self.current_selected_job_id:
            return
        if not force and "description" not in self.dirty_tabs:
            return
        signature = {"epoch": self.data_epoch, "job_id": self.current_selected_job_id}
        signature_key = stable_signature(signature)
        if (
            not force
            and self.loaded_payload_signatures.get("job_detail") == signature_key
            and self.current_selected_job_detail is not None
        ):
            self.render_selected_job_detail(self.current_selected_job_detail)
            return
        self.queue_request(
            key="job_detail",
            pane="description",
            label="Loading job detail",
            signature=signature,
            fn=lambda: load_job_detail_view_task(self.db_path, int(self.current_selected_job_id or 0)),
            on_success=self.on_job_detail_loaded,
            force=force,
        )

    def render_selected_job_detail(self, job: Dict[str, Any]) -> None:
        """Apply the prepared selected-job detail HTML only when the tab is visible."""
        self.update_selection_headers(job)
        html = str(job.get("_rendered_html") or "")
        if html:
            self.set_browser_html("description", self.description_browser, html)
        self.dirty_tabs.discard("description")

    def on_job_detail_loaded(self, payload: Dict[str, Any]) -> None:
        """Store selected-job detail and render it only when visible."""
        job = payload.get("job")
        html = str(payload.get("html") or html_shell("Description", "<p class='meta'>Job detail could not be loaded.</p>"))
        self.loaded_payload_signatures["job_detail"] = self.request_signatures.get("job_detail", "")
        if not job:
            self.current_selected_job_detail = None
            if self.pane_is_visible("description"):
                self.set_browser_html("description", self.description_browser, html)
                self.dirty_tabs.discard("description")
            return
        detail = dict(job)
        detail["_rendered_html"] = html
        self.current_selected_job_detail = detail
        if self.pane_is_visible("description"):
            self.render_selected_job_detail(detail)

    def refresh_analytics(self, *, force: bool = False) -> None:
        """Refresh analytics only when the pane is dirty or explicitly forced."""
        if not force and "analytics" not in self.dirty_tabs:
            return
        filters = self.current_filters()
        signature = {"epoch": self.data_epoch, **filters}
        signature_key = stable_signature(signature)
        cached_payload = self.analytics_cache.get(signature_key)
        if not force and cached_payload is not None:
            self.current_analytics_payload = dict(cached_payload)
            self.loaded_payload_signatures["analytics"] = signature_key
            self.render_analytics_view(self.current_analytics_payload)
            return
        if (
            not force
            and self.loaded_payload_signatures.get("analytics") == signature_key
            and self.current_analytics_payload is not None
        ):
            self.render_analytics_view(self.current_analytics_payload)
            return
        self.queue_request(
            key="analytics",
            pane="analytics",
            label="Loading analytics",
            signature=signature,
            fn=lambda: load_analytics_view_task(
                self.db_path,
                matching_only=filters["matching_only"],
                open_only=filters["open_only"],
                companies=filters["companies"],
                portal=filters["portal"],
                source_id=filters["source_id"],
                source_tag=filters["source_tag"],
                hn_mode=filters["hn_mode"],
                search=filters["search"],
                stack=filters["stack"],
                founding_only=filters["founding_only"],
            ),
            on_success=self.on_analytics_loaded,
            force=force,
        )

    def render_analytics_view(self, payload: Dict[str, Any]) -> None:
        """Apply the prepared analytics HTML only when the pane is visible."""
        html = str(payload.get("_rendered_html") or "")
        if html:
            self.set_browser_html("analytics", self.analytics_browser, html)
        self.dirty_tabs.discard("analytics")

    def on_analytics_loaded(self, payload: Dict[str, Any]) -> None:
        """Store analytics output and render it only when visible."""
        analytics_payload = dict(payload.get("payload") or {})
        analytics_payload["_rendered_html"] = str(payload.get("html") or "")
        self.current_analytics_payload = analytics_payload
        signature_key = self.request_signatures.get("analytics", "")
        self.loaded_payload_signatures["analytics"] = signature_key
        if signature_key:
            self.analytics_cache[signature_key] = dict(analytics_payload)
            self.analytics_cache.move_to_end(signature_key)
            while len(self.analytics_cache) > 24:
                self.analytics_cache.popitem(last=False)
        if self.pane_is_visible("analytics"):
            self.render_analytics_view(analytics_payload)
            return
        self.mark_analysis_dirty("analytics")

    def refresh_topic_roadmap(self, *, force: bool = False) -> None:
        """Generate the roadmap only when its pane is relevant or explicitly requested."""
        if not force and "roadmap" not in self.dirty_tabs:
            return
        scope_mode = str(self.roadmap_scope_combo.currentData() or "selected")
        filters = self.current_filters()
        selected_companies = filters.get("companies") or []
        selected_job_ids = self.selected_job_ids()
        if scope_mode == "selected" and not selected_job_ids:
            selected_job_ids = self.jobs_model.visible_job_ids()[:100]
        signature = {
            "epoch": self.data_epoch,
            "scope": scope_mode,
            "selected_job_ids": selected_job_ids,
            "filters": filters,
        }
        signature_key = stable_signature(signature)
        if (
            not force
            and self.loaded_payload_signatures.get("roadmap") == signature_key
            and self.current_roadmap_payload is not None
        ):
            self.render_roadmap_view(self.current_roadmap_payload)
            return
        self.queue_request(
            key="roadmap",
            pane="roadmap",
            label="Building roadmap",
            signature=signature,
            fn=lambda: build_roadmap_payload(
                self.db_path,
                scope_mode=scope_mode,
                selected_job_ids=selected_job_ids,
                current_job_filters=filters,
                selected_companies=selected_companies,
            ),
            on_success=self.on_roadmap_loaded,
            force=force,
        )

    def render_roadmap_view(self, payload: Dict[str, Any]) -> None:
        """Apply the prepared roadmap HTML only when the pane is visible."""
        self.roadmap_summary_label.setText(str(payload.get("_summary") or ""))
        html = str(payload.get("_rendered_html") or "")
        if html:
            self.set_browser_html("roadmap", self.roadmap_browser, html)
        self.dirty_tabs.discard("roadmap")

    def on_roadmap_loaded(self, payload: Dict[str, Any]) -> None:
        """Store the roadmap payload and render it only for the visible pane."""
        roadmap_payload = dict(payload.get("payload") or {})
        roadmap_payload["_summary"] = str(payload.get("summary") or "")
        roadmap_payload["_rendered_html"] = str(payload.get("html") or "")
        self.current_roadmap_payload = roadmap_payload
        self.loaded_payload_signatures["roadmap"] = self.request_signatures.get("roadmap", "")
        if self.pane_is_visible("roadmap"):
            self.render_roadmap_view(roadmap_payload)
            return
        self.mark_analysis_dirty("roadmap")

    def select_companies_from_jobs(self) -> None:
        """Replace company filters with the employers from the selected job rows."""
        companies = []
        selection = self.jobs_table.selectionModel()
        if selection is not None:
            for index in selection.selectedRows():
                row = self.jobs_model.rows[index.row()]
                if row.get("row_type") == "job" and row.get("company"):
                    companies.append(str(row.get("company")))
        if companies:
            self.company_model.set_checked_companies(companies)

    def import_sources(self) -> None:
        """Open a file picker and import source definitions in the worker pool."""
        selected, _ = QFileDialog.getOpenFileName(self, "Import Sources", str(self.sources_path), "JSON Files (*.json);;All Files (*)")
        if not selected:
            return
        task_id = "preview_import_sources"
        self.start_task(task_id, "sources", "Previewing source import", control=self.import_action)
        token = self.next_token(task_id)
        selected_path = Path(selected)
        task = BackgroundTask(key=task_id, token=token, fn=lambda: preview_source_import_task(self.db_path, selected_path))
        task.signals.finished.connect(
            lambda key, finished_token, result, tid=task_id: self.on_import_sources_preview_done(
                tid,
                finished_token,
                token,
                selected_path,
                result,
            )
        )
        task.signals.failed.connect(lambda key, finished_token, error, tid=task_id: self.on_simple_task_failed(tid, error))
        self.thread_pool.start(task)

    def _source_import_preview_message(self, selected_path: Path, preview: Dict[str, Any]) -> str:
        """Build the operator confirmation text for one source import preview."""
        message = (
            f"Import {int(preview.get('total') or 0)} source rows from:\n{selected_path}\n\n"
            f"New: {int(preview.get('new') or 0)}\n"
            f"Updated: {int(preview.get('updated') or 0)}\n"
            f"Stale DB-only sources that will be disabled: {int(preview.get('stale_disabled') or 0)}\n"
            f"Enabled in file: {int(preview.get('enabled') or 0)}\n"
            f"Disabled in file: {int(preview.get('disabled') or 0)}\n\n"
            "A SQLite backup will be created before applying the import."
        )
        return message

    def on_import_sources_preview_done(
        self,
        task_id: str,
        finished_token: int,
        expected_token: int,
        selected_path: Path,
        preview: Dict[str, Any],
    ) -> None:
        """Confirm and apply a source import after the preview worker finishes."""
        self.finish_task(task_id)
        if finished_token != expected_token:
            return
        import_path = Path(str(preview.get("path") or selected_path))
        message = self._source_import_preview_message(import_path, preview)
        if QMessageBox.question(self, "Confirm Source Import", message) != QMessageBox.StandardButton.Yes:
            return
        self._start_import_sources_task(import_path)

    def _start_import_sources_task(self, selected_path: Path) -> None:
        """Start the source import worker after the operator confirms the preview."""
        task_id = "import_sources"
        self.start_task(task_id, "sources", "Importing sources", control=self.import_action)
        token = self.next_token(task_id)
        task = BackgroundTask(key=task_id, token=token, fn=lambda: import_sources_task(self.db_path, selected_path))
        task.signals.finished.connect(lambda key, finished_token, result, tid=task_id: self.on_import_sources_done(tid, finished_token, token, result))
        task.signals.failed.connect(lambda key, finished_token, error, tid=task_id: self.on_simple_task_failed(tid, error))
        self.thread_pool.start(task)

    def on_import_sources_done(self, task_id: str, finished_token: int, expected_token: int, result: Dict[str, Any]) -> None:
        """Advance the data epoch and refresh source-dependent panes after import."""
        self.finish_task(task_id)
        if finished_token != expected_token:
            return
        self.data_epoch += 1
        imported_path = str(result.get("path") or "").strip()
        if imported_path:
            self.sources_path = Path(imported_path)
            self.sources_path_edit.setText(str(self.sources_path))
            self.persist_settings()
        backup = str(result.get("backup_path") or "")
        self.append_activity(
            "Imported sources: "
            f"{int(result.get('count') or 0)} "
            f"(new={int(result.get('new') or 0)}, updated={int(result.get('updated') or 0)}, "
            f"stale_disabled={int(result.get('stale_disabled') or 0)})"
            + (f" backup={backup}" if backup else "")
        )
        self.reload_active_surface(force=True)
        self.update_command_summary()

    def probe_watchlist_and_import(self) -> None:
        """Probe candidate direct sources and import only verified rows."""
        if self.scrape_worker is not None:
            QMessageBox.information(self, "Probe Watchlist", "Wait for the active scrape to finish before probing sources.")
            return
        self.db_path = Path(self.db_path_edit.text().strip() or str(self.db_path))
        self.sources_path = Path(self.sources_path_edit.text().strip() or str(self.sources_path))
        self.source_watchlist_path = Path(self.watchlist_path_edit.text().strip() or str(self.source_watchlist_path))
        report_path = paths.reports_dir() / "source_candidate_report.json"
        message = (
            "Probe the source watchlist against public ATS APIs and append only verified source rows?\n\n"
            f"Watchlist: {self.source_watchlist_path}\n"
            f"Sources JSON: {self.sources_path}\n"
            f"Report: {report_path}\n\n"
            "If new rows are promoted, SQLite will be backed up before importing the updated source JSON."
        )
        if QMessageBox.question(self, "Probe Watchlist", message) != QMessageBox.StandardButton.Yes:
            return
        self.persist_settings()
        task_id = "probe_watchlist"
        self.start_task(task_id, "sources", "Probing source watchlist", control=self.source_probe_button)
        token = self.next_token(task_id)
        task = BackgroundTask(
            key=task_id,
            token=token,
            fn=lambda: probe_watchlist_and_import_task(
                self.db_path,
                self.source_watchlist_path,
                self.sources_path,
                report_path,
            ),
        )
        task.signals.finished.connect(lambda key, finished_token, result, tid=task_id: self.on_probe_watchlist_done(tid, finished_token, token, result))
        task.signals.failed.connect(lambda key, finished_token, error, tid=task_id: self.on_simple_task_failed(tid, error))
        self.thread_pool.start(task)

    def on_probe_watchlist_done(self, task_id: str, finished_token: int, expected_token: int, result: Dict[str, Any]) -> None:
        """Refresh source panes after watchlist probing/import finishes."""
        self.finish_task(task_id)
        if finished_token != expected_token:
            return
        promoted = int(result.get("promoted") or 0)
        valid = int(result.get("valid") or 0)
        probed = int(result.get("probed") or 0)
        duplicate_skipped = int(result.get("duplicate_skipped") or 0)
        rejected = int(result.get("rejected") or 0)
        import_report = dict(result.get("import_report") or {})
        backup = str(import_report.get("backup_path") or "")
        self.append_activity(
            "Watchlist probe complete: "
            f"probed={probed}, valid={valid}, promoted={promoted}, "
            f"duplicates={duplicate_skipped}, rejected={rejected}, report={result.get('report_path')}"
            + (f", backup={backup}" if backup else "")
        )
        if promoted:
            self.data_epoch += 1
            self.reload_active_surface(force=True)
            self.update_command_summary()
        else:
            self.reload_sources(force=True)

    def export_filtered_json(self) -> None:
        """Export the current jobs slice without blocking the GUI thread."""
        selected, _ = QFileDialog.getSaveFileName(self, "Export Jobs", str(paths.exports_dir() / "jobs.json"), "JSON Files (*.json)")
        if not selected:
            return
        filters = self.current_filters()
        task_id = "export"
        cancel_state = {"cancelled": False}
        self.export_cancel_state = cancel_state
        self.cancel_export_action.setEnabled(True)
        self.jobs_summary_label.setText(f"Exporting filtered jobs to {selected}")
        self.append_activity(f"Export started: {selected}")
        self.start_task(task_id, "jobs", f"Exporting to {Path(selected).name}", control=self.export_action)
        token = self.next_token(task_id)
        task = BackgroundTask(
            key=task_id,
            token=token,
            fn=lambda: export_jobs_task(
                self.db_path,
                selected,
                matching_only=filters["matching_only"],
                open_only=filters["open_only"],
                companies=filters["companies"],
                portal=filters["portal"],
                source_id=filters["source_id"],
                source_tag=filters["source_tag"],
                hn_mode=filters["hn_mode"],
                founding_only=filters["founding_only"],
                search=filters["search"],
                stack=filters["stack"],
                should_cancel=lambda: bool(cancel_state.get("cancelled")),
            ),
        )
        task.signals.finished.connect(lambda key, finished_token, result, tid=task_id: self.on_export_done(tid, finished_token, token, result))
        task.signals.failed.connect(lambda key, finished_token, error, tid=task_id: self.on_simple_task_failed(tid, error))
        self.thread_pool.start(task)

    def cancel_export(self) -> None:
        """Request cooperative cancellation for the active export task."""
        if not self.export_cancel_state:
            return
        self.export_cancel_state["cancelled"] = True
        self.cancel_export_action.setEnabled(False)
        self.update_task_progress("export", 0, label="Cancelling export")
        self.append_activity("Cancel requested for export.")

    def on_export_done(self, task_id: str, finished_token: int, expected_token: int, result: Dict[str, Any]) -> None:
        """Record the export result in the activity log."""
        self.finish_task(task_id)
        self.cancel_export_action.setEnabled(False)
        self.export_cancel_state = None
        if finished_token != expected_token:
            return
        out_path = str(result.get("out_path") or "")
        if bool(result.get("cancelled")):
            self.jobs_summary_label.setText(f"Export cancelled: {out_path}")
            self.append_activity(f"Export cancelled: {out_path}")
            return
        count = int(result.get("count") or 0)
        self.jobs_summary_label.setText(f"Exported {count} jobs to {out_path}")
        self.append_activity(f"Exported {count} jobs to {out_path}")

    def run_scrape(self) -> None:
        """Start the scrape worker and expose progress in the activity strips."""
        if self.scrape_worker is not None:
            return
        self.db_path = Path(self.db_path_edit.text().strip() or str(self.db_path))
        self.sources_path = Path(self.sources_path_edit.text().strip() or str(self.sources_path))
        options = core.ScrapeOptions(
            exclude_words=self.parse_csv(self.exclude_edit.text()),
            interest_terms=self.parse_csv(self.interests_edit.text()),
            enable_remote=self.remote_checkbox.isChecked(),
            enable_india_office_hybrid=self.india_checkbox.isChecked(),
            concurrency=int(self.concurrency_spin.value()),
            http_concurrency=int(self.http_concurrency_spin.value()),
            hackernews_parser_engine=str(self.hn_parser_combo.currentData() or "auto"),
        )
        self.scrape_progress_state = {
            "total": len([row for row in self.current_sources if bool(row.get("enabled"))]) or len(self.current_sources),
            "completed": 0,
            "ok": 0,
            "error": 0,
            "skipped": 0,
            "current_source": "",
            "last_line": "Preparing scrape...",
        }
        self.scrape_worker = ScrapeWorker(db_path=self.db_path, sources_path=self.sources_path, options=options)
        self.scrape_worker.log.connect(self.on_scrape_log)
        self.scrape_worker.done.connect(self.on_scrape_done)
        self.scrape_worker.failed.connect(self.on_scrape_failed)
        self.scrape_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        total = int(self.scrape_progress_state.get("total") or 0)
        self.start_task("scrape", "jobs", "Scraping sources", determinate=total > 0, total=total)
        self.scrape_worker.start()

    def stop_scrape(self) -> None:
        """Request a clean stop for the running scrape worker."""
        if self.scrape_worker is not None:
            self.scrape_worker.request_stop()
            self.append_activity("Stop requested for scrape.")

    def update_scrape_progress_state(self, line: str) -> None:
        """Reduce verbose scrape logs into a coarse UI progress model."""
        state = self.scrape_progress_state
        if not state:
            return
        text = str(line or "").strip()
        if not text:
            return
        state["last_line"] = text
        if text.startswith("Scraping "):
            current_source = text[len("Scraping "):].split(" (", 1)[0].strip()
            if current_source:
                state["current_source"] = current_source
            return
        if text.startswith("Done "):
            state["completed"] = int(state.get("completed") or 0) + 1
            state["ok"] = int(state.get("ok") or 0) + 1
            return
        if text.startswith("ERROR "):
            state["completed"] = int(state.get("completed") or 0) + 1
            state["error"] = int(state.get("error") or 0) + 1
            return
        status_word = text.split(" ", 1)[0]
        if status_word.isupper() and "_" in status_word:
            state["completed"] = int(state.get("completed") or 0) + 1
            state["skipped"] = int(state.get("skipped") or 0) + 1

    def flush_scrape_progress(self) -> None:
        """Project the coarse scrape summary into the busy strips at a bounded rate."""
        if not self.scrape_progress_state:
            return
        state = self.scrape_progress_state
        total = int(state.get("total") or 0)
        completed = int(state.get("completed") or 0)
        current_source = compact_text(str(state.get("current_source") or state.get("last_line") or ""), 72)
        summary = (
            f"Scraping {completed}/{total or '?'} | "
            f"ok={int(state.get('ok') or 0)} "
            f"error={int(state.get('error') or 0)} "
            f"skipped={int(state.get('skipped') or 0)}"
        )
        if current_source:
            summary += f" | {current_source}"
        self.update_task_progress(
            "scrape",
            completed if total > 0 else 0,
            label=summary,
            total=total if total > 0 else None,
        )

    def update_last_scrape_report(self, line: str) -> None:
        """Parse the scraper's final SCRAPE_SUMMARY line into a visible UI report."""
        if not line.startswith("SCRAPE_SUMMARY "):
            return
        values: Dict[str, str] = {}
        for part in line[len("SCRAPE_SUMMARY "):].split():
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            values[key.strip()] = value.strip()
        new_jobs = int(values.get("new_since_last_scrape") or values.get("net_new") or 0)
        matching_new = int(values.get("matching_new") or 0)
        total_jobs = int(values.get("jobs_after") or 0)
        sources = int(values.get("sources") or 0)
        fetched = int(values.get("fetched") or 0)
        label = f"Last scrape: +{new_jobs} new jobs"
        if matching_new:
            label += f", +{matching_new} matching"
        if total_jobs:
            label += f" | total={total_jobs}"
        if sources:
            label += f" | sources={sources}"
        if fetched:
            label += f" | fetched={fetched}"
        self.last_scrape_report_label.setText(label)

    @staticmethod
    def should_persist_scrape_log(line: str) -> bool:
        """Return whether one scrape-progress line is important enough for the file log."""
        text = str(line or "").strip()
        if not text:
            return False
        if text.startswith(("SCRAPE_SUMMARY ", "Done ", "ERROR ", "Imported ", "No enabled sources found.", "Stop requested")):
            return True
        status_word = text.split(" ", 1)[0]
        return status_word.isupper() and "_" in status_word

    @staticmethod
    def should_record_scrape_activity(line: str) -> bool:
        """Return whether one scrape-progress line belongs in the user-facing activity history."""
        text = str(line or "").strip()
        if not text:
            return False
        if text.startswith(("SCRAPE_SUMMARY ", "Done ", "ERROR ", "Imported ", "No enabled sources found.", "Stop requested")):
            return True
        status_word = text.split(" ", 1)[0]
        return status_word.isupper() and "_" in status_word

    def on_scrape_log(self, line: str) -> None:
        """Mirror scrape progress into the activity pane and busy strip."""
        text = str(line or "").strip()
        if self.should_persist_scrape_log(text):
            self.logger.info("scrape_progress %s", compact_text(text, 240))
        if self.should_record_scrape_activity(text):
            self.append_activity(text)
        self.update_last_scrape_report(text)
        self.update_scrape_progress_state(text)
        if not self.scrape_progress_timer.isActive():
            self.scrape_progress_timer.start()

    def on_scrape_done(self) -> None:
        """Handle normal scrape completion and refresh all data panes."""
        self.flush_scrape_progress()
        self.finish_task("scrape")
        self.scrape_progress_state = {}
        self.scrape_worker = None
        self.scrape_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.data_epoch += 1
        self.append_activity("Scrape finished.")
        self.mark_analysis_dirty("description")
        self.mark_analysis_dirty("analytics")
        self.mark_analysis_dirty("roadmap")
        self.reload_active_surface(force=True)

    def on_scrape_failed(self, error: str) -> None:
        """Handle scrape failure without crashing the shell."""
        self.flush_scrape_progress()
        self.finish_task("scrape")
        self.scrape_progress_state = {}
        self.scrape_worker = None
        self.scrape_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.append_activity(error)
        QMessageBox.warning(self, "Scrape Failed", compact_text(error, 2000))

    def open_selected_source_in_browser(self) -> None:
        """Open the selected public source URL in the system browser."""
        row = self.selected_source_row()
        if not row:
            QMessageBox.information(self, "Open Source URL", "Select a source row first.")
            return
        url = str(row.get("entry_url") or row.get("url") or "").strip()
        if not url:
            QMessageBox.information(self, "Open Source URL", "The selected source does not have a URL.")
            return
        opened = QDesktopServices.openUrl(QUrl(url))
        if not opened:
            QMessageBox.warning(self, "Open Source URL", f"Could not open {url}")
            return
        label = str(row.get("company") or row.get("portal") or "source")
        self.append_activity(f"Opened {label} in the system browser: {url}")

    def on_simple_task_failed(self, task_id: str, error: str) -> None:
        self.finish_task(task_id)
        if task_id == "export":
            self.cancel_export_action.setEnabled(False)
            self.export_cancel_state = None
        self.append_activity(error)
        QMessageBox.warning(self, "Task Failed", compact_text(error, 2000))

