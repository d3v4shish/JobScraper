"""Module entry point for JobScraper."""

from __future__ import annotations

import sys

from .bootstrap import initialize_runtime
from .runtime.helper_main import maybe_run_helper


def main(argv: list[str] | None = None) -> int:
    """Start the desktop app or dispatch one helper runtime mode."""
    argv = list(sys.argv[1:] if argv is None else argv)
    initialize_runtime()
    helper_exit = maybe_run_helper(argv)
    if helper_exit is not None:
        return int(helper_exit)
    from .ui.main import main as ui_main

    return int(ui_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
