import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication
except Exception as exc:  # pragma: no cover - only used when Qt is unavailable
    pytest.skip(f"PyQt6 unavailable: {exc}", allow_module_level=True)

from jobscraper.ui import models as models_module
from jobscraper.ui import workers as workers_module
from jobscraper.ui.models import JobsTableModel, SourcesTableModel
from jobscraper.ui.window import MainWindow
from jobscraper.ui.workers import ScrapeWorker


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


def test_sources_model_uses_prepared_display_values(monkeypatch: pytest.MonkeyPatch) -> None:
    _app()
    model = SourcesTableModel()
    model.set_rows(
        [
            {
                "company": "Acme",
                "ats": "greenhouse",
                "portal": "company_boards",
                "entry_kind": "company_board",
                "open_count": 12,
                "matching_count": 7,
                "source_health_group": "healthy",
                "source_quality_score": 88,
                "failure_count": 0,
                "success_count": 4,
                "last_duration_ms": 321,
                "last_status": "ok",
                "last_error": "",
            }
        ]
    )

    def fail(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("display formatting should not run inside data()")

    monkeypatch.setattr(models_module, "compact_text", fail)

    assert model.data(model.index(0, 0), Qt.ItemDataRole.DisplayRole) == "Acme"
    assert model.data(model.index(0, 8), Qt.ItemDataRole.DisplayRole) == "ok"
    assert model.data(model.index(0, 0), Qt.ItemDataRole.FontRole) is model.data(
        model.index(0, 1), Qt.ItemDataRole.FontRole
    )


def test_jobs_model_uses_prepared_display_values(monkeypatch: pytest.MonkeyPatch) -> None:
    _app()
    model = JobsTableModel()
    model.set_rows(
        [
            {
                "id": 1,
                "row_type": "job",
                "company": "Acme",
                "title": "Senior Platform Engineer",
                "location": "Remote",
                "detected_stack": "Python, Linux",
                "source_portal": "company_board",
                "published_at": "2026-06-01 10:00:00",
            }
        ]
    )
    expected_posted = str(model.rows[0]["_display_published_at"])

    def fail(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("display formatting should not run inside data()")

    monkeypatch.setattr(models_module, "compact_text", fail)
    monkeypatch.setattr(models_module, "format_ts", fail)

    assert model.data(model.index(0, 1), Qt.ItemDataRole.DisplayRole) == "Senior Platform Engineer"
    assert model.data(model.index(0, 5), Qt.ItemDataRole.DisplayRole) == expected_posted
    assert model.data(model.index(0, 0), Qt.ItemDataRole.FontRole) is model.data(
        model.index(0, 1), Qt.ItemDataRole.FontRole
    )


def test_scrape_worker_throttles_transient_logs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    clock_values = iter([0.0, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07])
    monkeypatch.setattr(workers_module, "perf_counter", lambda: next(clock_values))

    def fake_scrape_all(*, progress, **_kwargs) -> None:
        progress("Scraping Acme (workday)...")
        progress("Workday Acme page=1/2")
        progress("Workday Acme page=2/2")
        progress("Done Acme: jobs=4")
        progress(
            "SCRAPE_SUMMARY jobs_before=0 jobs_after=4 new_since_last_scrape=4 matching_new=1 sources=1 fetched=4"
        )

    monkeypatch.setattr(workers_module.core, "scrape_all", fake_scrape_all)
    worker = ScrapeWorker(
        db_path=tmp_path / "jobs.sqlite",
        sources_path=tmp_path / "sources.json",
        options=workers_module.core.ScrapeOptions(),
    )
    logs: list[str] = []
    done: list[bool] = []
    worker.log.connect(logs.append)
    worker.done.connect(lambda: done.append(True))

    worker.run()

    assert logs == [
        "Scraping Acme (workday)...",
        "Workday Acme page=2/2",
        "Done Acme: jobs=4",
        "SCRAPE_SUMMARY jobs_before=0 jobs_after=4 new_since_last_scrape=4 matching_new=1 sources=1 fetched=4",
    ]
    assert done == [True]


def test_window_activity_history_skips_transient_scrape_updates(tmp_path: Path) -> None:
    _app()
    window = MainWindow(settings=_settings(tmp_path))
    try:
        window.scrape_progress_state = {
            "total": 1,
            "completed": 0,
            "ok": 0,
            "error": 0,
            "skipped": 0,
            "current_source": "",
            "last_line": "",
        }

        window.on_scrape_log("Scraping Example (workday)...")
        window.on_scrape_log("Workday Example page=1/2")
        window.on_scrape_log("Done Example: jobs=3")

        assert list(window.activity_history) == ["Done Example: jobs=3"]
        assert window.scrape_progress_state["current_source"] == "Example"
        assert window.scrape_progress_state["completed"] == 1
        assert window.scrape_progress_state["ok"] == 1
    finally:
        window.close()
