"""Filesystem helpers for app-owned runtime data."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any


PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600


def chmod_best_effort(path: Path | str, mode: int) -> None:
    """Apply POSIX permissions when supported by the current platform."""
    if os.name == "nt":
        return
    try:
        Path(path).chmod(mode)
    except OSError:
        return


def ensure_dir(path: Path | str, *, mode: int = PRIVATE_DIR_MODE) -> Path:
    """Create an app-owned directory and tighten permissions where possible."""
    target = Path(path)
    target.mkdir(parents=True, exist_ok=True)
    chmod_best_effort(target, mode)
    return target


def _fsync_parent(path: Path) -> None:
    if os.name == "nt":
        return
    try:
        dir_fd = os.open(str(path.parent), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)


def _prepare_write_parent(target: Path) -> Path:
    parent = target.parent
    if str(parent) not in ("", "."):
        ensure_dir(parent)
    return parent


def atomic_write_text(
    path: Path | str,
    text: str,
    *,
    encoding: str = "utf-8",
    mode: int = PRIVATE_FILE_MODE,
) -> Path:
    """Write a text file with replace semantics so readers never see partial data."""
    target = Path(path)
    parent = _prepare_write_parent(target)
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(parent))
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        chmod_best_effort(temp_path, mode)
        os.replace(temp_path, target)
        chmod_best_effort(target, mode)
        _fsync_parent(target)
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
    return target


def atomic_write_json(
    path: Path | str,
    payload: Any,
    *,
    indent: int = 2,
    trailing_newline: bool = False,
    mode: int = PRIVATE_FILE_MODE,
) -> Path:
    """Serialize JSON and persist it atomically with app-private permissions."""
    text = json.dumps(payload, ensure_ascii=False, indent=indent)
    if trailing_newline:
        text += "\n"
    return atomic_write_text(path, text, mode=mode)


def copy_file(src: Path | str, dst: Path | str, *, mode: int = PRIVATE_FILE_MODE) -> Path:
    """Copy one app-owned runtime file atomically into place."""
    source = Path(src)
    target = Path(dst)
    parent = _prepare_write_parent(target)
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(parent))
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        shutil.copy2(source, temp_path)
        chmod_best_effort(temp_path, mode)
        os.replace(temp_path, target)
        chmod_best_effort(target, mode)
        _fsync_parent(target)
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
    return target
