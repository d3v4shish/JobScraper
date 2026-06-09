#!/usr/bin/env python3
"""Background task helpers used by the request graph and workers."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Sequence

from .. import paths
from ..scraping import core
from ..storage import db
from ..storage import fs
from ..ai import roadmap
from .renderers import (
    html_shell,
    render_analytics_html,
    render_description_html,
    render_roadmap_html,
)


def initialize_database_task(db_path: Path) -> Dict[str, Any]:
    """Initialize and migrate the SQLite database off the GUI thread."""
    db.init_db(db_path)
    return {"db_path": str(db_path)}


def import_sources_task(db_path: Path, sources_path: Path) -> Dict[str, Any]:
    """Import the source JSON into SQLite from a worker thread."""
    db.init_db(db_path)
    return db.import_sources_report(db_path, sources_path, create_backup=True)


def preview_source_import_task(db_path: Path, sources_path: Path) -> Dict[str, Any]:
    """Preview a source JSON import from a worker thread."""
    return db.preview_source_import(db_path, sources_path)


def probe_watchlist_and_import_task(
    db_path: Path,
    watchlist_path: Path,
    sources_path: Path,
    report_path: Path,
) -> Dict[str, Any]:
    """Probe the candidate watchlist, promote verified sources, and import them."""
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
) -> Dict[str, Any]:
    """Export the current filtered job view without blocking the GUI thread."""
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
    )
    return {"count": count, "out_path": out_path}


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
    return {"jobs": list(jobs), "rows": rows, "grouped": group_by_company}


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
    summary = (
        f"scope={payload.get('scope_label') or payload.get('scope') or ''} | "
        f"jobs={int(payload.get('job_count') or 0)} | "
        f"topics={', '.join((payload.get('signals') or {}).get('dominant_topics') or [])}"
    )
    return {"payload": payload, "summary": summary, "html": render_roadmap_html(payload)}

