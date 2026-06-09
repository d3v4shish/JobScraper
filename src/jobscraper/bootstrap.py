"""Runtime bootstrap for workspace setup, migration, and logging."""

from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

from . import paths
from .storage import fs


SETTINGS_VERSION = 1
MIGRATION_SENTINEL = "legacy_repo_migration_v1"
_INITIALIZED = False


def _copy_file(src: Path, dst: Path) -> None:
    """Copy one file into place when the destination is absent."""
    if dst.exists():
        return
    fs.copy_file(src, dst)


def default_settings_payload() -> Dict[str, Any]:
    """Return the default persisted settings payload."""
    workspace_root = paths.default_workspace_root()
    return {
        "version": SETTINGS_VERSION,
        "workspace_root": str(workspace_root),
        "db_path": str(paths.default_db_path(workspace_root=workspace_root)),
        "sources_path": str(paths.default_sources_path(workspace_root=workspace_root)),
        "source_watchlist_path": str(paths.default_source_watchlist_path(workspace_root=workspace_root)),
        "local_ai_base_url": "",
        "local_ai_model": "",
        "migration": {
            "completed": False,
            "marker": "",
            "source_root": "",
            "completed_at": 0,
            "errors": [],
        },
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
    migration = defaults.get("migration", {})
    if isinstance(payload.get("migration"), dict):
        migration.update(payload["migration"])
    defaults["migration"] = migration
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


def legacy_candidate_roots() -> list[Path]:
    """Return the filesystem roots that may contain the legacy repo-local workspace."""
    roots: list[Path] = []
    cwd = Path.cwd().resolve()
    roots.append(cwd)
    package_root = paths.package_root()
    roots.append(package_root.parent)
    roots.append(package_root.parent.parent)
    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(root)
    return unique


def find_legacy_root() -> Optional[Path]:
    """Locate the current repo-root style workspace if it still exists."""
    for root in legacy_candidate_roots():
        if (root / "company_jobs.sqlite").exists() or (root / "company_sources.json").exists():
            return root
    return None


def migrate_legacy_workspace(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Copy the legacy repo-root runtime artifacts into the Documents workspace."""
    migration = dict(settings.get("migration") or {})
    if migration.get("completed") and migration.get("marker") == MIGRATION_SENTINEL:
        return settings

    legacy_root = find_legacy_root()
    migration_errors: list[str] = []
    if legacy_root is None:
        migration.update(
            {
                "completed": True,
                "marker": MIGRATION_SENTINEL,
                "source_root": "",
                "completed_at": int(time.time()),
                "errors": [],
            }
        )
        settings["migration"] = migration
        save_settings(settings)
        return settings

    copy_targets = [
        (legacy_root / "company_jobs.sqlite", Path(settings["db_path"])),
        (legacy_root / "company_sources.json", Path(settings["sources_path"])),
    ]
    for src, dst in copy_targets:
        try:
            if src.exists():
                _copy_file(src, dst)
        except Exception as exc:  # pragma: no cover - defensive migration logging
            migration_errors.append(f"{src.name}: {exc}")
    migration.update(
        {
            "completed": True,
            "marker": MIGRATION_SENTINEL,
            "source_root": str(legacy_root),
            "completed_at": int(time.time()),
            "errors": migration_errors,
        }
    )
    settings["migration"] = migration
    save_settings(settings)
    return settings


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
    settings = migrate_legacy_workspace(settings)
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
