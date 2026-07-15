#!/usr/bin/env python3
"""Qt model/view classes and compact pane widgets for repeated UI data."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QSizePolicy, QWidget

from ..storage import db
from .utils import compact_text, format_ts


def _source_health_label(row: Dict[str, Any]) -> str:
    failures = int(row.get("failure_count") or 0)
    successes = int(row.get("success_count") or 0)
    last_status = str(row.get("last_status") or "").strip().lower()
    if last_status in {"error", "manual_review", "blocked_skipped", "parser_issue"}:
        return "needs review"
    if successes <= 0 and failures > 0:
        return "failing"
    if successes > 0:
        return "ok"
    return "new"


def _prepare_source_row(row: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(row)
    for key in ("open_count", "matching_count", "failure_count", "last_duration_ms", "source_quality_score"):
        item[f"_display_{key}"] = str(int(item.get(key) or 0))
    for key in ("company", "ats", "portal", "entry_kind", "source_health_group", "last_status", "last_error"):
        item[f"_display_{key}"] = compact_text(item.get(key), 120)
    item["_display_source_health"] = _source_health_label(item)
    return item


def _prepare_company_row(row: Dict[str, Any], *, checked: bool) -> Dict[str, Any]:
    item = dict(row)
    item["checked"] = checked
    item["_display_company"] = str(item.get("company") or "")
    item["_display_open_count"] = str(int(item.get("open_count") or 0))
    item["_display_matching_count"] = str(int(item.get("matching_count") or 0))
    return item


def _prepare_jobs_row(row: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(row)
    if item.get("row_type") == "group":
        item["_display_company"] = f"{item.get('company')}  ({int(item.get('count') or 0)})"
        item["_display_title"] = ""
        item["_display_location"] = ""
        item["_display_detected_stack"] = ""
        item["_display_source_portal"] = ""
        item["_display_published_at"] = ""
        return item
    item["_display_company"] = compact_text(item.get("company"), 120)
    item["_display_title"] = compact_text(item.get("title"), 120)
    item["_display_location"] = compact_text(item.get("location"), 120)
    item["_display_detected_stack"] = compact_text(item.get("detected_stack"), 120)
    item["_display_source_portal"] = str(item.get("source_portal") or "company_board")
    item["_display_published_at"] = format_ts(item.get("published_at") or item.get("updated_at") or item.get("last_seen_at"))
    return item


class BusyStrip(QWidget):
    """Compact pane-level busy indicator used throughout the workbench."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("BusyStrip")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.label = QLabel("", self)
        self.label.setObjectName("PaneMeta")
        self.label.setMinimumWidth(0)
        self.label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.bar = QProgressBar(self)
        self.bar.setTextVisible(False)
        self.bar.setFixedWidth(120)
        layout.addWidget(self.label, 1)
        layout.addWidget(self.bar, 0)
        self.hide()

    def set_busy(self, label: str, *, determinate: bool = False, total: int = 0, value: int = 0) -> None:
        self.label.setText(label)
        if determinate and total > 0:
            self.bar.setRange(0, total)
            self.bar.setValue(max(0, min(value, total)))
        else:
            self.bar.setRange(0, 0)
        self.show()

    def set_progress(self, value: int, *, label: Optional[str] = None, total: Optional[int] = None) -> None:
        if label is not None:
            self.label.setText(label)
        if total is not None and total > 0:
            self.bar.setRange(0, total)
        if self.bar.maximum() > 0:
            self.bar.setValue(max(0, min(value, self.bar.maximum())))
        self.show()

    def clear(self) -> None:
        self.label.clear()
        self.hide()


class SourcesTableModel(QAbstractTableModel):
    """Model backing the sources admin table."""

    COLUMNS = [
        ("Company", "company"),
        ("ATS", "ats"),
        ("Portal", "portal"),
        ("Entry", "entry_kind"),
        ("Open", "open_count"),
        ("Matching", "matching_count"),
        ("Group", "source_health_group"),
        ("Quality", "source_quality_score"),
        ("Health", "source_health"),
        ("Fail", "failure_count"),
        ("ms", "last_duration_ms"),
        ("Status", "last_status"),
        ("Error", "last_error"),
    ]
    HEADER_TOOLTIPS = {
        "company": "Display name for the configured source row.",
        "ats": "Primary adapter family used for the source, such as greenhouse, lever, ashby, workday, public API, RSS, or search adapters.",
        "portal": "Source family for public rows, such as Wellfound, RemoteOK, or Hacker News. Company-board rows leave this empty.",
        "entry_kind": "How the source is entered, such as a public search, API feed, RSS feed, or company board.",
        "open_count": "Number of currently open jobs stored for this source.",
        "matching_count": "Number of open jobs from this source that match the current interest and location profile.",
        "source_health_group": "Action-oriented source health group: healthy, disabled, blocked, parser failure, or new.",
        "source_quality_score": "0-100 source quality score based on successes, failures, open jobs, matching jobs, and closed-job churn.",
        "source_health": "Legacy compact health label derived from success/failure counters.",
        "failure_count": "Cumulative failed scrape/import attempts for this source.",
        "last_duration_ms": "Duration of the most recent scrape attempt for this source in milliseconds.",
        "last_status": "Last scrape/import result recorded for the source.",
        "last_error": "Most recent source-specific error or blocked-source note.",
    }

    def __init__(self) -> None:
        super().__init__()
        self.rows: List[Dict[str, Any]] = []
        self._mono_font = QFont("JetBrains Mono", 10)
        self._error_color = QColor("#ff6b6b")
        self._review_color = QColor("#ff9b71")
        self._healthy_color = QColor("#8ddb8c")
        self._disabled_color = QColor("#8b949e")
        self._new_color = QColor("#f0b35f")

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.COLUMNS)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not (0 <= index.row() < len(self.rows)):
            return None
        row = self.rows[index.row()]
        _header, key = self.COLUMNS[index.column()]
        if role == Qt.ItemDataRole.DisplayRole:
            return row.get(f"_display_{key}", "")
        if role == Qt.ItemDataRole.FontRole:
            return self._mono_font
        if role == Qt.ItemDataRole.ForegroundRole and key == "last_error" and row.get("last_error"):
            return self._error_color
        if role == Qt.ItemDataRole.ForegroundRole and key == "source_health_group":
            group = str(row.get("source_health_group") or "")
            if group == "healthy":
                return self._healthy_color
            if group in {"blocked", "parser failure"}:
                return self._review_color
            if group == "disabled":
                return self._disabled_color
            return self._new_color
        if role == Qt.ItemDataRole.ForegroundRole and key == "source_health":
            health = str(row.get("_display_source_health") or "")
            if health == "ok":
                return self._healthy_color
            if health in {"failing", "needs review"}:
                return self._review_color
            return self._new_color
        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:  # noqa: N802
        if orientation == Qt.Orientation.Horizontal:
            if role == Qt.ItemDataRole.DisplayRole:
                return self.COLUMNS[section][0]
            if role == Qt.ItemDataRole.ToolTipRole:
                return self.HEADER_TOOLTIPS.get(self.COLUMNS[section][1], "")
        return super().headerData(section, orientation, role)

    def set_rows(self, rows: Sequence[Dict[str, Any]]) -> None:
        self.beginResetModel()
        self.rows = [_prepare_source_row(row) for row in rows]
        self.endResetModel()


class CompanyFilterModel(QAbstractTableModel):
    """Checkable model backing the company filter sidebar."""

    checksChanged = pyqtSignal()
    COLUMNS = [("", "checked"), ("Company", "company"), ("Open", "open_count"), ("Matching", "matching_count")]
    HEADER_TOOLTIPS = {
        "checked": "Toggle whether the company participates in the current jobs view.",
        "company": "Employer name represented in the current result set.",
        "open_count": "Number of open jobs currently visible for the company under the non-company filters.",
        "matching_count": "Number of company jobs that match the current interest and location profile.",
    }

    def __init__(self) -> None:
        super().__init__()
        self.rows: List[Dict[str, Any]] = []
        self._mono_font = QFont("JetBrains Mono", 10)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.COLUMNS)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not (0 <= index.row() < len(self.rows)):
            return None
        row = self.rows[index.row()]
        _header, key = self.COLUMNS[index.column()]
        if role == Qt.ItemDataRole.CheckStateRole and index.column() == 0:
            return Qt.CheckState.Checked if row.get("checked", True) else Qt.CheckState.Unchecked
        if role == Qt.ItemDataRole.DisplayRole:
            if index.column() == 0:
                return ""
            return row.get(f"_display_{key}", "")
        if role == Qt.ItemDataRole.FontRole:
            return self._mono_font
        return None

    def setData(self, index: QModelIndex, value: Any, role: int = Qt.ItemDataRole.EditRole) -> bool:  # noqa: N802
        if not index.isValid() or index.column() != 0:
            return False
        if role == Qt.ItemDataRole.CheckStateRole:
            self.rows[index.row()]["checked"] = value == Qt.CheckState.Checked
            self.dataChanged.emit(index, index, [Qt.ItemDataRole.CheckStateRole])
            self.checksChanged.emit()
            return True
        return False

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.column() == 0:
            flags |= Qt.ItemFlag.ItemIsUserCheckable
        return flags

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:  # noqa: N802
        if orientation == Qt.Orientation.Horizontal:
            if role == Qt.ItemDataRole.DisplayRole:
                return self.COLUMNS[section][0]
            if role == Qt.ItemDataRole.ToolTipRole:
                return self.HEADER_TOOLTIPS.get(self.COLUMNS[section][1], "")
        return super().headerData(section, orientation, role)

    def set_rows(self, rows: Sequence[Dict[str, Any]], preserve: Optional[Sequence[str]] = None) -> None:
        preserve_set = {str(item) for item in (preserve or self.checked_companies())}
        self.beginResetModel()
        self.rows = []
        for row in rows:
            company = str(row.get("company") or "")
            checked = not preserve_set or company in preserve_set
            self.rows.append(_prepare_company_row(row, checked=checked))
        self.endResetModel()

    def checked_companies(self) -> List[str]:
        return [str(row.get("company") or "") for row in self.rows if row.get("checked")]

    def set_all(self, checked: bool) -> None:
        if not self.rows:
            return
        self.beginResetModel()
        for row in self.rows:
            row["checked"] = checked
        self.endResetModel()
        self.checksChanged.emit()

    def set_checked_companies(self, companies: Sequence[str]) -> None:
        """Replace the checked-company set with the provided company names."""
        wanted = {str(company).strip() for company in companies if str(company).strip()}
        self.beginResetModel()
        for row in self.rows:
            row["checked"] = row.get("company") in wanted
        self.endResetModel()
        self.checksChanged.emit()

    def company_at(self, row: int) -> str:
        """Return the company name at the given row or an empty string for invalid rows."""
        if 0 <= row < len(self.rows):
            return str(self.rows[row].get("company") or "")
        return ""

    def is_checked_at(self, row: int) -> bool:
        """Return whether the given row is currently checked."""
        if 0 <= row < len(self.rows):
            return bool(self.rows[row].get("checked"))
        return False

    def toggle_row(self, row: int) -> None:
        """Toggle one company's checked state and notify listeners."""
        if not (0 <= row < len(self.rows)):
            return
        self.rows[row]["checked"] = not bool(self.rows[row].get("checked"))
        left = self.index(row, 0)
        right = self.index(row, self.columnCount() - 1)
        self.dataChanged.emit(left, right, [Qt.ItemDataRole.CheckStateRole, Qt.ItemDataRole.DisplayRole])
        self.checksChanged.emit()


class JobsTableModel(QAbstractTableModel):
    """Dense jobs table model with optional grouped company header rows."""

    COLUMNS = [
        ("Company", "company"),
        ("Title", "title"),
        ("Location", "location"),
        ("Stack", "detected_stack"),
        ("Source", "source_portal"),
        ("Posted", "published_at"),
    ]
    HEADER_TOOLTIPS = {
        "company": "Employer name for the job row. In grouped mode, company headers are inserted above related jobs.",
        "title": "Normalized role title from the scraped job posting.",
        "location": "Best-effort location string extracted from the job or ATS metadata.",
        "detected_stack": "Detected domain, language, tool, and group tags inferred from the job description.",
        "source_portal": "Where the job came from, such as company_board, Hacker News, Wellfound, or another public source family.",
        "published_at": "Posting timestamp from the source when available; falls back to the latest seen timestamp.",
    }

    def __init__(self) -> None:
        super().__init__()
        self.rows: List[Dict[str, Any]] = []
        self._row_signature: tuple[tuple[Any, ...], ...] = ()
        self._row_by_job_id: Dict[int, int] = {}
        self._visible_job_ids: List[int] = []
        self._mono_font = QFont("JetBrains Mono", 10)
        self._group_font = QFont("Inter", 10)
        self._group_font.setBold(True)
        self._group_background = QColor("#161b22")
        self._group_foreground = QColor("#f0f6fc")

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.COLUMNS)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not (0 <= index.row() < len(self.rows)):
            return None
        row = self.rows[index.row()]
        row_type = row.get("row_type", "job")
        _header, key = self.COLUMNS[index.column()]
        if role == Qt.ItemDataRole.DisplayRole:
            return row.get(f"_display_{key}", "")
        if role == Qt.ItemDataRole.FontRole:
            if row_type == "group":
                return self._group_font
            return self._mono_font
        if role == Qt.ItemDataRole.BackgroundRole and row_type == "group":
            return self._group_background
        if role == Qt.ItemDataRole.ForegroundRole and row_type == "group":
            return self._group_foreground
        if role == Qt.ItemDataRole.TextAlignmentRole and key == "published_at":
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        row = self.rows[index.row()]
        if row.get("row_type") == "group":
            return Qt.ItemFlag.ItemIsEnabled
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:  # noqa: N802
        if orientation == Qt.Orientation.Horizontal:
            if role == Qt.ItemDataRole.DisplayRole:
                return self.COLUMNS[section][0]
            if role == Qt.ItemDataRole.ToolTipRole:
                return self.HEADER_TOOLTIPS.get(self.COLUMNS[section][1], "")
        return super().headerData(section, orientation, role)

    def set_jobs(self, jobs: Sequence[Dict[str, Any]], *, grouped: bool) -> None:
        rows: List[Dict[str, Any]] = []
        if grouped:
            grouped_map = db.group_jobs_by_company(jobs)
            for company, company_jobs in grouped_map.items():
                rows.append({"row_type": "group", "company": company, "count": len(company_jobs)})
                for job in company_jobs:
                    item = dict(job)
                    item["row_type"] = "job"
                    rows.append(item)
        else:
            for job in jobs:
                item = dict(job)
                item["row_type"] = "job"
                rows.append(item)
        self.set_rows(rows)

    @staticmethod
    def row_signature(rows: Sequence[Dict[str, Any]]) -> tuple[Any, ...]:
        return tuple(
            (
                row.get("row_type", "job"),
                row.get("id"),
                row.get("company"),
                row.get("title"),
                row.get("location"),
                row.get("detected_stack"),
                row.get("source_portal"),
                row.get("published_at") or row.get("updated_at") or row.get("last_seen_at"),
                row.get("status"),
                row.get("count"),
            )
            for row in rows
        )

    def set_rows(self, rows: Sequence[Dict[str, Any]], signature: Optional[Sequence[Any]] = None) -> bool:
        """Replace the prepared jobs-table rows without rebuilding them on the UI thread."""
        row_signature = tuple(signature) if signature is not None else self.row_signature(rows)
        if row_signature == self._row_signature:
            return False
        new_rows = [_prepare_jobs_row(row) for row in rows]
        self.beginResetModel()
        self.rows = new_rows
        self._row_signature = row_signature
        self._row_by_job_id = {}
        self._visible_job_ids = []
        for index, row in enumerate(self.rows):
            if row.get("row_type") != "job":
                continue
            try:
                job_id = int(row.get("id") or 0)
            except (TypeError, ValueError):
                continue
            if job_id:
                self._row_by_job_id[job_id] = index
                self._visible_job_ids.append(job_id)
        self.endResetModel()
        return True

    def job_id_at(self, row_index: int) -> Optional[int]:
        if not (0 <= row_index < len(self.rows)):
            return None
        row = self.rows[row_index]
        if row.get("row_type") != "job":
            return None
        try:
            return int(row.get("id") or 0) or None
        except (TypeError, ValueError):
            return None

    def row_for_job_id(self, job_id: int) -> int:
        return self._row_by_job_id.get(int(job_id), -1)

    def visible_job_ids(self) -> List[int]:
        return list(self._visible_job_ids)

    def preview_for_job_id(self, job_id: int) -> Optional[Dict[str, Any]]:
        row_index = self.row_for_job_id(int(job_id))
        if row_index >= 0:
            return dict(self.rows[row_index])
        return None
