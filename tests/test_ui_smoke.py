import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtWidgets import QApplication, QFileDialog, QInputDialog, QMessageBox
except Exception as exc:  # pragma: no cover - only used when Qt is unavailable
    pytest.skip(f"PyQt6 unavailable: {exc}", allow_module_level=True)

from jobscraper.storage import db, fs
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


def _settings(tmp_path: Path) -> dict[str, str]:
    return {
        "db_path": str(tmp_path / "jobs.sqlite"),
        "sources_path": str(tmp_path / "sources.json"),
        "source_watchlist_path": str(tmp_path / "source_watchlist.json"),
    }


def _source_row(company: str = "Smoke Source") -> dict[str, object]:
    return {
        "company": company,
        "ats": "remotive_api",
        "url": "https://remotive.com/api/remote-jobs",
        "entry_url": "https://remotive.com/remote-jobs/software-dev",
        "enabled": True,
        "portal": "remotive",
        "entry_kind": "public_api",
        "auth_mode": "public",
        "tags": ["systems"],
        "notes": "initial",
    }


class _CapturingThreadPool:
    def __init__(self) -> None:
        self.started: list[object] = []

    def start(self, task: object) -> None:
        self.started.append(task)


def test_main_window_tabs_exclude_interview_pack(tmp_path: Path) -> None:
    _app()
    window = MainWindow(settings=_settings(tmp_path))
    try:
        tabs = [window.main_tabs.tabText(index) for index in range(window.main_tabs.count())]
        assert tabs == ["Workbench", "Analytics", "Topic Roadmap", "Sources"]
        assert all("Interview" not in tab for tab in tabs)
    finally:
        window.close()


def test_source_health_filter_and_scrape_summary_label(tmp_path: Path) -> None:
    _app()
    window = MainWindow(settings=_settings(tmp_path))
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
    window = MainWindow(settings=_settings(tmp_path))
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
    window = MainWindow(settings=_settings(tmp_path))
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
    settings = _settings(tmp_path)
    settings["sources_path"] = str(selected_sources)
    window = MainWindow(settings=settings)
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
    settings = _settings(tmp_path)
    settings["sources_path"] = str(selected_sources)
    window = MainWindow(settings=settings)
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
    settings = _settings(tmp_path)
    settings["sources_path"] = str(selected_sources)
    window = MainWindow(settings=settings)
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


def test_refresh_ai_status_queues_background_task(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _app()
    window = MainWindow(settings=_settings(tmp_path))
    capture = _CapturingThreadPool()
    window.thread_pool = capture  # type: ignore[assignment]

    def fail_ai_status(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("AI status checks should not run on the UI thread")

    monkeypatch.setattr(window_module, "load_ai_status_task", fail_ai_status)
    try:
        window.refresh_ai_status(force=True)

        assert len(capture.started) == 1
        assert getattr(capture.started[0], "key") == "ai_status"
        assert "ai_status:1" in window.active_tasks
        assert window.ai_status_label.text() == "Checking AI availability..."
        assert not window.ai_status_button.isEnabled()
    finally:
        window.close()


def test_local_ai_settings_change_queues_background_status_refresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _app()
    saved: dict[str, object] = {}
    window = MainWindow(settings=_settings(tmp_path))
    capture = _CapturingThreadPool()
    window.thread_pool = capture  # type: ignore[assignment]

    def fail_ai_status(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("AI status checks should not run on the UI thread")

    monkeypatch.setattr(window_module, "save_settings", lambda payload: saved.update(payload))
    monkeypatch.setattr(window_module, "load_ai_status_task", fail_ai_status)
    try:
        window.local_ai_url_edit.setText("http://127.0.0.1:11434")
        window.local_ai_model_edit.setText("llama3.1")
        window.on_local_ai_settings_changed()

        assert saved["local_ai_base_url"] == "http://127.0.0.1:11434"
        assert saved["local_ai_model"] == "llama3.1"
        assert os.environ["LOCAL_AI_BASE_URL"] == "http://127.0.0.1:11434"
        assert os.environ["LOCAL_AI_MODEL"] == "llama3.1"
        assert len(capture.started) == 1
        assert getattr(capture.started[0], "key") == "ai_status"
    finally:
        os.environ.pop("LOCAL_AI_BASE_URL", None)
        os.environ.pop("LOCAL_AI_MODEL", None)
        window.close()


def test_filter_presets_save_load_delete_and_missing_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _app()
    saved: dict[str, object] = {}
    monkeypatch.setattr(window_module, "save_settings", lambda payload: saved.update(payload))
    monkeypatch.setattr(QInputDialog, "getText", lambda *_args, **_kwargs: ("Infra", True))
    window = MainWindow(settings=_settings(tmp_path))
    try:
        window.current_sources = [
            {"id": 5, "company": "HN Hiring", "portal": "hackernews", "ats": "hackernews_hiring", "open_count": 3, "matching_count": 2}
        ]
        window.company_model.set_rows(
            [
                {"company": "Acme", "open_count": 2, "matching_count": 2},
                {"company": "Other", "open_count": 1, "matching_count": 0},
            ]
        )
        window.company_model.set_checked_companies(["Acme"])
        window.refresh_portal_filter_options()
        for index in range(window.portal_filter_combo.count()):
            if str(window.portal_filter_combo.itemData(index) or "") == "hackernews":
                window.portal_filter_combo.setCurrentIndex(index)
                break
        window.refresh_source_filter_options()
        for index in range(window.source_filter_combo.count()):
            if int(window.source_filter_combo.itemData(index) or 0) == 5:
                window.source_filter_combo.setCurrentIndex(index)
                break
        window.refresh_hn_review_state()
        window._set_combo_data(window.hn_review_combo, "parsed")
        window._set_combo_data(window.source_tag_combo, "systems")
        window.stack_filter_combo.addItem("Rust", "Rust")
        window._set_combo_data(window.stack_filter_combo, "Rust")
        window.search_edit.setText("kernel")
        window.matching_only_checkbox.setChecked(False)
        window.open_only_checkbox.setChecked(True)
        window.group_by_company_checkbox.setChecked(True)
        window.founding_only_checkbox.setChecked(True)

        window.save_current_filter_preset()
        assert "Infra" in window.settings["filter_presets"]
        assert "Infra" in saved["filter_presets"]

        window.search_edit.clear()
        window.matching_only_checkbox.setChecked(True)
        window.group_by_company_checkbox.setChecked(False)
        window.company_model.set_checked_companies(["Other"])
        window.load_selected_filter_preset()
        assert window.search_edit.text() == "kernel"
        assert not window.matching_only_checkbox.isChecked()
        assert window.group_by_company_checkbox.isChecked()
        assert window.company_model.checked_companies() == ["Acme"]
        assert int(window.source_filter_combo.currentData() or 0) == 5

        missing = dict(window.settings["filter_presets"]["Infra"])
        missing["source_id"] = 99999
        window.settings["filter_presets"]["Missing"] = missing
        window.refresh_filter_preset_options(selected="Missing")
        window.load_selected_filter_preset()
        assert int(window.source_filter_combo.currentData() or 0) == 0

        window.refresh_filter_preset_options(selected="Infra")
        window.delete_current_filter_preset()
        assert "Infra" not in window.settings["filter_presets"]
    finally:
        window.close()


def test_source_edit_validates_backs_up_and_imports(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _app()
    monkeypatch.setattr(window_module.paths, "backups_dir", lambda workspace_root=None: tmp_path / "backups")
    sources_path = tmp_path / "sources.json"
    sources_path.write_text(json.dumps([_source_row()]), encoding="utf-8")
    settings = _settings(tmp_path)
    settings["sources_path"] = str(sources_path)
    db_path = Path(settings["db_path"])
    db.import_sources_report(db_path, sources_path, create_backup=False)
    selected = db.list_sources(db_path)[0]
    window = MainWindow(settings=settings)
    try:
        before = sources_path.read_text(encoding="utf-8")
        with pytest.raises(ValueError, match="Unsupported ATS"):
            window.save_source_config_edit(selected, {"ats": "not_real"})
        assert sources_path.read_text(encoding="utf-8") == before
        assert not list((tmp_path / "backups").glob("*.json"))

        result = window.save_source_config_edit(
            selected,
            {
                "enabled": False,
                "ats": "remotive_api",
                "url": "https://remotive.com/api/remote-jobs?category=software-dev",
                "entry_url": "https://remotive.com/remote-jobs/software-dev",
                "tags": ["systems", "remote"],
                "notes": "edited",
            },
        )
        assert Path(result["backup_path"]).exists()
        rows = json.loads(sources_path.read_text(encoding="utf-8"))
        assert rows[0]["enabled"] is False
        assert rows[0]["tags"] == ["systems", "remote"]
        db_rows = db.list_sources(db_path)
        assert any(row["notes"] == "edited" for row in db_rows)
    finally:
        window.close()


def test_queue_source_edit_uses_background_task(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _app()
    sources_path = tmp_path / "sources.json"
    sources_path.write_text(json.dumps([_source_row()]), encoding="utf-8")
    settings = _settings(tmp_path)
    settings["sources_path"] = str(sources_path)
    db_path = Path(settings["db_path"])
    db.import_sources_report(db_path, sources_path, create_backup=False)
    selected = db.list_sources(db_path)[0]
    window = MainWindow(settings=settings)
    capture = _CapturingThreadPool()
    window.thread_pool = capture  # type: ignore[assignment]

    def fail_source_edit(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("source edit apply should not run on the UI thread")

    monkeypatch.setattr(window_module, "save_source_config_edit_task", fail_source_edit)
    try:
        window.queue_source_config_edit(
            selected,
            {
                "enabled": False,
                "ats": "remotive_api",
                "url": "https://remotive.com/api/remote-jobs?category=software-dev",
                "entry_url": "https://remotive.com/remote-jobs/software-dev",
                "tags": ["systems", "remote"],
                "notes": "edited",
            },
        )

        assert len(capture.started) == 1
        assert getattr(capture.started[0], "key") == "source_edit"
        assert any(task_id.startswith("source_edit:") for task_id in window.active_tasks)
        assert window.sources_busy.label.text() == "Saving source edit"
        assert not window.source_edit_button.isEnabled()
    finally:
        window.close()


def test_storage_manager_categories_delete_generated_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _app()
    monkeypatch.setattr(window_module.paths, "logs_dir", lambda workspace_root=None: tmp_path / "logs")
    monkeypatch.setattr(window_module.paths, "backups_dir", lambda workspace_root=None: tmp_path / "backups")
    monkeypatch.setattr(window_module.paths, "reports_dir", lambda workspace_root=None: tmp_path / "reports")
    monkeypatch.setattr(window_module.paths, "exports_dir", lambda workspace_root=None: tmp_path / "exports")
    monkeypatch.setattr(window_module.paths, "settings_path", lambda workspace_root=None: tmp_path / "settings.json")
    monkeypatch.setattr(window_module.paths, "default_workspace_root", lambda: tmp_path)
    for name in ("logs", "backups", "reports", "exports", "cache"):
        folder = fs.ensure_dir(tmp_path / name)
        (folder / f"{name}.txt").write_text("generated", encoding="utf-8")
    db_path = tmp_path / "jobs.sqlite"
    db_path.write_text("active", encoding="utf-8")
    settings = _settings(tmp_path)
    settings["db_path"] = str(db_path)
    window = MainWindow(settings=settings)
    try:
        rows = {row["key"]: row for row in window.storage_category_rows()}
        assert rows["db"]["deletable"] is False
        assert rows["logs"]["size"] > 0
        assert window.delete_storage_category("db") is False
        assert db_path.exists()
        assert window.delete_storage_category("logs") is True
        assert window._path_size(tmp_path / "logs") == 0
    finally:
        window.close()


def test_storage_manager_scan_queues_background_task(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _app()
    window = MainWindow(settings=_settings(tmp_path))
    capture = _CapturingThreadPool()
    window.thread_pool = capture  # type: ignore[assignment]

    def fail_storage_scan(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("storage scans should not run on the UI thread")

    monkeypatch.setattr(window_module, "load_storage_category_rows_task", fail_storage_scan)
    dialog = window._build_storage_manager_dialog()
    try:
        window.refresh_storage_manager(dialog, force=True)

        assert len(capture.started) == 1
        assert getattr(capture.started[0], "key") == "storage_scan"
        assert any(task_id.startswith("storage_scan:") for task_id in window.active_tasks)
        size_labels = getattr(dialog, "_storage_size_labels")
        assert size_labels["db"].text() == "Scanning..."
    finally:
        dialog.close()
        window.close()


def test_first_run_tutorial_state_is_persisted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _app()
    saved: dict[str, object] = {}
    monkeypatch.setattr(window_module, "save_settings", lambda payload: saved.update(payload))
    window = MainWindow(settings=_settings(tmp_path))
    calls: list[dict[str, object]] = []
    try:
        window.show_startup_tutorial = lambda *_args, **kwargs: calls.append(kwargs)  # type: ignore[method-assign]
        window.maybe_show_startup_tutorial()
        assert calls == [{"mark_dismissed": True}]
        window.mark_startup_tutorial_dismissed()
        assert window.settings["first_run_tutorial_dismissed"] is True
        assert saved["first_run_tutorial_dismissed"] is True
    finally:
        window.close()


def test_export_ui_runs_in_background_and_can_request_cancel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _app()
    out_path = tmp_path / "exports" / "jobs.json"
    window = MainWindow(settings=_settings(tmp_path))
    capture = _CapturingThreadPool()
    window.thread_pool = capture  # type: ignore[assignment]
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_args, **_kwargs: (str(out_path), ""))
    try:
        window.export_filtered_json()
        assert len(capture.started) == 1
        assert getattr(capture.started[0], "key") == "export"
        assert window.cancel_export_action.isEnabled()
        assert str(out_path) in window.jobs_summary_label.text()
        window.cancel_export()
        assert window.export_cancel_state is not None
        assert window.export_cancel_state["cancelled"] is True
        assert not window.cancel_export_action.isEnabled()
        assert window.active_tasks["export"]["label"] == "Cancelling export"
    finally:
        window.close()


def test_settings_paths_validate_and_reports_path_is_visible(tmp_path: Path) -> None:
    _app()
    window = MainWindow(settings=_settings(tmp_path))
    try:
        assert window.reports_path_edit.text()
        errors = MainWindow.validate_runtime_paths(
            tmp_path / "jobs.sqlite",
            tmp_path / "sources.txt",
            tmp_path / "source_watchlist.json",
        )
        assert any("Sources path must be a .json file" in error for error in errors)
        errors = MainWindow.validate_runtime_paths(
            tmp_path / "jobs.sqlite",
            tmp_path / "sources.json",
            tmp_path / "source_watchlist.json",
        )
        assert errors == []
    finally:
        window.close()


def test_dense_layout_supported_sizes_and_scaled_fonts(tmp_path: Path) -> None:
    app = _app()
    window = MainWindow(settings=_settings(tmp_path / "layout"))
    window.thread_pool = _CapturingThreadPool()  # type: ignore[assignment]
    try:
        for width, height in ((1440, 900), (1920, 1080), (2560, 1440), (3440, 1440)):
            window.resize(width, height)
            window.ensurePolished()
            tab_bar = window.main_tabs.tabBar()
            for index in range(window.main_tabs.count()):
                text = window.main_tabs.tabText(index)
                text_width = tab_bar.fontMetrics().horizontalAdvance(text)
                for scale in (1.0, 1.25, 1.5):
                    assert tab_bar.tabRect(index).width() >= int(text_width * scale) + 12
            for widget in (
                window.scrape_button,
                window.tools_button,
                window.company_table,
                window.jobs_table,
                window.description_browser,
                window.main_tabs,
            ):
                assert not widget.isHidden()
                assert widget.rect().width() > 0
                assert widget.rect().height() > 0
    finally:
        window.close()
