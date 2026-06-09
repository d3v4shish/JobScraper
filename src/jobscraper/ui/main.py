#!/usr/bin/env python3
"""Application entry point for the JobScraper desktop workbench."""

from __future__ import annotations

import sys
import logging

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from .. import paths
from ..bootstrap import load_settings
from .window import MainWindow

LOGGER = logging.getLogger(__name__)


def _log_uncaught_exception(exc_type: type[BaseException], exc: BaseException, tb: object) -> None:
    """Persist unexpected GUI/runtime crashes to the rotating app log."""
    LOGGER.critical("uncaught_exception", exc_info=(exc_type, exc, tb))
    sys.__excepthook__(exc_type, exc, tb)


def main(_argv: list[str] | None = None) -> int:
    """Start the desktop workbench and return the Qt event-loop exit code."""
    sys.excepthook = _log_uncaught_exception
    argv = list(sys.argv[1:] if _argv is None else _argv)
    smoke_test = "--smoke-test" in argv
    qt_argv = [sys.argv[0], *(arg for arg in argv if arg != "--smoke-test")]
    app = QApplication(qt_argv)
    app.setWindowIcon(QIcon(str(paths.app_icon_path())))
    window = MainWindow(settings=load_settings())
    window.show()
    if smoke_test:
        QTimer.singleShot(250, app.quit)
    return app.exec()
