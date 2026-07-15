#!/usr/bin/env python3
"""View configuration and help text for the desktop workbench shell."""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QAbstractItemView, QHeaderView

from .models import CompanyFilterModel, JobsTableModel, SourcesTableModel
from .theme import ROW_HEIGHT


def configure_table_views(window: Any) -> None:
    """Attach models and tune dense desktop table behavior for the workbench.

    The main window keeps ownership of the models and selection flow. This
    helper only applies the static view configuration that does not need direct
    access to the request graph.
    """
    window.sources_model = SourcesTableModel()
    window.sources_table.setModel(window.sources_model)
    window.sources_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    window.sources_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    window.sources_table.setWordWrap(False)
    window.sources_table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    window.sources_table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    window.sources_table.verticalHeader().setVisible(False)
    window.sources_table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
    window.sources_table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
    window.sources_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    for col, width in ((1, 90), (2, 90), (3, 90), (4, 90), (5, 70), (6, 80), (7, 110), (8, 64), (9, 92), (10, 56), (11, 56), (12, 95), (13, 260)):
        window.sources_table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        window.sources_table.setColumnWidth(col, width)

    window.company_model = CompanyFilterModel()
    window.company_table.setModel(window.company_model)
    window.company_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    window.company_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    window.company_table.setWordWrap(False)
    window.company_table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    window.company_table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    window.company_table.verticalHeader().setVisible(False)
    window.company_table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
    window.company_table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
    window.company_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
    window.company_table.setColumnWidth(0, 34)
    window.company_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
    window.company_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
    window.company_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
    window.company_table.setColumnWidth(2, 70)
    window.company_table.setColumnWidth(3, 80)

    window.jobs_model = JobsTableModel()
    window.jobs_table.setModel(window.jobs_model)
    window.jobs_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    window.jobs_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    window.jobs_table.setWordWrap(False)
    window.jobs_table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    window.jobs_table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    window.jobs_table.setTextElideMode(Qt.TextElideMode.ElideRight)
    window.jobs_table.verticalHeader().setVisible(False)
    window.jobs_table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
    window.jobs_table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
    window.jobs_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
    window.jobs_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
    window.jobs_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
    window.jobs_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
    window.jobs_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
    window.jobs_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
    for col, width in ((0, 130), (2, 240), (3, 180), (4, 110), (5, 130)):
        window.jobs_table.setColumnWidth(col, width)


def apply_tooltips(window: Any) -> None:
    """Attach extensive help text to the workbench controls and panes.

    Tooltips are intentionally centralized here so help coverage can evolve
    without pushing more presentation-only code into the request-graph shell.
    """
    window.tools_button.setToolTip("Low-frequency workspace actions. Open this menu for source import, filtered export, and shell-level visibility toggles instead of keeping every administrative action visible all the time.")
    window.import_action.setToolTip("Import or refresh source rows from the selected JSON file into SQLite. Use this after editing the workspace sources.json file or adding new source adapters.")
    window.cancel_export_action.setToolTip("Request cancellation for the active JSON export. Completed exports are already written atomically; in-progress cancellation removes the temporary file.")
    window.scrape_button.setToolTip("Run the scraper against enabled sources and update jobs, matches, analytics, and cache state. This is the action that actually refreshes job data.")
    window.stop_button.setToolTip("Request a clean stop for the currently running scrape worker. The current source may finish its in-flight request before the worker exits.")
    window.reload_button.setToolTip("Reload visible data from SQLite without scraping. Use this after cache generation, source import, or any change that already wrote to the database.")
    window.export_action.setToolTip("Export the current filtered jobs view to JSON. The export respects the active company, source family, source row, Hacker News review mode, stack, and search filters.")
    window.activity_action.setToolTip("Open the activity dialog. The log is useful for scrape progress, blocked sources, and background worker failures.")
    window.storage_action.setToolTip("Open storage usage and cleanup for generated logs, reports, backups, exports, and caches. Active DB and config files are visible but protected.")
    window.tutorial_action.setToolTip("Reopen the startup guide for source import, scraping, filtering, export, source health, and storage cleanup.")
    window.open_logs_action.setToolTip("Open the production log folder. Logs are rotated under Documents\\JobScraper\\logs.")
    window.open_backups_action.setToolTip("Open the SQLite backup folder. Source imports create a backup before mutating the database.")
    window.open_reports_action.setToolTip("Open generated reports, including source watchlist probe reports and manual source-review outputs.")
    window.open_watchlist_action.setToolTip("Open the candidate direct-source watchlist used by source-discovery probes. Verified rows can be promoted into sources.json after validation.")
    window.copy_diagnostics_action.setToolTip("Copy a compact diagnostics summary with local paths and current app state. It does not include secrets.")
    window.settings_action.setToolTip("Open the settings dialog. It contains DB/source paths, matching terms, scrape concurrency, Hacker News parser mode, Local AI settings, and the current AI availability summary.")
    window.command_summary.setToolTip("Compact state bar for the current workspace context. It shows the active database and source file plus the current matching profile, exclusions, concurrency, and Hacker News parser mode.")
    window.global_busy.setToolTip("Global background-task strip. It shows the most relevant in-flight action and whether it has determinate progress or just an indeterminate busy state.")

    window.db_path_edit.setToolTip("Path to the SQLite database used by the workbench. Change this only if you want the UI to point at a different jobs database.")
    window.sources_path_edit.setToolTip("Path to the JSON file used when importing source definitions. This does not scrape by itself; it only updates the configured source list in SQLite.")
    window.watchlist_path_edit.setToolTip("Path to the candidate source watchlist for probing direct ATS/company sources before adding them to the active source JSON.")
    window.log_path_edit.setToolTip("Read-only path to the current rotating application log file.")
    window.reports_path_edit.setToolTip("Read-only path to generated reports, including source discovery reports and benchmark outputs.")
    window.remote_checkbox.setToolTip("Include remote roles in scrape matching and the current desktop view. Turn this off if you want to narrow the matching profile to office or hybrid roles only.")
    window.india_checkbox.setToolTip("Include India office or hybrid roles in scrape matching and the current desktop view. This is mainly for Bangalore and similar location-targeted roles.")
    window.interests_edit.setToolTip("Comma-separated interest terms used by the scraper to tag and match relevant jobs. These terms influence which jobs count as interesting, not just the visible filter.")
    window.exclude_edit.setToolTip("Comma-separated exclusion terms that remove jobs from the matching set. Use this for terms like visa, relocation, or role families you do not want in the final shortlist.")
    window.concurrency_spin.setToolTip("Maximum concurrent source and detail fetches used during scraping. Higher values can speed up scraping, but they also increase load and the chance of rate-limit or browser contention.")
    window.http_concurrency_spin.setToolTip("Global HTTP connection cap for the shared scraper session. This bounds total network fan-out across all active sources.")
    window.hn_parser_combo.setToolTip("Choose how Hacker News hiring comments should be normalized. Auto keeps heuristics as the base path and upgrades weak parses through Local AI or OpenAI when available.")
    window.local_ai_url_edit.setToolTip("Base URL for the optional local AI endpoint used by Hacker News parsing when that parser mode is selected. Typical values are http://127.0.0.1:11434 for Ollama or an OpenAI-compatible /v1 endpoint.")
    window.local_ai_model_edit.setToolTip("Exact local model name to use for optional Local AI parsing, for example llama3.1:8b. Leave it blank to let the app use the endpoint's default or first available model.")
    window.ai_status_label.setToolTip("Current AI availability summary. OpenAI is reported as configured or not configured; Local AI is checked against the configured endpoint in the background.")
    window.ai_status_button.setToolTip("Run a background AI availability check now. This verifies Local AI reachability and refreshes the visible AI status line without blocking the UI.")

    window.company_panel.setToolTip("Company-first narrowing pane. Use it when you want to compare a small employer set without losing the current global filters and cached analysis state.")
    window.company_busy.setToolTip("Pane-level loading state for company counts and company-list refresh. This updates independently so the sidebar can stay responsive.")
    window.company_selection_button.setToolTip("Bulk selection actions for the company sidebar. Use this menu to select everything, clear everything, or keep only employers from the currently selected jobs.")
    window.company_all_action.setToolTip("Check every company in the sidebar so all current companies stay in the jobs view.")
    window.company_none_action.setToolTip("Uncheck every company in the sidebar and clear the company filter selection.")
    window.company_selected_action.setToolTip("Limit the company sidebar to the companies represented by the currently selected jobs rows.")
    window.company_table.setToolTip("Click a company row to focus the workbench on that company only. Use the checkbox column when you want to build a multi-company view instead of an exclusive single-company filter.")

    window.jobs_panel.setToolTip("Primary jobs result pane for the current filter state. This area is optimized for dense scanning, not oversized presentation.")
    window.jobs_busy.setToolTip("Pane-level loading state for jobs, exports, and scrape-driven result refreshes.")
    window.matching_only_checkbox.setToolTip("Show only jobs that pass the current interest and location matching rules. Turn this off if you want to inspect the broader raw scrape output.")
    window.open_only_checkbox.setToolTip("Hide closed jobs and show only currently open roles. Keep this on for normal job hunting unless you are auditing scrape history.")
    window.group_by_company_checkbox.setToolTip("Insert company header rows so the jobs table is grouped by company. This is useful when scanning several companies at once instead of one long flat list.")
    window.founding_only_checkbox.setToolTip("Filter to jobs tagged with the built-in founding-engineer group. Use this when you want startup-first or early-engineering roles only.")
    window.portal_filter_combo.setToolTip("Filter jobs by source family, such as company boards, Hacker News, Wellfound, public APIs, or another public source family.")
    window.source_filter_combo.setToolTip("Filter jobs by one configured source row inside the current source family. This is the explicit source-centric view, distinct from free-text search and distinct from employer name grouping.")
    window.source_tag_combo.setToolTip("Filter jobs by first-class source tags such as security, systems, India, remote, big-tech, marketplace, quant, or AI.")
    window.stack_filter_combo.setToolTip("Filter jobs by detected language, domain, framework, tool, or group tag. This is the quickest way to isolate roles mentioning systems, Rust, networking, storage, and similar topics.")
    window.hn_review_combo.setToolTip("When the current scope is Hacker News, switch between all HN jobs, parsed-company rows, and the fallback bucket for comments that still need manual review.")
    window.search_edit.setToolTip("Search across title, company, location, and description text. This is a live filter, so broad queries will immediately reduce the jobs list and downstream analytics.")
    window.filter_preset_combo.setToolTip("Load a saved filter preset. Presets include search, stack, company selection, source family, source row, Hacker News mode, open/matching/founding flags, and grouping.")
    window.save_filter_preset_button.setToolTip("Save the current visible job filters as a named preset in workspace settings.")
    window.delete_filter_preset_button.setToolTip("Delete the selected filter preset from workspace settings.")
    window.jobs_summary_label.setToolTip("Live summary of the current jobs view. Use it to verify whether the visible result set is constrained by companies, portal source, stack tag, search text, or grouping.")
    window.jobs_table.setToolTip("Select one or more job rows to drive the Workbench description pane, analytics, and roadmap tabs. The first selected real job supplies selected-job detail and selected-scope context when group headers are present.")

    window.description_panel.setToolTip("Selected-job description pane for the active Workbench row. This stays on Workbench because it is true row detail rather than aggregate or study output.")
    window.description_header.setToolTip("Compact metadata header for the current primary job selection. It stays visible above the description body so you can keep row context while scanning details.")
    window.description_busy.setToolTip("Pane-level loading state for selected-job description and metadata.")
    window.description_browser.setToolTip("Rendered selected-job description, normalized metadata, and links from SQLite.")
    window.analytics_busy.setToolTip("Pane-level loading state for analytics over the current jobs scope.")
    window.analytics_browser.setToolTip("Aggregate analytics for the current jobs scope. Use this to detect repeated technologies, domains, locations, and source patterns before drilling into one role.")
    window.roadmap_scope_combo.setToolTip("Choose whether the roadmap is built from selected jobs, current filters, or the whole database.")
    window.roadmap_refresh_button.setToolTip("Rebuild the topic roadmap for the currently selected roadmap scope.")
    window.roadmap_busy.setToolTip("Pane-level loading state for roadmap synthesis.")
    window.roadmap_summary_label.setToolTip("Compact roadmap summary showing scope, job count, and dominant topics.")
    window.roadmap_browser.setToolTip("Rendered study roadmap from fundamentals through deeper topics for the selected jobs scope.")

    window.sources_busy.setToolTip("Pane-level loading state for source import, source list refresh, and watchlist probing.")
    window.source_health_filter_combo.setToolTip("Filter raw sources by the action-oriented health group. Use this to isolate disabled, blocked, parser-failure, healthy, or newly added rows.")
    window.sources_label.setToolTip("Source diagnostics header. It summarizes the selected raw source row and keeps source-centric navigation anchored on the current source choice.")
    window.last_scrape_report_label.setToolTip("Most recent scrape summary from this UI session, including new jobs added and matching jobs found. The same SCRAPE_SUMMARY line is also written to the activity log.")
    window.source_probe_button.setToolTip("Probe the candidate source watchlist against public ATS APIs. Only rows with a valid job id, title, URL, and location are appended to sources.json and imported.")
    window.source_focus_button.setToolTip("Project the selected raw source row into the Workbench filters and switch tabs. Use this when you want to browse everything from one configured source, including Hacker News as one source row.")
    window.source_edit_button.setToolTip("Edit enabled state, URL, source type, tags, and notes for the selected source row. The source JSON is backed up before saving.")
    window.sources_table.setToolTip("Raw source diagnostics. Use this table for per-source scrape status, source metadata, and blocked-source detail. Double-click a row or use Focus In Workbench to browse jobs from that source.")
    window.activity_group.setToolTip("Collapsible runtime log. Leave it hidden while scanning jobs and open it when you need scrape traces, blocked-source details, or worker failures.")
    window.activity_log.setToolTip("Raw activity log from scraping, browser fallbacks, imports, exports, and background tasks. Use this first when something looks stale or blocked.")
    window.main_tabs.setTabToolTip(0, "Primary browsing workstation: companies, jobs, and selected-job description.")
    window.main_tabs.setTabToolTip(1, "Aggregate analytics for the current jobs view.")
    window.main_tabs.setTabToolTip(2, "Topic roadmap and study planning derived from the current jobs scope.")
    window.main_tabs.setTabToolTip(3, "Source administration: scrape source status, public source discovery, and adapter visibility.")
