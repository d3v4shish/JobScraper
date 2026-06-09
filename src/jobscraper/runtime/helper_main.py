"""Dispatch helper runtime modes for source and frozen builds."""

from __future__ import annotations

from typing import Optional


def maybe_run_helper(argv: list[str]) -> Optional[int]:
    """Run one helper mode when `--helper` is present, otherwise return None."""
    if not argv or argv[0] != "--helper":
        return None
    if len(argv) < 2:
        raise SystemExit("missing helper mode after --helper")
    mode = str(argv[1] or "").strip().lower()
    helper_args = list(argv[2:])
    if mode == "trace-viewer":
        from . import trace_viewer

        return int(trace_viewer.main(helper_args))
    raise SystemExit(f"unknown helper mode: {mode}")
