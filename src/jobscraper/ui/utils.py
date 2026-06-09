#!/usr/bin/env python3
"""Small utility helpers shared by the desktop UI modules."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any


def format_ts(value: Any) -> str:
    """Format a Unix timestamp or ISO datetime for compact desktop display."""
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    try:
        ts = int(text)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return ""
        return dt.astimezone().strftime("%Y-%m-%d %H:%M") if dt.tzinfo else dt.strftime("%Y-%m-%d %H:%M")
    if ts <= 0:
        return ""
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def compact_text(value: Any, limit: int = 120) -> str:
    """Collapse whitespace and ellipsize long strings for dense tables."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def stable_signature(value: Any) -> str:
    """Create a stable JSON signature for change detection and cache keys."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
