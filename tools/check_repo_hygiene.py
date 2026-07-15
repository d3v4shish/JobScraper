#!/usr/bin/env python3
"""Fail when tracked files look like private runtime data or local-machine leakage."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN_PATH_PARTS = (
    ".portal_sessions/",
    "browser_debug/",
    "__pycache__/",
    ".pytest_cache/",
    "dist/",
    "dist-linux/",
    "build-linux/",
    "build/JobScraper/",
    "build/ms-playwright/",
    "build/scrape_runs/",
    "src/JobScraper.egg-info/",
)
FORBIDDEN_PATH_SUFFIXES = (
    ".sqlite",
    ".log",
    ".tmp",
    ".swp",
)
FORBIDDEN_EXACT_PATHS = (
    ".coverage",
    "build/source_import_smoke.sqlite",
)
FORBIDDEN_CONTENT_MARKERS = (
    "C:\\Users\\",
    "D:\\Workspace\\",
    "AppData\\Roaming\\Mozilla\\Firefox\\Profiles",
    '"profile_path": "',
    '"state_path": "',
    '"debug_path": "',
    '"qweb_profile_root": "',
)


def tracked_files() -> list[Path]:
    output = subprocess.check_output(["git", "ls-files", "-z"], cwd=REPO_ROOT)
    return [Path(path) for path in output.decode("utf-8").split("\0") if path]


def is_text_candidate(path: Path) -> bool:
    return path.suffix.lower() in {
        ".md",
        ".py",
        ".toml",
        ".txt",
        ".json",
        ".yml",
        ".yaml",
        ".ini",
        ".cfg",
        ".ps1",
        ".sh",
        ".iss",
        ".spec",
    }


def main() -> int:
    errors: list[str] = []
    for relative_path in tracked_files():
        posix_path = relative_path.as_posix()
        if posix_path in FORBIDDEN_EXACT_PATHS:
            errors.append(f"forbidden tracked file: {posix_path}")
        if any(part in posix_path for part in FORBIDDEN_PATH_PARTS):
            errors.append(f"forbidden tracked path: {posix_path}")
        if any(posix_path.endswith(suffix) for suffix in FORBIDDEN_PATH_SUFFIXES):
            errors.append(f"forbidden tracked artifact: {posix_path}")

        absolute_path = REPO_ROOT / relative_path
        if not absolute_path.is_file() or not is_text_candidate(relative_path):
            continue
        try:
            content = absolute_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for marker in FORBIDDEN_CONTENT_MARKERS:
            if marker in content:
                errors.append(f"forbidden content marker {marker!r} in {posix_path}")

    if errors:
        print("Repo hygiene check failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("Repo hygiene check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
