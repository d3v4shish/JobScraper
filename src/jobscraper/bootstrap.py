"""Runtime bootstrap for workspace setup and logging."""

from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
import os
import sys
from pathlib import Path
from typing import Any, Dict

from . import paths
from .storage import fs


SETTINGS_VERSION = 1
_INITIALIZED = False


def default_settings_payload() -> Dict[str, Any]:
    """Return the default persisted settings payload."""
    workspace_root = paths.default_workspace_root()
    return {
        "version": SETTINGS_VERSION,
        "workspace_root": str(workspace_root),
        "db_path": str(paths.default_db_path(workspace_root=workspace_root)),
        "sources_path": str(paths.default_sources_path(workspace_root=workspace_root)),
        "source_watchlist_path": str(paths.default_source_watchlist_path(workspace_root=workspace_root)),
        "http_concurrency": 32,
        "local_ai_base_url": "",
        "local_ai_model": "",
        "first_run_tutorial_dismissed": False,
    }


def load_settings() -> Dict[str, Any]:
    """Load persisted settings or return the default payload."""
    path = paths.settings_path()
    if not path.exists():
        return default_settings_payload()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default_settings_payload()
    defaults = default_settings_payload()
    defaults.update({key: value for key, value in payload.items() if key != "migration"})
    return defaults


def save_settings(payload: Dict[str, Any]) -> None:
    """Persist the settings payload into the workspace config directory."""
    fs.atomic_write_json(paths.settings_path(), payload)


def update_settings(**updates: Any) -> Dict[str, Any]:
    """Apply one settings update and return the persisted payload."""
    payload = load_settings()
    for key, value in updates.items():
        payload[key] = value
    save_settings(payload)
    return payload


def ensure_workspace_files(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Create the workspace directory layout and bootstrap the default sources file."""
    paths.ensure_workspace_dirs()
    sources_path = Path(settings["sources_path"])
    if not sources_path.exists():
        bundled = paths.bundled_sources_path()
        fs.copy_file(bundled, sources_path)
    watchlist_path = Path(settings.get("source_watchlist_path") or paths.default_source_watchlist_path())
    if not watchlist_path.exists():
        bundled_watchlist = paths.bundled_source_watchlist_path()
        if bundled_watchlist.exists():
            fs.copy_file(bundled_watchlist, watchlist_path)
    return settings


def configure_logging() -> None:
    """Configure file and stderr logging for the production workspace."""
    log_path = paths.log_path()
    fs.ensure_dir(log_path.parent)
    root_logger = logging.getLogger()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    root_logger.setLevel(logging.INFO)
    has_stream = any(
        isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler)
        for handler in root_logger.handlers
    )
    has_file = any(
        isinstance(handler, RotatingFileHandler)
        and Path(getattr(handler, "baseFilename", "")).resolve() == log_path.resolve()
        for handler in root_logger.handlers
    )
    if not has_stream:
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        root_logger.addHandler(stream)
    if not has_file:
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
    logging.captureWarnings(True)


def apply_environment_from_settings(settings: Dict[str, Any]) -> None:
    """Push persisted endpoint settings into the current process environment."""
    base_url = str(settings.get("local_ai_base_url") or "").strip()
    model = str(settings.get("local_ai_model") or "").strip()
    if base_url:
        os.environ["LOCAL_AI_BASE_URL"] = base_url
    if model:
        os.environ["LOCAL_AI_MODEL"] = model


def initialize_runtime() -> Dict[str, Any]:
    """Prepare the Documents workspace once per process before app startup."""
    global _INITIALIZED
    if _INITIALIZED:
        return load_settings()
    settings = load_settings()
    ensure_workspace_files(settings)
    apply_environment_from_settings(settings)
    configure_logging()
    _INITIALIZED = True
    return settings


def helper_command(mode: str, *extra_args: str) -> list[str]:
    """Build one helper process command for source and frozen modes."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--helper", mode, *extra_args]
    return [sys.executable, "-m", "jobscraper", "--helper", mode, *extra_args]
