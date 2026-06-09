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

    def request_stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        started = perf_counter()
        LOG.info("worker_start name=scrape")
        try:
            core.scrape_all(
                db_path=self.db_path,
                sources_path=self.sources_path,
                options=self.options,
                progress=self.log.emit,
                should_stop=lambda: self._stop_requested,
            )
            LOG.info("worker_finish name=scrape duration_ms=%.1f", (perf_counter() - started) * 1000.0)
            self.done.emit()
        except Exception:
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

