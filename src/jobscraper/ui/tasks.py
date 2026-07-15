#!/usr/bin/env python3
"""Background task helpers used by the request graph and workers."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
from typing import Any, Callable, Dict, List, Sequence

from .. import paths
from ..ai import client as ai_client
from ..storage import db
from ..storage import fs
from ..ai import roadmap
from .renderers import (
    html_shell,
    render_analytics_html,
    render_description_html,
    render_roadmap_html,
)
from .utils import compact_text, format_ts


def initialize_database_task(db_path: Path) -> Dict[str, Any]:
    """Initialize and migrate the SQLite database off the GUI thread."""
    db.init_db(db_path)
    return {"db_path": str(db_path)}


def load_ai_status_task() -> Dict[str, Any]:
    """Return one compact availability snapshot for OpenAI and Local AI."""
    openai_config = ai_client.openai_config()
    openai_ready = openai_config is not None
    openai_label = "configured" if openai_ready else "not configured"
    openai_detail = ""
    if openai_config:
        openai_detail = " | ".join(
            bit
            for bit in [
                str(openai_config.get("base_url") or "").strip(),
                str(openai_config.get("model") or "").strip(),
            ]
            if bit
        )
    local_status = ai_client.local_ai_status()
    local_label = str(local_status.get("label") or ("ready" if local_status.get("ready") else "not ready")).strip()
    local_detail = str(local_status.get("detail") or "").strip()
    summary = f"OpenAI: {openai_label} | Local AI: {local_label or 'not ready'}"
    detail = " | ".join(bit for bit in [f"OpenAI {openai_detail}" if openai_detail else "", f"Local AI {local_detail}" if local_detail else ""] if bit)
    activity = summary + (f" | {detail}" if detail else "")
    return {
        "openai_ready": openai_ready,
        "openai_label": openai_label,
        "openai_detail": openai_detail,
        "openai_model": str((openai_config or {}).get("model") or ""),
        "openai_base_url": str((openai_config or {}).get("base_url") or ""),
        "local_status": dict(local_status),
        "summary": summary,
        "detail": detail,
        "activity": activity,
    }


def import_sources_task(db_path: Path, sources_path: Path) -> Dict[str, Any]:
    """Import the source JSON into SQLite from a worker thread."""
    db.init_db(db_path)
    return db.import_sources_report(db_path, sources_path, create_backup=True)


def preview_source_import_task(db_path: Path, sources_path: Path) -> Dict[str, Any]:
    """Preview a source JSON import from a worker thread."""
    return db.preview_source_import(db_path, sources_path)


def load_source_config_rows(sources_path: Path) -> List[Dict[str, Any]]:
    """Load the editable source JSON rows, copying bundled defaults if needed."""
    if not sources_path.exists():
        if not paths.bundled_sources_path().exists():
            raise FileNotFoundError(f"Source file does not exist: {sources_path}")
        fs.copy_file(paths.bundled_sources_path(), sources_path)
    payload = json.loads(sources_path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, list):
        raise ValueError("Source file must contain a JSON list.")
    invalid_count = sum(1 for item in payload if not isinstance(item, dict))
    if invalid_count:
        raise ValueError(f"Source file contains {invalid_count} non-object source rows.")
    return [dict(item) for item in payload]


def validate_source_edit_values(source: Dict[str, Any]) -> str:
    """Return an error string for invalid editable source values."""
    ats = str(source.get("ats") or "").strip().lower()
    url = str(source.get("url") or "").strip()
    entry_url = str(source.get("entry_url") or "").strip()
    if ats not in db.SUPPORTED_ATS:
        return f"Unsupported ATS/source type: {ats}"
    if url and not (url.startswith("http://") or url.startswith("https://")):
        return "URL must start with http:// or https://."
    if entry_url and not (entry_url.startswith("http://") or entry_url.startswith("https://")):
        return "Entry URL must start with http:// or https://."
    if ats in db.URL_REQUIRED_ATS and not (url or entry_url):
        return f"{ats} requires a URL."
    return ""


def _source_identity(row: Dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row.get("company") or "").strip().casefold(),
        str(row.get("ats") or "").strip().casefold(),
        str(row.get("token") or "").strip(),
        str(row.get("url") or "").strip(),
    )


def save_source_config_edit_task(
    db_path: Path,
    sources_path: Path,
    selected: Dict[str, Any],
    edited_source: Dict[str, Any],
) -> Dict[str, Any]:
    """Write one source JSON edit after validation and SQLite re-import."""
    rows = load_source_config_rows(sources_path)
    target_identity = _source_identity(selected)
    target_index = next((index for index, row in enumerate(rows) if _source_identity(row) == target_identity), -1)
    if target_index < 0:
        raise ValueError("Selected source row was not found in the active source file.")
    source = dict(rows[target_index])
    source.update(edited_source)
    source["ats"] = str(source.get("ats") or "").strip().lower()
    source["url"] = str(source.get("url") or "").strip()
    source["entry_url"] = str(source.get("entry_url") or source.get("url") or "").strip()
    error = validate_source_edit_values(source)
    if error:
        raise ValueError(error)
    rows[target_index] = source
    backup_path = fs.ensure_dir(paths.backups_dir()) / f"{sources_path.stem}.source-edit.{db.now_ts()}.json"
    fs.copy_file(sources_path, backup_path)
    fs.atomic_write_json(sources_path, rows, trailing_newline=True)
    import_report = db.import_sources_report(db_path, sources_path, create_backup=False)
    return {"backup_path": str(backup_path), "import_report": import_report, "source": source}


def probe_watchlist_and_import_task(
    db_path: Path,
    watchlist_path: Path,
    sources_path: Path,
    report_path: Path,
) -> Dict[str, Any]:
    """Probe the candidate watchlist, promote verified sources, and import them."""
    from ..scraping import core

    if not sources_path.exists() and paths.bundled_sources_path().exists():
        fs.copy_file(paths.bundled_sources_path(), sources_path)
    if not watchlist_path.exists() and paths.bundled_source_watchlist_path().exists():
        fs.copy_file(paths.bundled_source_watchlist_path(), watchlist_path)
    fs.ensure_dir(report_path.parent)
    result = core.probe_and_promote_watchlist(
        candidates_path=watchlist_path,
        sources_path=sources_path,
        report_path=report_path,
    )
    import_report: Dict[str, Any] = {}
    if int(result.get("promoted") or 0) > 0:
        db.init_db(db_path)
        import_report = db.import_sources_report(db_path, sources_path, create_backup=True)
    result["import_report"] = import_report
    return result


def export_jobs_task(
    db_path: Path,
    out_path: str,
    *,
    matching_only: bool,
    open_only: bool,
    companies: Sequence[str],
    portal: str,
    source_id: int,
    source_tag: str,
    hn_mode: str,
    founding_only: bool,
    search: str,
    stack: str,
    should_cancel: Callable[[], bool] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> Dict[str, Any]:
    """Export the current filtered job view without blocking the GUI thread."""
    try:
        count = db.export_jobs_json(
            db_path,
            out_path,
            matching_only=matching_only,
            open_only=open_only,
            companies=companies,
            portal=portal,
            source_id=source_id,
            source_tag=source_tag,
            hn_mode=hn_mode,
            founding_only=founding_only,
            search=search,
            stack=stack,
            progress=progress,
            should_cancel=should_cancel,
        )
    except db.ExportCancelled:
        return {"count": 0, "out_path": out_path, "cancelled": True}
    return {"count": count, "out_path": out_path, "cancelled": False}


def _path_size(path: Path) -> int:
    """Return a best-effort byte size for one file or directory tree."""
    if not path.exists():
        return 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    try:
        children = list(path.rglob("*"))
    except OSError:
        return 0
    for child in children:
        if not child.is_file():
            continue
        try:
            total += child.stat().st_size
        except OSError:
            continue
    return total


def _format_bytes(size: int) -> str:
    value = float(max(0, int(size)))
    for suffix in ("B", "KB", "MB", "GB"):
        if value < 1024.0 or suffix == "GB":
            if suffix == "B":
                return f"{int(value)} {suffix}"
            return f"{value:.1f} {suffix}"
        value /= 1024.0
    return f"{value:.1f} GB"


def load_storage_category_rows_task(categories: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return storage categories with current byte sizes."""
    rows: List[Dict[str, Any]] = []
    for category in categories:
        item = dict(category)
        size = _path_size(Path(item["path"]))
        item["size"] = size
        item["size_label"] = _format_bytes(size)
        rows.append(item)
    return rows


def delete_storage_category_task(category: Dict[str, Any]) -> Dict[str, Any]:
    """Delete files for one generated storage category."""
    item = dict(category)
    if not bool(item.get("deletable")):
        return {"deleted": False, "key": str(item.get("key") or ""), "label": str(item.get("label") or ""), "path": str(item.get("path") or "")}
    target = Path(item["path"])
    if target.exists():
        try:
            if target.is_dir():
                for child in target.iterdir():
                    if child.is_dir():
                        shutil.rmtree(child)
                    else:
                        child.unlink()
            else:
                target.unlink()
        except OSError as exc:
            raise RuntimeError(f"Could not delete {item.get('label') or item.get('key')}: {exc}") from exc
    return {
        "deleted": True,
        "key": str(item.get("key") or ""),
        "label": str(item.get("label") or ""),
        "path": str(target),
    }


def _filter_summary(filters: Dict[str, Any]) -> str:
    """Return a compact human label for active job filters."""
    parts: List[str] = []
    if filters.get("matching_only"):
        parts.append("matching")
    if filters.get("open_only"):
        parts.append("open")
    if filters.get("founding_only"):
        parts.append("founding")
    for key, label in (
        ("portal", "portal"),
        ("source_tag", "tag"),
        ("hn_mode", "hn"),
        ("stack", "stack"),
        ("search", "search"),
    ):
        value = str(filters.get(key) or "").strip()
        if value:
            parts.append(f"{label}:{value}")
    source_id = int(filters.get("source_id") or 0)
    if source_id:
        parts.append(f"source:{source_id}")
    companies = list(filters.get("companies") or [])
    if companies:
        parts.append(f"companies:{len(companies)}")
    return ", ".join(parts) if parts else "all jobs"


def _job_row_signature(rows: Sequence[Dict[str, Any]]) -> tuple[Any, ...]:
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


def _prepare_jobs_display_row(row: Dict[str, Any]) -> Dict[str, Any]:
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


def load_jobs_view_task(
    db_path: Path,
    *,
    matching_only: bool,
    open_only: bool,
    companies: Sequence[str],
    portal: str,
    source_id: int,
    source_tag: str,
    hn_mode: str,
    founding_only: bool,
    search: str,
    stack: str,
    group_by_company: bool,
    limit: int = 2000,
) -> Dict[str, Any]:
    """Load jobs plus pre-expanded table rows off the GUI thread."""
    jobs = db.query_jobs(
        db_path,
        matching_only=matching_only,
        open_only=open_only,
        search=search,
        stack=stack,
        companies=companies,
        portal=portal,
        source_id=source_id,
        source_tag=source_tag,
        hn_mode=hn_mode,
        founding_only=founding_only,
        group_by_company=group_by_company,
        limit=limit,
        summary_only=True,
    )
    rows: List[Dict[str, Any]] = []
    if group_by_company:
        grouped_map = db.group_jobs_by_company(jobs)
        for company, company_jobs in grouped_map.items():
            rows.append(_prepare_jobs_display_row({"row_type": "group", "company": company, "count": len(company_jobs)}))
            for job in company_jobs:
                item = dict(job)
                item["row_type"] = "job"
                rows.append(_prepare_jobs_display_row(item))
    else:
        for job in jobs:
            item = dict(job)
            item["row_type"] = "job"
            rows.append(_prepare_jobs_display_row(item))
    return {"jobs": list(jobs), "rows": rows, "grouped": group_by_company, "row_signature": _job_row_signature(rows)}


def load_job_detail_view_task(db_path: Path, job_id: int) -> Dict[str, Any]:
    """Load one selected-job detail payload and pre-render its HTML."""
    job = db.get_job_detail(db_path, int(job_id))
    if not job:
        return {
            "job": None,
            "html": html_shell("Description", "<p class='meta'>Job detail could not be loaded.</p>"),
        }
    return {"job": job, "html": render_description_html(job)}


def load_analytics_view_task(
    db_path: Path,
    *,
    matching_only: bool,
    open_only: bool,
    companies: Sequence[str],
    portal: str,
    source_id: int,
    source_tag: str,
    hn_mode: str,
    search: str,
    stack: str,
    founding_only: bool,
) -> Dict[str, Any]:
    """Load analytics data and pre-render the analysis document."""
    filters = {
        "matching_only": matching_only,
        "open_only": open_only,
        "companies": list(companies),
        "portal": portal,
        "source_id": source_id,
        "source_tag": source_tag,
        "hn_mode": hn_mode,
        "search": search,
        "stack": stack,
        "founding_only": founding_only,
    }
    payload = db.analytics_summary(
        db_path,
        matching_only=matching_only,
        open_only=open_only,
        companies=companies,
        portal=portal,
        source_id=source_id,
        source_tag=source_tag,
        hn_mode=hn_mode,
        search=search,
        stack=stack,
        founding_only=founding_only,
    )
    totals = payload.get("totals") or {}
    payload["scope_label"] = "Current filters"
    payload["scope_mode"] = "filters"
    payload["sample_size"] = int(totals.get("total") or 0)
    payload["refreshed_at"] = db.now_ts()
    payload["active_filters"] = _filter_summary(filters)
    return {"payload": payload, "html": render_analytics_html(payload)}


def build_roadmap_payload(
    db_path: Path,
    *,
    scope_mode: str,
    selected_job_ids: Sequence[int],
    current_job_filters: Dict[str, Any],
    selected_companies: Sequence[str],
) -> Dict[str, Any]:
    """Build roadmap input for the chosen scope in a worker thread."""
    jobs: List[Dict[str, Any]] = []
    scope_label = "Current filters"
    if scope_mode == "all":
        scope_label = "Whole DB"
        jobs = db.query_jobs(
            db_path,
            matching_only=False,
            open_only=True,
            search="",
            stack="",
            companies=[],
            source_id=0,
            source_tag="",
            hn_mode="",
            limit=100000,
            summary_only=True,
        )
    elif scope_mode == "selected" and selected_job_ids:
        scope_label = "Selected jobs"
        jobs = db.get_job_details(db_path, selected_job_ids)
    else:
        scope_label = "Selected companies / visible jobs" if scope_mode == "selected" else "Current filters"
        jobs = db.query_jobs(
            db_path,
            matching_only=bool(current_job_filters.get("matching_only", True)),
            open_only=bool(current_job_filters.get("open_only", True)),
            search=str(current_job_filters.get("search") or ""),
            stack=str(current_job_filters.get("stack") or ""),
            companies=current_job_filters.get("companies") or [],
            portal=str(current_job_filters.get("portal") or ""),
            source_id=int(current_job_filters.get("source_id") or 0),
            source_tag=str(current_job_filters.get("source_tag") or ""),
            hn_mode=str(current_job_filters.get("hn_mode") or ""),
            founding_only=bool(current_job_filters.get("founding_only", False)),
            limit=2000,
        )
    payload = roadmap.generate_topic_roadmap(
        jobs,
        scope=scope_label,
        selected_companies=list(selected_companies),
    )
    payload["scope_label"] = scope_label
    payload["scope_mode"] = scope_mode
    payload["sample_size"] = len(jobs)
    payload["refreshed_at"] = db.now_ts()
    payload["active_filters"] = _filter_summary(current_job_filters)
    summary = (
        f"scope={payload.get('scope_label') or payload.get('scope') or ''} | "
        f"jobs={int(payload.get('job_count') or 0)} | "
        f"topics={', '.join((payload.get('signals') or {}).get('dominant_topics') or [])}"
    )
    return {"payload": payload, "summary": summary, "html": render_roadmap_html(payload)}

