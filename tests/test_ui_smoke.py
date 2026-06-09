import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox
except Exception as exc:  # pragma: no cover - only used when Qt is unavailable
    pytest.skip(f"PyQt6 unavailable: {exc}", allow_module_level=True)

from jobscraper.ui import window as window_module
from jobscraper.ui.window import MainWindow


_APP: QApplication | None = None


def _app() -> QApplication:
    global _APP
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    _APP = app
    app.setQuitOnLastWindowClosed(False)
    return app


class _CapturingThreadPool:
    def __init__(self) -> None:
        self.started: list[object] = []

    def start(self, task: object) -> None:
        self.started.append(task)


def test_main_window_tabs_exclude_interview_pack(tmp_path: Path) -> None:
    _app()
    window = MainWindow(
        settings={
            "db_path": str(tmp_path / "jobs.sqlite"),
            "sources_path": str(tmp_path / "sources.json"),
            "source_watchlist_path": str(tmp_path / "source_watchlist.json"),
        }
    )
    try:
        tabs = [window.main_tabs.tabText(index) for index in range(window.main_tabs.count())]
        assert tabs == ["Workbench", "Analytics", "Topic Roadmap", "Sources"]
        assert all("Interview" not in tab for tab in tabs)
    finally:
        window.close()


def test_source_health_filter_and_scrape_summary_label(tmp_path: Path) -> None:
    _app()
    window = MainWindow(
        settings={
            "db_path": str(tmp_path / "jobs.sqlite"),
            "sources_path": str(tmp_path / "sources.json"),
            "source_watchlist_path": str(tmp_path / "source_watchlist.json"),
        }
    )
    try:
        window.current_sources = [
            {
                "id": 1,
                "company": "Healthy Co",
                "portal": "",
                "source_health_group": "healthy",
                "source_quality_score": 91,
                "enabled": True,
            },
            {
                "id": 2,
                "company": "Blocked Co",
                "portal": "",
                "source_health_group": "blocked",
                "source_quality_score": 12,
                "enabled": True,
            },
        ]
        for index in range(window.source_health_filter_combo.count()):
            if str(window.source_health_filter_combo.itemData(index) or "") == "blocked":
                window.source_health_filter_combo.setCurrentIndex(index)
                break
        window.apply_source_table_filter()

        assert [row["company"] for row in window.sources_model.rows] == ["Blocked Co"]

        window.update_last_scrape_report(
            "SCRAPE_SUMMARY jobs_before=10 jobs_after=17 new_since_last_scrape=7 matching_new=3 sources=2 fetched=9"
        )
        assert "+7 new jobs" in window.last_scrape_report_label.text()
        assert "+3 matching" in window.last_scrape_report_label.text()
    finally:
        window.close()


def test_source_tag_combo_exposes_first_class_filters(tmp_path: Path) -> None:
    _app()
    window = MainWindow(
        settings={
            "db_path": str(tmp_path / "jobs.sqlite"),
            "sources_path": str(tmp_path / "sources.json"),
            "source_watchlist_path": str(tmp_path / "source_watchlist.json"),
        }
    )
    try:
        tags = {
            str(window.source_tag_combo.itemData(index) or "")
            for index in range(window.source_tag_combo.count())
        }
        assert {"security", "systems", "india", "remote", "big-tech", "marketplace", "quant", "ai"} <= tags
    finally:
        window.close()


def test_public_sources_ui_hides_auth_session_controls(tmp_path: Path) -> None:
    _app()
    window = MainWindow(
        settings={
            "db_path": str(tmp_path / "jobs.sqlite"),
            "sources_path": str(tmp_path / "sources.json"),
            "source_watchlist_path": str(tmp_path / "source_watchlist.json"),
        }
    )
    try:
        assert not hasattr(window, "portal_sessions_table")
        assert not hasattr(window.sources_tab, "browser_combo")
        assert not hasattr(window.sources_tab, "session_actions_button")
        assert not hasattr(window.settings_panel, "upwork_token_edit")
        health_values = {
            str(window.source_health_filter_combo.itemData(index) or "")
            for index in range(window.source_health_filter_combo.count())
        }
        assert "auth needed" not in health_values
        assert "stale token" not in health_values
    finally:
        window.close()


def test_import_sources_queues_visible_preview_task(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _app()
    selected_sources = tmp_path / "sources.json"
    selected_sources.write_text("[]", encoding="utf-8")
    window = MainWindow(
        settings={
            "db_path": str(tmp_path / "jobs.sqlite"),
            "sources_path": str(selected_sources),
            "source_watchlist_path": str(tmp_path / "source_watchlist.json"),
        }
    )
    capture = _CapturingThreadPool()
    window.thread_pool = capture  # type: ignore[assignment]

    def fail_preview(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("source import preview should not run on the UI thread")

    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args, **_kwargs: (str(selected_sources), ""))
    monkeypatch.setattr(window_module.db, "preview_source_import", fail_preview)
    try:
        window.import_sources()

        assert len(capture.started) == 1
        assert getattr(capture.started[0], "key") == "preview_import_sources"
        assert "preview_import_sources" in window.active_tasks
        assert window.sources_busy.label.text() == "Previewing source import"
        assert not window.import_action.isEnabled()
    finally:
        window.close()


def test_import_sources_preview_cancel_clears_visible_task(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _app()
    selected_sources = tmp_path / "sources.json"
    selected_sources.write_text("[]", encoding="utf-8")
    window = MainWindow(
        settings={
            "db_path": str(tmp_path / "jobs.sqlite"),
            "sources_path": str(selected_sources),
            "source_watchlist_path": str(tmp_path / "source_watchlist.json"),
        }
    )
    capture = _CapturingThreadPool()
    window.thread_pool = capture  # type: ignore[assignment]
    monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.StandardButton.No)
    try:
        window.start_task("preview_import_sources", "sources", "Previewing source import", control=window.import_action)
        window.on_import_sources_preview_done(
            "preview_import_sources",
            1,
            1,
            selected_sources,
            {"path": str(selected_sources), "total": 0},
        )

        assert "preview_import_sources" not in window.active_tasks
        assert window.import_action.isEnabled()
        assert capture.started == []
    finally:
        window.close()


def test_import_sources_preview_confirmation_starts_import_task(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _app()
    selected_sources = tmp_path / "sources.json"
    selected_sources.write_text("[]", encoding="utf-8")
    window = MainWindow(
        settings={
            "db_path": str(tmp_path / "jobs.sqlite"),
            "sources_path": str(selected_sources),
            "source_watchlist_path": str(tmp_path / "source_watchlist.json"),
        }
    )
    capture = _CapturingThreadPool()
    window.thread_pool = capture  # type: ignore[assignment]
    monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes)
    try:
        window.start_task("preview_import_sources", "sources", "Previewing source import", control=window.import_action)
        window.on_import_sources_preview_done(
            "preview_import_sources",
            1,
            1,
            selected_sources,
            {"path": str(selected_sources), "total": 0},
        )

        assert "preview_import_sources" not in window.active_tasks
        assert "import_sources" in window.active_tasks
        assert len(capture.started) == 1
        assert getattr(capture.started[0], "key") == "import_sources"
        assert not window.import_action.isEnabled()
    finally:
        window.close()
