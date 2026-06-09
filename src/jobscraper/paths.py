"""Workspace and packaged-resource path helpers."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .storage import fs

try:
    from PyQt6.QtCore import QStandardPaths
except Exception:  # pragma: no cover - PyQt is expected at runtime
    QStandardPaths = None  # type: ignore[assignment]


APP_NAME = "JobScraper"


def package_root() -> Path:
    """Return the installed package root for the canonical jobscraper package."""
    return Path(__file__).resolve().parent


def project_root() -> Path:
    """Return the top-level project root in source mode."""
    return package_root().parent.parent


def _qt_documents_dir() -> Path | None:
    if QStandardPaths is None:
        return None
    locations = QStandardPaths.standardLocations(QStandardPaths.StandardLocation.DocumentsLocation)
    if not locations:
        return None
    return Path(locations[0])


def documents_dir() -> Path:
    """Resolve the user's Documents folder with a filesystem fallback."""
    qt_path = _qt_documents_dir()
    if qt_path:
        return qt_path
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    return home / "Documents"


def default_workspace_root() -> Path:
    """Return the default mutable workspace root under Documents."""
    return documents_dir() / APP_NAME


def config_dir(*, workspace_root: Path | None = None) -> Path:
    return (workspace_root or default_workspace_root()) / "config"


def data_dir(*, workspace_root: Path | None = None) -> Path:
    return (workspace_root or default_workspace_root()) / "data"


def logs_dir(*, workspace_root: Path | None = None) -> Path:
    return (workspace_root or default_workspace_root()) / "logs"


def backups_dir(*, workspace_root: Path | None = None) -> Path:
    return (workspace_root or default_workspace_root()) / "backups"


def exports_dir(*, workspace_root: Path | None = None) -> Path:
    return (workspace_root or default_workspace_root()) / "exports"


def reports_dir(*, workspace_root: Path | None = None) -> Path:
    return (workspace_root or default_workspace_root()) / "reports"


def packaged_assets_dir() -> Path:
    """Return the packaged immutable assets directory in source or frozen mode."""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
        return base / "jobscraper" / "assets"
    return package_root() / "assets"


def settings_path(*, workspace_root: Path | None = None) -> Path:
    return config_dir(workspace_root=workspace_root) / "settings.json"


def log_path(*, workspace_root: Path | None = None) -> Path:
    return logs_dir(workspace_root=workspace_root) / "jobscraper.log"


def app_icon_path() -> Path:
    return packaged_assets_dir() / "app_icon.png"


def app_icon_ico_path() -> Path:
    return packaged_assets_dir() / "app_icon.ico"


def app_icon_light_path() -> Path:
    return packaged_assets_dir() / "app_icon_light.png"


def app_icon_light_ico_path() -> Path:
    return packaged_assets_dir() / "app_icon_light.ico"


def default_db_path(*, workspace_root: Path | None = None) -> Path:
    return data_dir(workspace_root=workspace_root) / "jobs.sqlite"


def default_sources_path(*, workspace_root: Path | None = None) -> Path:
    return config_dir(workspace_root=workspace_root) / "sources.json"


def default_source_watchlist_path(*, workspace_root: Path | None = None) -> Path:
    return config_dir(workspace_root=workspace_root) / "source_watchlist.json"


def bundled_sources_path() -> Path:
    """Return the shipped default sources file in source or frozen mode."""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
        return base / "jobscraper" / "resources" / "company_sources.json"
    return package_root() / "resources" / "company_sources.json"


def bundled_source_watchlist_path() -> Path:
    """Return the shipped candidate-source watchlist in source or frozen mode."""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
        return base / "jobscraper" / "resources" / "source_watchlist.json"
    return package_root() / "resources" / "source_watchlist.json"


def ensure_workspace_dirs(*, workspace_root: Path | None = None) -> None:
    """Create the default workspace directory structure."""
    root = workspace_root or default_workspace_root()
    for path in (
        config_dir(workspace_root=root),
        data_dir(workspace_root=root),
        logs_dir(workspace_root=root),
        backups_dir(workspace_root=root),
        exports_dir(workspace_root=root),
        reports_dir(workspace_root=root),
    ):
        fs.ensure_dir(path)
