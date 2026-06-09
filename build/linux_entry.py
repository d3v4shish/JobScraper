"""PyInstaller entry wrapper for Linux onedir builds."""

from __future__ import annotations

from jobscraper.__main__ import main


if __name__ == "__main__":
    raise SystemExit(main())
