#!/usr/bin/env python3
"""Long-running worker threads and pool tasks for the desktop workbench."""

from __future__ import annotations

import logging
import traceback
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

from PyQt6.QtCore import QObject, QRunnable, QThread, pyqtSignal, pyqtSlot

from ..scraping import core


LOG = logging.getLogger(__name__)


class ScrapeWorker(QThread):
    """Run the full scrape pipeline on a dedicated worker thread."""
    log = pyqtSignal(str)
    done = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, *, db_path: Path, sources_path: Path, options: core.ScrapeOptions) -> None:
        super().__init__()
        self.db_path = db_path
        self.sources_path = sources_path
        self.options = options
        self._stop_requested = False
        self._last_progress_emit_at = 0.0
        self._pending_progress_line = ""
        self._transient_progress_interval_s = 0.2

    def request_stop(self) -> None:
        self._stop_requested = True

    @staticmethod
    def _is_significant_progress(line: str) -> bool:
        text = str(line or "").strip()
        if not text:
            return False
        if text.startswith(
            (
                "Scraping ",
                "Done ",
                "ERROR ",
                "SCRAPE_SUMMARY ",
                "Imported ",
                "No enabled sources found.",
                "Stop requested",
            )
        ):
            return True
        status_word = text.split(" ", 1)[0]
        return status_word.isupper() and "_" in status_word

    def _emit_progress_line(self, line: str) -> None:
        self.log.emit(line)
        self._last_progress_emit_at = perf_counter()

    def _flush_pending_progress(self) -> None:
        text = str(self._pending_progress_line or "").strip()
        if not text:
            return
        self._pending_progress_line = ""
        self._emit_progress_line(text)

    def _handle_progress(self, line: str) -> None:
        text = str(line or "").strip()
        if not text:
            return
        if self._is_significant_progress(text):
            self._flush_pending_progress()
            self._emit_progress_line(text)
            return
        self._pending_progress_line = text
        if (perf_counter() - self._last_progress_emit_at) >= self._transient_progress_interval_s:
            self._flush_pending_progress()

    def run(self) -> None:
        started = perf_counter()
        LOG.info("worker_start name=scrape")
        try:
            core.scrape_all(
                db_path=self.db_path,
                sources_path=self.sources_path,
                options=self.options,
                progress=self._handle_progress,
                should_stop=lambda: self._stop_requested,
            )
            self._flush_pending_progress()
            LOG.info("worker_finish name=scrape duration_ms=%.1f", (perf_counter() - started) * 1000.0)
            self.done.emit()
        except Exception:
            self._flush_pending_progress()
            self.failed.emit(traceback.format_exc())


class TaskSignals(QObject):
    """Signals emitted by short-lived background request tasks."""
    finished = pyqtSignal(str, int, object)
    failed = pyqtSignal(str, int, str)


class BackgroundTask(QRunnable):
    """Execute one request-graph task in the shared worker pool."""
    def __init__(self, *, key: str, token: int, fn: Callable[[], Any]) -> None:
        super().__init__()
        self.key = key
        self.token = token
        self.fn = fn
        self.signals = TaskSignals()
        self.setAutoDelete(True)

    @pyqtSlot()
    def run(self) -> None:
        started = perf_counter()
        LOG.info("worker_start name=background_task key=%s token=%s", self.key, self.token)
        try:
            result = self.fn()
            LOG.info("worker_finish name=background_task key=%s token=%s duration_ms=%.1f", self.key, self.token, (perf_counter() - started) * 1000.0)
            self.signals.finished.emit(self.key, self.token, result)
        except Exception:
            self.signals.failed.emit(self.key, self.token, traceback.format_exc())

