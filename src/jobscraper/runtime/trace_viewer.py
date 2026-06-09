#!/usr/bin/env python3
"""Small live JSON trace viewer for browser_debug artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import tkinter as tk
from tkinter.scrolledtext import ScrolledText


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", required=True)
    parser.add_argument("--title", default="Trace Viewer")
    parser.add_argument("--refresh-ms", type=int, default=700)
    parser.add_argument("--tail", type=int, default=25)
    return parser.parse_args(argv)


class TraceViewer:
    """Display the last N events from one JSON debug file in a small GUI."""

    def __init__(self, path: Path, title: str, refresh_ms: int, tail: int) -> None:
        self.path = path
        self.refresh_ms = max(200, int(refresh_ms))
        self.tail = max(1, int(tail))
        self.last_mtime = 0.0

        self.root = tk.Tk()
        self.root.title(title)
        self.root.geometry("1200x760")

        self.text = ScrolledText(self.root, wrap=tk.WORD, font=("Consolas", 10))
        self.text.pack(fill=tk.BOTH, expand=True)
        self.text.configure(state=tk.DISABLED)
        self._schedule_refresh()

    def _schedule_refresh(self) -> None:
        self._refresh()
        self.root.after(self.refresh_ms, self._schedule_refresh)

    def _refresh(self) -> None:
        if not self.path.exists():
            self._set_text(f"waiting for {self.path} ...")
            return
        try:
            stat = self.path.stat()
        except OSError as exc:
            self._set_text(f"could not stat {self.path}: {exc}")
            return
        if stat.st_mtime == self.last_mtime:
            return
        self.last_mtime = stat.st_mtime
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            self._set_text(f"could not parse {self.path}: {exc}")
            return
        events = list(payload.get("events") or [])
        lines = [
            f"file:       {self.path}",
            f"last_url:   {payload.get('last_url') or ''}",
            f"last_title: {payload.get('last_title') or ''}",
            f"cookies:    {payload.get('cookie_count') or 0}",
            f"events:     {len(events)}",
            "",
        ]
        for event in events[-self.tail :]:
            lines.append(json.dumps(event, ensure_ascii=False, indent=2))
            lines.append("")
        self._set_text("\n".join(lines).rstrip() + "\n")

    def _set_text(self, value: str) -> None:
        self.text.configure(state=tk.NORMAL)
        self.text.delete("1.0", tk.END)
        self.text.insert("1.0", value)
        self.text.configure(state=tk.DISABLED)

    def run(self) -> None:
        self.root.mainloop()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    viewer = TraceViewer(Path(args.path), args.title, args.refresh_ms, args.tail)
    viewer.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
