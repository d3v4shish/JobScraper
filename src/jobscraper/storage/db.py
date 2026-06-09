#!/usr/bin/env python3
"""
SQLite storage for the company job scraper.

The database is the runtime source of truth. A JSON source file can seed or
refresh source rows, but jobs, matches, stack labels, and scrape status live in
SQLite.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import hashlib
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .. import paths
from . import fs


DEFAULT_DB_PATH = paths.default_db_path()
SUPPORTED_ATS = {
    "greenhouse",
    "lever",
    "ashby",
    "recruitee",
    "personio",
    "google",
    "eightfold",
    "optiver",
    "drw",
    "gresearch",
    "smartrecruiters",
    "workday",
    "hackernews_hiring",
    "remoteok_api",
    "remotive_api",
    "weworkremotely_rss",
    "powertofly_search",
    "authenticjobs_wp",
    "dice_search",
    "remote_co_search",
    "justremote_search",
    "skipthedrive_search",
    "flexjobs_search",
    "jobs24x_search",
    "remotefront_search",
    "underdog_search",
    "microsoft_careers",
    "amazon_jobs",
    "apple_jobs",
    "oracle_careers",
    "ibm_careers",
    "uber_careers",
    "devsnap_search",
    "workable",
    "teamtailor",
    "bamboohr",
    "breezy_hr",
    "jazzhr",
    "icims",
    "jobvite",
    "oracle_taleo",
    "sap_successfactors",
    "ukg",
    "ultipro",
    "adp",
    "paylocity",
    "pinpoint",
    "comeet",
    "rippling",
    "yc_work_at_startup",
    "himalayas_search",
    "workingnomads_search",
    "nodesk_search",
    "jobspresso_search",
    "remote_rocketship_search",
    "arc_dev_search",
    "levels_fyi_jobs",
    "builtin_jobs",
    "climatebase_jobs",
    "arbeitnow_api",
    "naukri_search",
    "instahyre_search",
    "cutshort_search",
    "hirist_search",
    "foundit_search",
    "timesjobs_search",
    "ai_jobs_search",
    "ml_jobs_search",
    "data_jobs_search",
    "rust_jobs_search",
    "golangprojects_search",
    "python_jobs_search",
    "cybersecjobs_search",
    "otta_search",
    "welcome_to_the_jungle_search",
}
TOKEN_REQUIRED_ATS = {
    "greenhouse",
    "lever",
    "ashby",
    "recruitee",
    "personio",
    "hackernews_hiring",
}
URL_REQUIRED_ATS = {
    "workday",
    "google",
    "eightfold",
    "optiver",
    "drw",
    "gresearch",
    "remoteok_api",
    "remotive_api",
    "weworkremotely_rss",
    "powertofly_search",
    "authenticjobs_wp",
    "dice_search",
    "remote_co_search",
    "justremote_search",
    "skipthedrive_search",
    "flexjobs_search",
    "jobs24x_search",
    "remotefront_search",
    "underdog_search",
    "microsoft_careers",
    "amazon_jobs",
    "apple_jobs",
    "oracle_careers",
    "ibm_careers",
    "uber_careers",
    "devsnap_search",
    "workable",
    "teamtailor",
    "bamboohr",
    "breezy_hr",
    "jazzhr",
    "icims",
    "jobvite",
    "oracle_taleo",
    "sap_successfactors",
    "ukg",
    "ultipro",
    "adp",
    "paylocity",
    "pinpoint",
    "comeet",
    "rippling",
    "yc_work_at_startup",
    "himalayas_search",
    "workingnomads_search",
    "nodesk_search",
    "jobspresso_search",
    "remote_rocketship_search",
    "arc_dev_search",
    "levels_fyi_jobs",
    "builtin_jobs",
    "climatebase_jobs",
    "arbeitnow_api",
    "naukri_search",
    "instahyre_search",
    "cutshort_search",
    "hirist_search",
    "foundit_search",
    "timesjobs_search",
    "ai_jobs_search",
    "ml_jobs_search",
    "data_jobs_search",
    "rust_jobs_search",
    "golangprojects_search",
    "python_jobs_search",
    "cybersecjobs_search",
    "otta_search",
    "welcome_to_the_jungle_search",
}
_MIGRATED_DB_PATHS: set[str] = set()
_MIGRATION_LOCK = Lock()
_SOURCE_SUCCESS_STATUSES = {"direct_api", "browser_public", "success", "public_ok"}
_SOURCE_NEUTRAL_STATUSES = {"running", "disabled"}
_STACK_SUMMARY_CATEGORIES = ("language", "framework", "domain", "tool", "group")
_STACK_SUMMARY_CATEGORY_SQL = ", ".join(f"'{category}'" for category in _STACK_SUMMARY_CATEGORIES)
LOGGER = logging.getLogger(__name__)


class JobScraperConnection(sqlite3.Connection):
    """SQLite connection that closes when used as a context manager."""

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        try:
            return bool(super().__exit__(exc_type, exc, tb))
        finally:
            self.close()


def now_ts() -> int:
    """Return the current Unix timestamp in seconds for persisted scrape metadata."""
    return int(time.time())


def json_dumps(value: Any) -> str:
    """Serialize JSON deterministically for SQLite storage and cache keys."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


MAX_RAW_JSON_BYTES = 64 * 1024
RAW_STRING_LIMIT = 4096
RAW_SEQUENCE_LIMIT = 12
RAW_MAPPING_LIMIT = 80


def _json_size_bytes(value: str) -> int:
    return len(value.encode("utf-8"))


def _compact_raw_value(value: Any, *, depth: int = 0) -> Any:
    """Summarize oversized source payloads before they are persisted in SQLite."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if len(value) <= RAW_STRING_LIMIT:
            return value
        return value[:RAW_STRING_LIMIT] + "... [truncated]"
    if depth >= 3:
        if isinstance(value, dict):
            return {"_type": "dict", "_keys": [str(key) for key in list(value.keys())[:RAW_SEQUENCE_LIMIT]]}
        if isinstance(value, list):
            return {"_type": "list", "_count": len(value)}
        return str(value)
    if isinstance(value, dict):
        compact: Dict[str, Any] = {}
        items = list(value.items())
        for key, child in items[:RAW_MAPPING_LIMIT]:
            compact[str(key)] = _compact_raw_value(child, depth=depth + 1)
        if len(items) > RAW_MAPPING_LIMIT:
            compact["_truncated_keys"] = len(items) - RAW_MAPPING_LIMIT
        return compact
    if isinstance(value, (list, tuple)):
        compact_list = [_compact_raw_value(child, depth=depth + 1) for child in list(value)[:RAW_SEQUENCE_LIMIT]]
        if len(value) > RAW_SEQUENCE_LIMIT:
            compact_list.append({"_truncated_items": len(value) - RAW_SEQUENCE_LIMIT})
        return compact_list
    return str(value)


def raw_json_dumps(value: Any) -> str:
    """Serialize source raw payloads with a production storage cap."""
    raw_json = json_dumps(value or {})
    original_bytes = _json_size_bytes(raw_json)
    if original_bytes <= MAX_RAW_JSON_BYTES:
        return raw_json
    compact_value = _compact_raw_value(value)
    if isinstance(compact_value, dict):
        compact_value["_jobscraper_raw_truncated"] = {
            "original_bytes": original_bytes,
            "max_bytes": MAX_RAW_JSON_BYTES,
            "policy": "normalized fields and jobs.text are authoritative; oversized raw payload was summarized",
        }
    else:
        compact_value = {
            "value": compact_value,
            "_jobscraper_raw_truncated": {
                "original_bytes": original_bytes,
                "max_bytes": MAX_RAW_JSON_BYTES,
                "policy": "normalized fields and jobs.text are authoritative; oversized raw payload was summarized",
            },
        }
    compact_json = json_dumps(compact_value)
    if _json_size_bytes(compact_json) <= MAX_RAW_JSON_BYTES:
        return compact_json
    return json_dumps(
        {
            "_jobscraper_raw_truncated": {
                "original_bytes": original_bytes,
                "max_bytes": MAX_RAW_JSON_BYTES,
                "policy": "raw payload exceeded storage cap after summarization",
            }
        }
    )


def json_loads(value: Optional[str], default: Any) -> Any:
    """Parse a JSON column value and fall back cleanly on empty or invalid input."""
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def connect(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open a SQLite connection with row access and parent-directory creation.

    This performs filesystem work and should be called inside worker threads for
    non-trivial query paths, not from high-frequency GUI callbacks.
    """
    path = Path(db_path)
    if path.parent and str(path.parent) not in ("", "."):
        fs.ensure_dir(path.parent)
    conn = sqlite3.connect(str(path), timeout=30, factory=JobScraperConnection)
    fs.chmod_best_effort(path, fs.PRIVATE_FILE_MODE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA wal_autocheckpoint = 1000")
    conn.execute("PRAGMA journal_size_limit = 67108864")
    conn.execute("PRAGMA cache_size = -32768")
    conn.execute("PRAGMA mmap_size = 268435456")
    try:
        conn.execute("PRAGMA trusted_schema = OFF")
    except sqlite3.DatabaseError:
        pass
    return conn


def _safe_backup_name(path: Path, reason: str) -> str:
    stem = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in path.stem) or "jobs"
    suffix = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in reason.strip().lower()) or "manual"
    return f"{stem}_{suffix}_{time.strftime('%Y%m%d_%H%M%S')}.sqlite"


def prune_backups(*, keep: int = 8) -> None:
    """Keep the most recent SQLite backups and remove older app-created backups."""
    backup_dir = paths.backups_dir()
    if not backup_dir.exists():
        return
    backups = sorted(
        backup_dir.glob("*.sqlite"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for stale in backups[max(0, keep):]:
        try:
            stale.unlink()
        except OSError as exc:
            LOGGER.warning("backup_prune_failed path=%s error=%s", stale, exc)


def backup_database(db_path: Path | str, *, reason: str, keep: int = 8) -> Optional[Path]:
    """Create a consistent SQLite backup before risky operations."""
    source_path = Path(db_path)
    if not source_path.exists():
        return None
    backup_dir = fs.ensure_dir(paths.backups_dir())
    backup_path = backup_dir / _safe_backup_name(source_path, reason)
    src = sqlite3.connect(str(source_path), timeout=30)
    dst = sqlite3.connect(str(backup_path), timeout=30)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    fs.chmod_best_effort(backup_path, fs.PRIVATE_FILE_MODE)
    prune_backups(keep=keep)
    return backup_path


def _connection_db_key(conn: sqlite3.Connection) -> str:
    """Return a stable resolved path key for the primary SQLite database."""
    row = conn.execute("PRAGMA database_list").fetchone()
    if not row or len(row) < 3 or not row[2]:
        return ""
    try:
        return str(Path(str(row[2])).resolve())
    except OSError:
        return str(row[2])


def init_db(db_path: Path | str = DEFAULT_DB_PATH) -> None:
    """Create or migrate the SQLite schema for the jobs workspace."""
    with connect(db_path) as conn:
        migrate_connection(conn)


def migrate_connection(conn: sqlite3.Connection) -> None:
    """Apply schema creation and additive migrations to an open connection."""
    db_key = _connection_db_key(conn)
    with _MIGRATION_LOCK:
        if db_key and db_key in _MIGRATED_DB_PATHS:
            return
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS sources (
            id INTEGER PRIMARY KEY,
            company TEXT NOT NULL,
            ats TEXT NOT NULL,
            token TEXT,
            url TEXT,
            enabled INTEGER NOT NULL DEFAULT 1,
            tags_json TEXT NOT NULL DEFAULT '[]',
            notes TEXT NOT NULL DEFAULT '',
            browser_required INTEGER NOT NULL DEFAULT 0,
            wait_selector TEXT NOT NULL DEFAULT '',
            discovery_notes TEXT NOT NULL DEFAULT '',
            portal TEXT NOT NULL DEFAULT '',
            entry_kind TEXT NOT NULL DEFAULT '',
            auth_mode TEXT NOT NULL DEFAULT 'public',
            browser_backend TEXT NOT NULL DEFAULT 'auto',
            profile_browser TEXT NOT NULL DEFAULT '',
            profile_name TEXT NOT NULL DEFAULT '',
            entry_url TEXT NOT NULL DEFAULT '',
            entry_url_override INTEGER NOT NULL DEFAULT 0,
            search_terms_json TEXT NOT NULL DEFAULT '[]',
            locations_json TEXT NOT NULL DEFAULT '[]',
            session_status TEXT NOT NULL DEFAULT '',
            session_detail TEXT NOT NULL DEFAULT '',
            session_checked_at INTEGER,
            last_scraped_at INTEGER,
            last_status TEXT,
            last_error TEXT,
            success_count INTEGER NOT NULL DEFAULT 0,
            failure_count INTEGER NOT NULL DEFAULT 0,
            last_success_at INTEGER,
            last_failure_at INTEGER,
            last_duration_ms INTEGER
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_sources_identity
        ON sources(company, ats, ifnull(token, ''), ifnull(url, ''));

        CREATE INDEX IF NOT EXISTS idx_sources_portal_enabled
        ON sources(portal, enabled, company COLLATE NOCASE);

        CREATE INDEX IF NOT EXISTS idx_sources_ats_enabled
        ON sources(ats, enabled, company COLLATE NOCASE);

        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY,
            job_key TEXT NOT NULL UNIQUE,
            source_id INTEGER NOT NULL,
            company TEXT NOT NULL,
            ats TEXT NOT NULL,
            source_job_id TEXT NOT NULL,
            title TEXT NOT NULL,
            location TEXT NOT NULL DEFAULT '',
            department TEXT NOT NULL DEFAULT '',
            employment_type TEXT NOT NULL DEFAULT '',
            remote INTEGER NOT NULL DEFAULT 0,
            job_url TEXT NOT NULL DEFAULT '',
            apply_url TEXT NOT NULL DEFAULT '',
            published_at TEXT,
            updated_at TEXT,
            first_seen_at INTEGER NOT NULL,
            last_seen_at INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            text TEXT NOT NULL DEFAULT '',
            raw_json TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY(source_id) REFERENCES sources(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS job_matches (
            job_id INTEGER PRIMARY KEY,
            matched_required_words_json TEXT NOT NULL DEFAULT '[]',
            matched_include_words_json TEXT NOT NULL DEFAULT '[]',
            matched_include_group_json TEXT NOT NULL DEFAULT '[]',
            matched_builtin_groups_json TEXT NOT NULL DEFAULT '[]',
            location_modes_json TEXT NOT NULL DEFAULT '[]',
            interest_tags_json TEXT NOT NULL DEFAULT '[]',
            passes_filter INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS job_stack (
            job_id INTEGER NOT NULL,
            category TEXT NOT NULL,
            name TEXT NOT NULL,
            PRIMARY KEY(job_id, category, name),
            FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS job_stack_summary (
            job_id INTEGER PRIMARY KEY,
            detected_stack TEXT NOT NULL DEFAULT '',
            FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS question_sets (
            id INTEGER PRIMARY KEY,
            scope_type TEXT NOT NULL,
            scope_key TEXT NOT NULL,
            engine TEXT NOT NULL,
            version TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            questions_json TEXT NOT NULL DEFAULT '[]',
            generated_at INTEGER NOT NULL,
            UNIQUE(scope_type, scope_key, engine, version)
        );

        CREATE TABLE IF NOT EXISTS question_entries (
            id INTEGER PRIMARY KEY,
            question_set_id INTEGER NOT NULL,
            ordinal INTEGER NOT NULL,
            track TEXT NOT NULL DEFAULT '',
            level TEXT NOT NULL DEFAULT '',
            category TEXT NOT NULL DEFAULT '',
            topic TEXT NOT NULL DEFAULT '',
            question TEXT NOT NULL DEFAULT '',
            signals_json TEXT NOT NULL DEFAULT '[]',
            deep_dive_html TEXT NOT NULL DEFAULT '',
            glossary_json TEXT NOT NULL DEFAULT '[]',
            pros_json TEXT NOT NULL DEFAULT '[]',
            cons_json TEXT NOT NULL DEFAULT '[]',
            visual_svg TEXT NOT NULL DEFAULT '',
            mindmap_svg TEXT NOT NULL DEFAULT '',
            code_walkthrough_html TEXT NOT NULL DEFAULT '',
            FOREIGN KEY(question_set_id) REFERENCES question_sets(id) ON DELETE CASCADE,
            UNIQUE(question_set_id, ordinal)
        );

        CREATE INDEX IF NOT EXISTS idx_jobs_job_key ON jobs(job_key);
        CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company);
        CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
        CREATE INDEX IF NOT EXISTS idx_jobs_remote ON jobs(remote);
        CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs(source_id);
        CREATE INDEX IF NOT EXISTS idx_jobs_status_company_seen ON jobs(status, company, last_seen_at DESC);
        CREATE INDEX IF NOT EXISTS idx_jobs_source_status_seen ON jobs(source_id, status, last_seen_at DESC);
        CREATE INDEX IF NOT EXISTS idx_jobs_last_seen ON jobs(last_seen_at DESC);
        CREATE INDEX IF NOT EXISTS idx_jobs_status_seen_company_title
        ON jobs(status, last_seen_at DESC, company COLLATE NOCASE, title COLLATE NOCASE);
        CREATE INDEX IF NOT EXISTS idx_job_stack_name ON job_stack(name);
        CREATE INDEX IF NOT EXISTS idx_job_stack_category_name ON job_stack(category, name);
        CREATE INDEX IF NOT EXISTS idx_job_stack_job_category_name ON job_stack(job_id, category, name);
        CREATE INDEX IF NOT EXISTS idx_job_stack_summary_stack ON job_stack_summary(detected_stack);
        CREATE INDEX IF NOT EXISTS idx_job_matches_passes_filter ON job_matches(passes_filter);
        CREATE INDEX IF NOT EXISTS idx_job_matches_job_filter ON job_matches(job_id, passes_filter);
        CREATE INDEX IF NOT EXISTS idx_job_matches_filter_job ON job_matches(passes_filter, job_id);
        CREATE INDEX IF NOT EXISTS idx_question_sets_scope
        ON question_sets(scope_type, scope_key, engine, version);
        CREATE INDEX IF NOT EXISTS idx_question_entries_set
        ON question_entries(question_set_id, ordinal);
        CREATE INDEX IF NOT EXISTS idx_question_entries_track
        ON question_entries(question_set_id, track, level, topic);
        """
    )
    ensure_column(conn, "job_matches", "location_modes_json", "TEXT NOT NULL DEFAULT '[]'")
    ensure_column(conn, "job_matches", "interest_tags_json", "TEXT NOT NULL DEFAULT '[]'")
    ensure_column(conn, "job_matches", "matched_builtin_groups_json", "TEXT NOT NULL DEFAULT '[]'")
    ensure_column(conn, "sources", "browser_required", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(conn, "sources", "wait_selector", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "sources", "discovery_notes", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "sources", "portal", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "sources", "entry_kind", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "sources", "auth_mode", "TEXT NOT NULL DEFAULT 'public'")
    ensure_column(conn, "sources", "browser_backend", "TEXT NOT NULL DEFAULT 'auto'")
    ensure_column(conn, "sources", "profile_browser", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "sources", "profile_name", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "sources", "entry_url", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "sources", "entry_url_override", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(conn, "sources", "search_terms_json", "TEXT NOT NULL DEFAULT '[]'")
    ensure_column(conn, "sources", "locations_json", "TEXT NOT NULL DEFAULT '[]'")
    ensure_column(conn, "sources", "session_status", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "sources", "session_detail", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "sources", "session_checked_at", "INTEGER")
    ensure_column(conn, "sources", "success_count", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(conn, "sources", "failure_count", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(conn, "sources", "last_success_at", "INTEGER")
    ensure_column(conn, "sources", "last_failure_at", "INTEGER")
    ensure_column(conn, "sources", "last_duration_ms", "INTEGER")
    _ensure_job_stack_summary(conn)
    try:
        conn.execute("PRAGMA optimize")
    except sqlite3.Error as exc:
        LOGGER.debug("sqlite_optimize_skipped error=%s", exc)
    conn.commit()
    with _MIGRATION_LOCK:
        if db_key:
            _MIGRATED_DB_PATHS.add(db_key)


def ensure_column(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    definition: str,
) -> None:
    columns = {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _refresh_job_stack_summary(conn: sqlite3.Connection, job_id: int) -> None:
    """Refresh the cached display stack for one job from normalized stack rows."""
    row = conn.execute(
        f"""
        SELECT COALESCE(GROUP_CONCAT(name, ', '), '') AS detected_stack
        FROM (
            SELECT name
            FROM job_stack
            WHERE job_id = ?
              AND category IN ({_STACK_SUMMARY_CATEGORY_SQL})
            GROUP BY name
            ORDER BY name COLLATE NOCASE
        )
        """,
        (int(job_id),),
    ).fetchone()
    detected_stack = str(row["detected_stack"] or "") if row else ""
    conn.execute(
        """
        INSERT INTO job_stack_summary (job_id, detected_stack)
        VALUES (?, ?)
        ON CONFLICT(job_id) DO UPDATE SET
            detected_stack = excluded.detected_stack
        """,
        (int(job_id), detected_stack),
    )


def _rebuild_job_stack_summary(conn: sqlite3.Connection) -> None:
    """Rebuild the stack display cache for existing databases."""
    conn.execute("DELETE FROM job_stack_summary")
    conn.execute(
        f"""
        INSERT INTO job_stack_summary (job_id, detected_stack)
        SELECT
            j.id,
            COALESCE(stack.detected_stack, '') AS detected_stack
        FROM jobs j
        LEFT JOIN (
            SELECT job_id, GROUP_CONCAT(name, ', ') AS detected_stack
            FROM (
                SELECT job_id, name
                FROM job_stack
                WHERE category IN ({_STACK_SUMMARY_CATEGORY_SQL})
                GROUP BY job_id, name
                ORDER BY job_id, name COLLATE NOCASE
            )
            GROUP BY job_id
        ) stack ON stack.job_id = j.id
        """
    )


def _ensure_job_stack_summary(conn: sqlite3.Connection) -> None:
    """Ensure the stack display cache has one row per job."""
    row = conn.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM jobs) AS job_count,
            (SELECT COUNT(*) FROM job_stack_summary) AS summary_count
        """
    ).fetchone()
    if not row:
        return
    if int(row["job_count"] or 0) != int(row["summary_count"] or 0):
        _rebuild_job_stack_summary(conn)


def _clean_source(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize one raw source row before it is inserted into SQLite."""
    company = str(raw.get("company") or "").strip()
    ats = str(raw.get("ats") or "").strip().lower()
    token = str(raw.get("token") or "").strip() or None
    url = str(raw.get("url") or "").strip() or None
    entry_url = str(raw.get("entry_url") or "").strip() or None
    enabled = 1 if raw.get("enabled", True) else 0
    tags = raw.get("tags") or []
    notes = str(raw.get("notes") or "").strip()
    browser_required = 1 if raw.get("browser_required", False) else 0
    wait_selector = str(raw.get("wait_selector") or "").strip()
    discovery_notes = str(raw.get("discovery_notes") or "").strip()
    portal = str(raw.get("portal") or "").strip().lower()
    entry_kind = str(raw.get("entry_kind") or "").strip().lower()
    auth_mode = str(raw.get("auth_mode") or "public").strip().lower() or "public"
    browser_backend = str(raw.get("browser_backend") or "auto").strip().lower() or "auto"
    profile_browser = str(raw.get("profile_browser") or "").strip().lower()
    profile_name = str(raw.get("profile_name") or "").strip()
    search_terms = raw.get("search_terms") or []
    locations = raw.get("locations") or []
    session_status = str(raw.get("session_status") or "").strip()
    session_detail = str(raw.get("session_detail") or "").strip()
    session_checked_at = raw.get("session_checked_at")
    entry_url_override = 1 if raw.get("entry_url_override", False) else 0

    if not company:
        raise ValueError("source is missing company")
    if ats not in SUPPORTED_ATS:
        raise ValueError(f"unsupported ats for {company}: {ats!r}")
    if auth_mode != "public":
        raise ValueError(f"source uses unsupported auth_mode for public release: {company}: {auth_mode}")
    if not token and not url and not entry_url:
        raise ValueError(f"source is missing token/url/entry_url: {company}")
    if ats in TOKEN_REQUIRED_ATS and not token:
        raise ValueError(f"source is missing token for {company}: {ats}")
    if ats in URL_REQUIRED_ATS and not (url or entry_url):
        raise ValueError(f"source is missing url/entry_url for {company}: {ats}")
    if not isinstance(tags, list):
        tags = [str(tags)]
    if not isinstance(search_terms, list):
        search_terms = [str(search_terms)]
    if not isinstance(locations, list):
        locations = [str(locations)]
    return {
        "company": company,
        "ats": ats,
        "token": token,
        "url": url,
        "enabled": enabled,
        "tags_json": json_dumps(tags),
        "notes": notes,
        "browser_required": browser_required,
        "wait_selector": wait_selector,
        "discovery_notes": discovery_notes,
        "portal": portal,
        "entry_kind": entry_kind,
        "auth_mode": auth_mode,
        "browser_backend": browser_backend,
        "profile_browser": profile_browser,
        "profile_name": profile_name,
        "entry_url": entry_url or (url or ""),
        "entry_url_override": entry_url_override,
        "search_terms_json": json_dumps([str(item).strip() for item in search_terms if str(item).strip()]),
        "locations_json": json_dumps([str(item).strip() for item in locations if str(item).strip()]),
        "session_status": session_status,
        "session_detail": session_detail,
        "session_checked_at": int(session_checked_at) if session_checked_at else None,
    }


def _load_source_rows(sources_path: Path | str) -> tuple[Path, List[Dict[str, Any]], set[tuple[str, str, str, str]]]:
    """Load, normalize, and validate a source JSON file before DB mutation."""
    path = Path(sources_path)
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, list):
        raise ValueError(f"{path} must contain a JSON list")

    invalid_count = sum(1 for item in payload if not isinstance(item, dict))
    if invalid_count:
        raise ValueError(f"{path} contains {invalid_count} non-object source rows")
    rows = [_clean_source(item) for item in payload]
    seen_identities: Dict[tuple[str, str, str, str], int] = {}
    for index, row in enumerate(rows, start=1):
        identity = (
            str(row["company"]).casefold(),
            str(row["ats"]).casefold(),
            str(row.get("token") or ""),
            str(row.get("url") or ""),
        )
        prior = seen_identities.get(identity)
        if prior is not None:
            raise ValueError(
                f"duplicate source identity in {path} for {row['company']} "
                f"(rows {prior} and {index})"
            )
        seen_identities[identity] = index
    imported_identities = {
        (
            str(row["company"]),
            str(row["ats"]),
            str(row.get("token") or ""),
            str(row.get("url") or ""),
        )
        for row in rows
    }
    return path, rows, imported_identities


def preview_source_import(
    db_path: Path | str = DEFAULT_DB_PATH,
    sources_path: Path | str = paths.default_sources_path(),
) -> Dict[str, Any]:
    """Return a non-mutating source import summary for operator confirmation."""
    path, rows, imported_identities = _load_source_rows(sources_path)
    with connect(db_path) as conn:
        migrate_connection(conn)
        existing_rows = conn.execute(
            "SELECT company, ats, ifnull(token, '') AS token, ifnull(url, '') AS url FROM sources"
        ).fetchall()
    existing_identities = {
        (
            str(row["company"]),
            str(row["ats"]),
            str(row["token"] or ""),
            str(row["url"] or ""),
        )
        for row in existing_rows
    }
    stale = existing_identities - imported_identities
    new = imported_identities - existing_identities
    return {
        "path": str(path),
        "total": len(rows),
        "new": len(new),
        "updated": len(imported_identities & existing_identities),
        "stale_disabled": len(stale),
        "enabled": sum(1 for row in rows if row.get("enabled")),
        "disabled": sum(1 for row in rows if not row.get("enabled")),
    }


def import_sources_report(
    db_path: Path | str = DEFAULT_DB_PATH,
    sources_path: Path | str = paths.default_sources_path(),
    *,
    create_backup: bool = True,
) -> Dict[str, Any]:
    """Import source rows transactionally and return an audit summary."""
    path, rows, imported_identities = _load_source_rows(sources_path)
    preview = preview_source_import(db_path, path)
    backup_path = backup_database(db_path, reason="before_source_import") if create_backup else None
    with connect(db_path) as conn:
        migrate_connection(conn)
        conn.execute("BEGIN IMMEDIATE")
        for row in rows:
            existing = conn.execute(
                """
                SELECT id
                FROM sources
                WHERE company = ?
                  AND ats = ?
                  AND ifnull(token, '') = ifnull(?, '')
                  AND ifnull(url, '') = ifnull(?, '')
                """,
                (
                    row["company"],
                    row["ats"],
                    row["token"],
                    row["url"],
                ),
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE sources
                    SET enabled = ?,
                        tags_json = ?,
                        notes = ?,
                        browser_required = ?,
                        wait_selector = ?,
                        discovery_notes = ?,
                        portal = ?,
                        entry_kind = ?,
                        auth_mode = ?,
                        browser_backend = ?,
                        profile_browser = ?,
                        profile_name = ?,
                        entry_url = CASE WHEN entry_url_override = 1 THEN entry_url ELSE ? END,
                        entry_url_override = CASE WHEN entry_url_override = 1 THEN 1 ELSE ? END,
                        search_terms_json = ?,
                        locations_json = ?,
                        session_status = CASE WHEN session_status = '' THEN ? ELSE session_status END,
                        session_detail = CASE WHEN session_detail = '' THEN ? ELSE session_detail END,
                        session_checked_at = COALESCE(session_checked_at, ?),
                        last_status = CASE
                            WHEN ? = 0 THEN 'disabled'
                            WHEN last_status = 'disabled' THEN NULL
                            ELSE last_status
                        END,
                        last_error = CASE
                            WHEN ? = 0 THEN ''
                            WHEN last_status = 'disabled' THEN ''
                            ELSE last_error
                        END
                    WHERE id = ?
                    """,
                    (
                        row["enabled"],
                        row["tags_json"],
                        row["notes"],
                        row["browser_required"],
                        row["wait_selector"],
                        row["discovery_notes"],
                        row["portal"],
                        row["entry_kind"],
                        row["auth_mode"],
                        row["browser_backend"],
                        row["profile_browser"],
                        row["profile_name"],
                        row["entry_url"],
                        row["entry_url_override"],
                        row["search_terms_json"],
                        row["locations_json"],
                        row["session_status"],
                        row["session_detail"],
                        row["session_checked_at"],
                        row["enabled"],
                        row["enabled"],
                        int(existing["id"]),
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO sources (
                        company, ats, token, url, enabled, tags_json, notes,
                        browser_required, wait_selector, discovery_notes,
                        portal, entry_kind, auth_mode, browser_backend, profile_browser, profile_name,
                        entry_url, entry_url_override, search_terms_json, locations_json, session_status, session_detail, session_checked_at,
                        last_status, last_error
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["company"],
                        row["ats"],
                        row["token"],
                        row["url"],
                        row["enabled"],
                        row["tags_json"],
                        row["notes"],
                        row["browser_required"],
                        row["wait_selector"],
                        row["discovery_notes"],
                        row["portal"],
                        row["entry_kind"],
                        row["auth_mode"],
                        row["browser_backend"],
                        row["profile_browser"],
                        row["profile_name"],
                        row["entry_url"],
                        row["entry_url_override"],
                        row["search_terms_json"],
                        row["locations_json"],
                        row["session_status"],
                        row["session_detail"],
                        row["session_checked_at"],
                        "disabled" if not row["enabled"] else None,
                        "",
                    ),
                )
        stale_rows = conn.execute(
            """
            SELECT id, company, ats, ifnull(token, '') AS token, ifnull(url, '') AS url
            FROM sources
            """
        ).fetchall()
        for stale in stale_rows:
            identity = (
                str(stale["company"]),
                str(stale["ats"]),
                str(stale["token"] or ""),
                str(stale["url"] or ""),
            )
            if identity in imported_identities:
                continue
            conn.execute(
                """
                UPDATE sources
                SET enabled = 0,
                    last_status = 'disabled',
                    last_error = ''
                WHERE id = ?
                """,
                (int(stale["id"]),),
            )
        conn.commit()
    report = dict(preview)
    report["count"] = len(rows)
    report["backup_path"] = str(backup_path or "")
    return report


def import_sources(
    db_path: Path | str = DEFAULT_DB_PATH,
    sources_path: Path | str = paths.default_sources_path(),
) -> int:
    return int(import_sources_report(db_path, sources_path).get("count") or 0)


def has_sources(db_path: Path | str = DEFAULT_DB_PATH) -> bool:
    """Return whether the sources table currently has any configured rows."""
    with connect(db_path) as conn:
        migrate_connection(conn)
        row = conn.execute("SELECT COUNT(*) AS c FROM sources").fetchone()
        return bool(row and row["c"])


def list_sources(
    db_path: Path | str = DEFAULT_DB_PATH,
    *,
    enabled_only: bool = False,
) -> List[Dict[str, Any]]:
    with connect(db_path) as conn:
        migrate_connection(conn)
        return list_sources_conn(conn, enabled_only=enabled_only)


def list_sources_conn(
    conn: sqlite3.Connection,
    *,
    enabled_only: bool = False,
) -> List[Dict[str, Any]]:
    where = "WHERE enabled = 1" if enabled_only else ""
    rows = conn.execute(
        f"""
        SELECT
            s.*,
            COUNT(j.id) AS job_count,
            SUM(CASE WHEN j.status = 'open' THEN 1 ELSE 0 END) AS open_count,
            SUM(CASE WHEN j.status = 'closed' THEN 1 ELSE 0 END) AS closed_count,
            SUM(CASE WHEN jm.passes_filter = 1 AND j.status = 'open' THEN 1 ELSE 0 END) AS matching_count
        FROM sources s
        LEFT JOIN jobs j ON j.source_id = s.id
        LEFT JOIN job_matches jm ON jm.job_id = j.id
        {where}
        GROUP BY s.id
        ORDER BY s.company COLLATE NOCASE
        """
    ).fetchall()
    out: List[Dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["enabled"] = bool(item["enabled"])
        item["browser_required"] = bool(item.get("browser_required"))
        item["tags"] = json_loads(item.pop("tags_json", "[]"), [])
        item["search_terms"] = json_loads(item.pop("search_terms_json", "[]"), [])
        item["locations"] = json_loads(item.pop("locations_json", "[]"), [])
        item["job_count"] = int(item.get("job_count") or 0)
        item["open_count"] = int(item.get("open_count") or 0)
        item["closed_count"] = int(item.get("closed_count") or 0)
        item["matching_count"] = int(item.get("matching_count") or 0)
        item["source_health_group"] = source_health_group(item)
        item["source_quality_score"] = source_quality_score(item)
        out.append(item)
    return out


def source_health_group(source: Dict[str, Any]) -> str:
    """Classify a source into the operator-facing health buckets."""
    if not bool(source.get("enabled")):
        return "disabled"
    status = str(source.get("last_status") or "").strip().lower()
    if status in {"blocked_skipped", "manual_review"}:
        return "blocked"
    if status == "parser_issue":
        return "parser failure"
    if status == "error":
        return "parser failure"
    if int(source.get("success_count") or 0) > 0:
        return "healthy"
    return "new"


def source_quality_score(source: Dict[str, Any]) -> int:
    """Compute a compact 0-100 score from scrape reliability and useful yield."""
    success_count = int(source.get("success_count") or 0)
    failure_count = int(source.get("failure_count") or 0)
    open_count = int(source.get("open_count") or 0)
    matching_count = int(source.get("matching_count") or 0)
    closed_count = int(source.get("closed_count") or 0)
    group = source_health_group(source)
    score = 40 + min(success_count, 6) * 7 + min(open_count, 30) + min(matching_count, 20) * 2
    score -= min(failure_count, 8) * 8
    score -= min(closed_count, 50) // 4
    if group in {"blocked", "parser failure"}:
        score -= 25
    if group == "disabled":
        score -= 35
    return max(0, min(100, int(score)))


def update_source_status(
    conn: sqlite3.Connection,
    source_id: int,
    *,
    status: str,
    error: str = "",
    scraped_at: Optional[int] = None,
    duration_ms: Optional[int] = None,
) -> None:
    timestamp = scraped_at if scraped_at is not None else now_ts()
    normalized_status = str(status or "").strip().lower()
    is_success = normalized_status in _SOURCE_SUCCESS_STATUSES
    is_failure = normalized_status not in _SOURCE_SUCCESS_STATUSES and normalized_status not in _SOURCE_NEUTRAL_STATUSES
    conn.execute(
        """
        UPDATE sources
        SET last_scraped_at = ?,
            last_status = ?,
            last_error = ?,
            success_count = success_count + ?,
            failure_count = failure_count + ?,
            last_success_at = CASE WHEN ? THEN ? ELSE last_success_at END,
            last_failure_at = CASE WHEN ? THEN ? ELSE last_failure_at END,
            last_duration_ms = CASE WHEN ? IS NULL THEN last_duration_ms ELSE ? END
        WHERE id = ?
        """,
        (
            timestamp,
            status,
            error,
            1 if is_success else 0,
            1 if is_failure else 0,
            1 if is_success else 0,
            timestamp,
            1 if is_failure else 0,
            timestamp,
            duration_ms,
            duration_ms,
            source_id,
        ),
    )


def update_source_session(
    conn: sqlite3.Connection,
    source_id: int,
    *,
    session_status: str,
    session_detail: str = "",
    checked_at: Optional[int] = None,
) -> None:
    conn.execute(
        """
        UPDATE sources
        SET session_status = ?,
            session_detail = ?,
            session_checked_at = ?
        WHERE id = ?
        """,
        (
            session_status,
            session_detail,
            checked_at if checked_at is not None else now_ts(),
            source_id,
        ),
    )


def upsert_job(
    conn: sqlite3.Connection,
    source: Dict[str, Any],
    job: Dict[str, Any],
    match: Dict[str, Any],
    stack: Dict[str, List[str]],
    *,
    seen_at: Optional[int] = None,
) -> int:
    seen = seen_at if seen_at is not None else now_ts()
    job_key = str(job["job_key"])
    raw_json = raw_json_dumps(job.get("raw", {}))

    conn.execute(
        """
        INSERT INTO jobs (
            job_key, source_id, company, ats, source_job_id, title, location,
            department, employment_type, remote, job_url, apply_url,
            published_at, updated_at, first_seen_at, last_seen_at, status,
            text, raw_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)
        ON CONFLICT(job_key) DO UPDATE SET
            source_id = excluded.source_id,
            company = excluded.company,
            ats = excluded.ats,
            source_job_id = excluded.source_job_id,
            title = excluded.title,
            location = excluded.location,
            department = excluded.department,
            employment_type = excluded.employment_type,
            remote = excluded.remote,
            job_url = excluded.job_url,
            apply_url = excluded.apply_url,
            published_at = excluded.published_at,
            updated_at = excluded.updated_at,
            last_seen_at = excluded.last_seen_at,
            status = 'open',
            text = excluded.text,
            raw_json = excluded.raw_json
        """,
        (
            job_key,
            int(source["id"]),
            job.get("company") or source["company"],
            job.get("ats") or source["ats"],
            str(job.get("source_job_id") or ""),
            str(job.get("title") or ""),
            str(job.get("location") or ""),
            str(job.get("department") or ""),
            str(job.get("employment_type") or ""),
            1 if job.get("remote") else 0,
            str(job.get("job_url") or ""),
            str(job.get("apply_url") or ""),
            job.get("published_at"),
            job.get("updated_at"),
            seen,
            seen,
            str(job.get("text") or ""),
            raw_json,
        ),
    )
    row = conn.execute("SELECT id FROM jobs WHERE job_key = ?", (job_key,)).fetchone()
    if not row:
        raise RuntimeError(f"failed to upsert job {job_key}")
    job_id = int(row["id"])

    conn.execute(
        """
        INSERT INTO job_matches (
            job_id, matched_required_words_json, matched_include_words_json,
            matched_include_group_json, matched_builtin_groups_json,
            location_modes_json, interest_tags_json, passes_filter
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(job_id) DO UPDATE SET
            matched_required_words_json = excluded.matched_required_words_json,
            matched_include_words_json = excluded.matched_include_words_json,
            matched_include_group_json = excluded.matched_include_group_json,
            matched_builtin_groups_json = excluded.matched_builtin_groups_json,
            location_modes_json = excluded.location_modes_json,
            interest_tags_json = excluded.interest_tags_json,
            passes_filter = excluded.passes_filter
        """,
        (
            job_id,
            json_dumps(match.get("matched_required_words", [])),
            json_dumps(match.get("matched_include_words", [])),
            json_dumps(match.get("matched_include_group", [])),
            json_dumps(match.get("matched_builtin_groups", [])),
            json_dumps(match.get("location_modes", [])),
            json_dumps(match.get("interest_tags", [])),
            1 if match.get("passes_filter") else 0,
        ),
    )

    conn.execute("DELETE FROM job_stack WHERE job_id = ?", (job_id,))
    for category, names in (
        ("language", stack.get("languages", [])),
        ("framework", stack.get("frameworks", [])),
        ("domain", stack.get("domains", [])),
        ("tool", stack.get("tools", [])),
        ("group", stack.get("groups", [])),
        ("location", match.get("location_modes", [])),
    ):
        for name in names:
            conn.execute(
                """
                INSERT OR IGNORE INTO job_stack (job_id, category, name)
                VALUES (?, ?, ?)
                """,
                (job_id, category, name),
            )

    _refresh_job_stack_summary(conn, job_id)
    return job_id


def mark_missing_jobs_closed(
    conn: sqlite3.Connection,
    source_id: int,
    seen_job_keys: Iterable[str],
    *,
    closed_at: Optional[int] = None,
) -> int:
    seen = set(seen_job_keys)
    closed = 0
    rows = conn.execute(
        "SELECT id, job_key FROM jobs WHERE source_id = ? AND status = 'open'",
        (source_id,),
    ).fetchall()
    for row in rows:
        if row["job_key"] in seen:
            continue
        conn.execute(
            """
            UPDATE jobs
            SET status = 'closed', updated_at = COALESCE(updated_at, ?)
            WHERE id = ?
            """,
            (str(closed_at if closed_at is not None else now_ts()), int(row["id"])),
        )
        closed += 1
    return closed


def _job_where(
    *,
    matching_only: bool,
    open_only: bool,
    search: str,
    stack: str,
    companies: Optional[Sequence[str]] = None,
    portal: str = "",
    source_id: int = 0,
    source_tag: str = "",
    hn_mode: str = "",
    founding_only: bool = False,
) -> tuple[str, List[Any]]:
    clauses: List[str] = []
    params: List[Any] = []

    if matching_only:
        clauses.append("jm.passes_filter = 1")
    if open_only:
        clauses.append("j.status = 'open'")
    if search.strip():
        needle = f"%{search.strip().lower()}%"
        clauses.append(
            """
            (
                lower(j.company) LIKE ?
                OR lower(j.title) LIKE ?
                OR lower(j.location) LIKE ?
                OR lower(j.department) LIKE ?
                OR lower(j.text) LIKE ?
            )
            """
        )
        params.extend([needle, needle, needle, needle, needle])
    if stack.strip():
        clauses.append(
            """
            EXISTS (
                SELECT 1
                FROM job_stack js_filter
                WHERE js_filter.job_id = j.id
                  AND js_filter.name = ?
            )
            """
        )
        params.append(stack.strip())
    selected_companies = [str(company).strip() for company in (companies or []) if str(company).strip()]
    if selected_companies:
        clauses.append(f"j.company IN ({', '.join('?' for _ in selected_companies)})")
        params.extend(selected_companies)
    portal_value = str(portal or "").strip().lower()
    if portal_value:
        if portal_value == "company_boards":
            clauses.append("ifnull(s.portal, '') = ''")
        else:
            clauses.append("s.portal = ?")
            params.append(portal_value)
    normalized_source_id = int(source_id or 0)
    if normalized_source_id > 0:
        clauses.append("j.source_id = ?")
        params.append(normalized_source_id)
    source_tag_value = str(source_tag or "").strip().lower()
    if source_tag_value:
        tag_aliases = {
            "security": ["security", "cybersecurity", "cloud-security", "endpoint-security"],
            "systems": ["systems", "infrastructure", "distributed-systems", "storage", "networking"],
            "ai": ["ai", "ml"],
        }
        wanted_tags = tag_aliases.get(source_tag_value, [source_tag_value])
        clauses.append("(" + " OR ".join("lower(s.tags_json) LIKE ?" for _ in wanted_tags) + ")")
        params.extend(f"%\"{tag}\"%" for tag in wanted_tags)
    hn_mode_value = str(hn_mode or "").strip().lower()
    if hn_mode_value in {"parsed", "fallback"}:
        fallback_names = ("hacker news", "hacker news who is hiring", "careers", "jobs", "apply")
        fallback_clause = (
            "(j.company = s.company OR lower(j.company) IN "
            f"({', '.join('?' for _ in fallback_names)}))"
        )
        clauses.append("ifnull(s.portal, '') = 'hackernews'")
        if hn_mode_value == "fallback":
            clauses.append(fallback_clause)
        else:
            clauses.append(f"NOT {fallback_clause}")
        params.extend(fallback_names)
    if founding_only:
        clauses.append("jm.matched_builtin_groups_json LIKE ?")
        params.append("%\"founding_engineer\"%")

    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    return where, params


def query_jobs(
    db_path: Path | str = DEFAULT_DB_PATH,
    *,
    matching_only: bool = True,
    open_only: bool = True,
    search: str = "",
    stack: str = "",
    companies: Optional[Sequence[str]] = None,
    portal: str = "",
    source_id: int = 0,
    source_tag: str = "",
    hn_mode: str = "",
    founding_only: bool = False,
    group_by_company: bool = False,
    limit: int = 1000,
    summary_only: bool = False,
) -> List[Dict[str, Any]]:
    with connect(db_path) as conn:
        migrate_connection(conn)
        where, params = _job_where(
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
        )
        order_by = (
            "j.company COLLATE NOCASE, j.title COLLATE NOCASE, j.last_seen_at DESC"
            if group_by_company
            else "j.last_seen_at DESC, j.company COLLATE NOCASE, j.title COLLATE NOCASE"
        )
        job_select = (
            """
                j.id,
                j.source_id,
                j.company,
                j.title,
                j.location,
                j.published_at,
                j.last_seen_at,
                j.status
            """
            if summary_only
            else "j.*"
        )
        match_select = (
            """
                0 AS passes_filter
            """
            if summary_only
            else """
                jm.passes_filter,
                jm.matched_required_words_json,
                jm.matched_include_words_json,
                jm.matched_include_group_json,
                jm.matched_builtin_groups_json,
                jm.location_modes_json,
                jm.interest_tags_json
            """
        )
        rows = conn.execute(
            f"""
            SELECT
                {job_select},
                s.portal AS source_portal,
                s.company AS source_name,
                {match_select},
                COALESCE(jss.detected_stack, '') AS detected_stack
            FROM jobs j
            JOIN sources s ON s.id = j.source_id
            LEFT JOIN job_matches jm ON jm.job_id = j.id
            LEFT JOIN job_stack_summary jss ON jss.job_id = j.id
            {where}
            ORDER BY {order_by}
            LIMIT ?
            """,
            [*params, limit],
        ).fetchall()
    if summary_only:
        return [_row_to_job_summary(row) for row in rows]
    return [_row_to_job(row) for row in rows]


def group_jobs_by_company(jobs: Sequence[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Group normalized jobs by company while preserving input row order."""
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for job in jobs:
        company = str(job.get("company") or "Unknown").strip() or "Unknown"
        grouped.setdefault(company, []).append(dict(job))
    return dict(sorted(grouped.items(), key=lambda item: item[0].lower()))


def list_company_counts(
    db_path: Path | str = DEFAULT_DB_PATH,
    *,
    matching_only: bool = True,
    open_only: bool = True,
    search: str = "",
    stack: str = "",
    portal: str = "",
    source_id: int = 0,
    source_tag: str = "",
    hn_mode: str = "",
    founding_only: bool = False,
) -> List[Dict[str, Any]]:
    with connect(db_path) as conn:
        migrate_connection(conn)
        where, params = _job_where(
            matching_only=matching_only,
            open_only=open_only,
            search=search,
            stack=stack,
            companies=None,
            portal=portal,
            source_id=source_id,
            source_tag=source_tag,
            hn_mode=hn_mode,
            founding_only=founding_only,
        )
        rows = conn.execute(
            f"""
            SELECT
                j.company,
                COUNT(*) AS open_count,
                SUM(CASE WHEN jm.passes_filter = 1 THEN 1 ELSE 0 END) AS matching_count
            FROM jobs j
            JOIN sources s ON s.id = j.source_id
            LEFT JOIN job_matches jm ON jm.job_id = j.id
            {where}
            GROUP BY j.company
            ORDER BY matching_count DESC, open_count DESC, j.company COLLATE NOCASE
            """,
            params,
        ).fetchall()
    return [
        {
            "company": str(row["company"]),
            "open_count": int(row["open_count"] or 0),
            "matching_count": int(row["matching_count"] or 0),
        }
        for row in rows
    ]


def _row_to_job(row: sqlite3.Row) -> Dict[str, Any]:
    """Convert one SQLite job row into the normalized dict used by the app."""
    item = dict(row)
    item["remote"] = bool(item.get("remote"))
    item["passes_filter"] = bool(item.get("passes_filter"))
    item["raw"] = json_loads(item.pop("raw_json", "{}"), {})
    item["matched_required_words"] = json_loads(item.pop("matched_required_words_json", "[]"), [])
    item["matched_include_words"] = json_loads(item.pop("matched_include_words_json", "[]"), [])
    item["matched_include_group"] = json_loads(item.pop("matched_include_group_json", "[]"), [])
    item["matched_builtin_groups"] = json_loads(item.pop("matched_builtin_groups_json", "[]"), [])
    item["location_modes"] = json_loads(item.pop("location_modes_json", "[]"), [])
    item["interest_tags"] = json_loads(item.pop("interest_tags_json", "[]"), [])
    return item


def _row_to_job_summary(row: sqlite3.Row) -> Dict[str, Any]:
    """Convert one summary-only row into the normalized dict used by the jobs table."""
    item = dict(row)
    item["remote"] = bool(item.get("remote"))
    item["passes_filter"] = bool(item.get("passes_filter"))
    item["matched_required_words"] = []
    item["matched_include_words"] = []
    item["matched_include_group"] = []
    item["matched_builtin_groups"] = []
    item["location_modes"] = []
    item["interest_tags"] = []
    return item


def get_job_detail(
    db_path: Path | str,
    job_id: int,
) -> Optional[Dict[str, Any]]:
    with connect(db_path) as conn:
        migrate_connection(conn)
        row = conn.execute(
            """
            SELECT
                j.*,
                s.portal AS source_portal,
                s.entry_kind AS source_entry_kind,
                s.auth_mode AS source_auth_mode,
                s.browser_backend AS source_browser_backend,
                s.session_status AS source_session_status,
                jm.passes_filter,
                jm.matched_required_words_json,
                jm.matched_include_words_json,
                jm.matched_include_group_json,
                jm.matched_builtin_groups_json,
                jm.location_modes_json,
                jm.interest_tags_json,
                COALESCE(jss.detected_stack, '') AS detected_stack
            FROM jobs j
            JOIN sources s ON s.id = j.source_id
            LEFT JOIN job_matches jm ON jm.job_id = j.id
            LEFT JOIN job_stack_summary jss ON jss.job_id = j.id
            WHERE j.id = ?
            """,
            (job_id,),
        ).fetchone()
    return _row_to_job(row) if row else None


def job_count(db_path: Path | str = DEFAULT_DB_PATH) -> int:
    """Return total stored job rows."""
    with connect(db_path) as conn:
        migrate_connection(conn)
        row = conn.execute("SELECT COUNT(*) AS count FROM jobs").fetchone()
    return int(row["count"] or 0) if row else 0


def new_job_report_since(db_path: Path | str, since_ts: int, *, before_count: int = 0) -> Dict[str, int]:
    """Return non-destructive scrape delta counts since the provided timestamp."""
    with connect(db_path) as conn:
        migrate_connection(conn)
        after = conn.execute("SELECT COUNT(*) AS count FROM jobs").fetchone()
        new_rows = conn.execute(
            """
            SELECT
                COUNT(DISTINCT j.id) AS new_count,
                SUM(CASE WHEN jm.passes_filter = 1 THEN 1 ELSE 0 END) AS matching_new
            FROM jobs j
            LEFT JOIN job_matches jm ON jm.job_id = j.id
            WHERE j.first_seen_at >= ?
            """,
            (int(since_ts),),
        ).fetchone()
    after_count = int(after["count"] or 0) if after else 0
    return {
        "jobs_before": int(before_count or 0),
        "jobs_after": after_count,
        "net_new": max(0, after_count - int(before_count or 0)),
        "new_since_started": int(new_rows["new_count"] or 0) if new_rows else 0,
        "matching_new": int(new_rows["matching_new"] or 0) if new_rows else 0,
    }


def get_job_details(
    db_path: Path | str,
    job_ids: Sequence[int],
) -> List[Dict[str, Any]]:
    """Load multiple job-detail rows in one query while preserving input order."""
    normalized_ids = [int(job_id) for job_id in job_ids if int(job_id)]
    if not normalized_ids:
        return []
    placeholders = ", ".join("?" for _ in normalized_ids)
    with connect(db_path) as conn:
        migrate_connection(conn)
        rows = conn.execute(
            f"""
            SELECT
                j.*,
                s.portal AS source_portal,
                s.company AS source_name,
                s.entry_kind AS source_entry_kind,
                s.auth_mode AS source_auth_mode,
                s.browser_backend AS source_browser_backend,
                s.session_status AS source_session_status,
                jm.passes_filter,
                jm.matched_required_words_json,
                jm.matched_include_words_json,
                jm.matched_include_group_json,
                jm.matched_builtin_groups_json,
                jm.location_modes_json,
                jm.interest_tags_json,
                COALESCE(jss.detected_stack, '') AS detected_stack
            FROM jobs j
            JOIN sources s ON s.id = j.source_id
            LEFT JOIN job_matches jm ON jm.job_id = j.id
            LEFT JOIN job_stack_summary jss ON jss.job_id = j.id
            WHERE j.id IN ({placeholders})
            """,
            normalized_ids,
        ).fetchall()
    by_id = {int(row["id"]): _row_to_job(row) for row in rows}
    return [by_id[job_id] for job_id in normalized_ids if job_id in by_id]


def list_stack_names(
    db_path: Path | str = DEFAULT_DB_PATH,
    *,
    matching_only: bool = False,
    open_only: bool = True,
    portal: str = "",
    source_id: int = 0,
    source_tag: str = "",
    hn_mode: str = "",
    founding_only: bool = False,
) -> List[str]:
    where, params = _job_where(
        matching_only=matching_only,
        open_only=open_only,
        search="",
        stack="",
        companies=None,
        portal=portal,
        source_id=source_id,
        source_tag=source_tag,
        hn_mode=hn_mode,
        founding_only=founding_only,
    )
    with connect(db_path) as conn:
        migrate_connection(conn)
        rows = conn.execute(
            f"""
            SELECT js.name
            FROM job_stack js
            JOIN jobs j ON j.id = js.job_id
            JOIN sources s ON s.id = j.source_id
            LEFT JOIN job_matches jm ON jm.job_id = j.id
            {where}
              {"AND" if where else "WHERE"} js.category IN ('language', 'framework', 'domain', 'tool', 'group')
            GROUP BY js.name
            ORDER BY js.name COLLATE NOCASE
            """,
            params,
        ).fetchall()
    return [str(row["name"]) for row in rows]


def list_stack_names_by_categories(
    db_path: Path | str = DEFAULT_DB_PATH,
    *,
    categories: Sequence[str],
    matching_only: bool = False,
    open_only: bool = True,
) -> List[Dict[str, str]]:
    wanted = [str(category).strip() for category in categories if str(category).strip()]
    if not wanted:
        return []
    clauses: List[str] = [f"js.category IN ({', '.join('?' for _ in wanted)})"]
    params: List[Any] = list(wanted)
    if matching_only:
        clauses.append("jm.passes_filter = 1")
    if open_only:
        clauses.append("j.status = 'open'")
    where = " AND ".join(clauses)
    with connect(db_path) as conn:
        migrate_connection(conn)
        rows = conn.execute(
            f"""
            SELECT js.category, js.name
            FROM job_stack js
            JOIN jobs j ON j.id = js.job_id
            LEFT JOIN job_matches jm ON jm.job_id = j.id
            WHERE {where}
            GROUP BY js.category, js.name
            ORDER BY js.category COLLATE NOCASE, js.name COLLATE NOCASE
            """,
            params,
        ).fetchall()
    return [{"category": str(row["category"]), "name": str(row["name"])} for row in rows]


def analytics_summary(
    db_path: Path | str = DEFAULT_DB_PATH,
    *,
    matching_only: bool = True,
    open_only: bool = True,
    companies: Optional[Sequence[str]] = None,
    portal: str = "",
    source_id: int = 0,
    source_tag: str = "",
    hn_mode: str = "",
    search: str = "",
    stack: str = "",
    founding_only: bool = False,
) -> Dict[str, Any]:
    where, params = _job_where(
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
    )
    with connect(db_path) as conn:
        migrate_connection(conn)
        totals = conn.execute(
            f"""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN j.remote = 1 THEN 1 ELSE 0 END) AS remote_total,
                SUM(CASE WHEN j.status = 'open' THEN 1 ELSE 0 END) AS open_total,
                SUM(CASE WHEN j.status = 'closed' THEN 1 ELSE 0 END) AS closed_total
            FROM jobs j
            JOIN sources s ON s.id = j.source_id
            LEFT JOIN job_matches jm ON jm.job_id = j.id
            {where}
            """,
            params,
        ).fetchone()
        stack_rows = conn.execute(
            f"""
            SELECT js.category, js.name, COUNT(DISTINCT j.id) AS count
            FROM job_stack js
            JOIN jobs j ON j.id = js.job_id
            JOIN sources s ON s.id = j.source_id
            LEFT JOIN job_matches jm ON jm.job_id = j.id
            {where}
            GROUP BY js.category, js.name
            ORDER BY count DESC, js.name COLLATE NOCASE
            """,
            params,
        ).fetchall()
        company_rows = conn.execute(
            f"""
            SELECT j.company, COUNT(*) AS count
            FROM jobs j
            JOIN sources s ON s.id = j.source_id
            LEFT JOIN job_matches jm ON jm.job_id = j.id
            {where}
            GROUP BY j.company
            ORDER BY count DESC, j.company COLLATE NOCASE
            LIMIT 50
            """,
            params,
        ).fetchall()
    allowed_stack_categories = {"language", "tool", "domain"}
    stacks: Dict[str, List[Dict[str, Any]]] = {
        "language": [],
        "tool": [],
        "domain": [],
    }
    for row in stack_rows:
        category = str(row["category"])
        if category not in allowed_stack_categories:
            continue
        stacks[category].append(
            {"name": row["name"], "count": int(row["count"])}
        )

    return {
        "totals": {
            "total": int(totals["total"] or 0) if totals else 0,
            "remote_total": int(totals["remote_total"] or 0) if totals else 0,
            "open_total": int(totals["open_total"] or 0) if totals else 0,
            "closed_total": int(totals["closed_total"] or 0) if totals else 0,
        },
        "stack": stacks,
        "companies": [
            {"company": row["company"], "count": int(row["count"])}
            for row in company_rows
        ],
    }


def make_filter_signature(filters: Dict[str, Any]) -> str:
    """Build a stable hash key for one filtered job/analysis scope."""
    normalized = dict(filters)
    if "companies" in normalized:
        normalized["companies"] = sorted(
            str(company).strip()
            for company in (normalized.get("companies") or [])
            if str(company).strip()
        )
    payload = json_dumps(normalized)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _question_summary(question: Dict[str, Any], ordinal: int) -> Dict[str, Any]:
    """Trim a heavy generated question payload into a navigator-summary row."""
    return {
        "ordinal": ordinal,
        "track": str(question.get("track") or "").strip(),
        "level": str(question.get("level") or question.get("difficulty") or "").strip(),
        "difficulty": str(question.get("level") or question.get("difficulty") or "").strip(),
        "category": str(question.get("category") or "").strip(),
        "topic": str(question.get("topic") or "").strip(),
        "question": str(question.get("question") or "").strip(),
        "signals": [str(signal).strip() for signal in question.get("signals", []) if str(signal).strip()],
    }


def _row_to_question_entry(row: sqlite3.Row) -> Dict[str, Any]:
    """Convert one question-entry SQLite row into the app's normalized dict shape."""
    item = dict(row)
    item["signals"] = json_loads(item.pop("signals_json", "[]"), [])
    item["glossary"] = json_loads(item.pop("glossary_json", "[]"), [])
    item["pros"] = json_loads(item.pop("pros_json", "[]"), [])
    item["cons"] = json_loads(item.pop("cons_json", "[]"), [])
    item["level"] = str(item.get("level") or item.get("difficulty") or "")
    item["difficulty"] = item["level"]
    return item


def get_question_set(
    db_path: Path | str,
    *,
    scope_type: str,
    scope_key: str,
    engine: str,
    version: str,
) -> Optional[Dict[str, Any]]:
    with connect(db_path) as conn:
        migrate_connection(conn)
        row = conn.execute(
            """
            SELECT *
            FROM question_sets
            WHERE scope_type = ?
              AND scope_key = ?
              AND engine = ?
              AND version = ?
            """,
            (scope_type, scope_key, engine, version),
        ).fetchone()
    if not row:
        return None
    item = dict(row)
    item["metadata"] = json_loads(item.pop("metadata_json", "{}"), {})
    item["questions"] = json_loads(item.pop("questions_json", "[]"), [])
    item["question_set_id"] = int(item.get("id") or 0)
    return item


def upsert_question_set(
    db_path: Path | str,
    *,
    scope_type: str,
    scope_key: str,
    engine: str,
    version: str,
    title: str,
    metadata: Dict[str, Any],
    questions: List[Dict[str, Any]],
    generated_at: Optional[int] = None,
) -> None:
    with connect(db_path) as conn:
        migrate_connection(conn)
        summaries = [_question_summary(question, index) for index, question in enumerate(questions, start=1)]
        conn.execute(
            """
            INSERT INTO question_sets (
                scope_type, scope_key, engine, version, title,
                metadata_json, questions_json, generated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(scope_type, scope_key, engine, version) DO UPDATE SET
                title = excluded.title,
                metadata_json = excluded.metadata_json,
                questions_json = excluded.questions_json,
                generated_at = excluded.generated_at
            """,
            (
                scope_type,
                scope_key,
                engine,
                version,
                title,
                json_dumps(metadata),
                json_dumps(summaries),
                generated_at if generated_at is not None else now_ts(),
            ),
        )
        row = conn.execute(
            """
            SELECT id
            FROM question_sets
            WHERE scope_type = ?
              AND scope_key = ?
              AND engine = ?
              AND version = ?
            """,
            (scope_type, scope_key, engine, version),
        ).fetchone()
        question_set_id = int(row["id"]) if row else 0
        if question_set_id:
            conn.execute(
                "DELETE FROM question_entries WHERE question_set_id = ?",
                (question_set_id,),
            )
            rows: List[Sequence[Any]] = []
            for index, question in enumerate(questions, start=1):
                rows.append(
                    (
                        question_set_id,
                        index,
                        str(question.get("track") or "").strip(),
                        str(question.get("level") or question.get("difficulty") or "").strip(),
                        str(question.get("category") or "").strip(),
                        str(question.get("topic") or "").strip(),
                        str(question.get("question") or "").strip(),
                        json_dumps([str(signal).strip() for signal in question.get("signals", []) if str(signal).strip()]),
                        str(question.get("deep_dive_html") or "").strip(),
                        json_dumps(question.get("glossary") or []),
                        json_dumps(question.get("pros") or []),
                        json_dumps(question.get("cons") or []),
                        str(question.get("visual_svg") or "").strip(),
                        str(question.get("mindmap_svg") or "").strip(),
                        str(question.get("code_walkthrough_html") or "").strip(),
                    )
                )
            if rows:
                conn.executemany(
                    """
                    INSERT INTO question_entries (
                        question_set_id, ordinal, track, level, category, topic, question,
                        signals_json, deep_dive_html, glossary_json, pros_json, cons_json,
                        visual_svg, mindmap_svg, code_walkthrough_html
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
        conn.commit()


def list_question_entries(
    db_path: Path | str,
    *,
    question_set_id: int,
    track: str = "",
    level: str = "",
    topic: str = "",
    summary_only: bool = False,
) -> List[Dict[str, Any]]:
    clauses = ["question_set_id = ?"]
    params: List[Any] = [question_set_id]
    if track.strip():
        clauses.append("track = ?")
        params.append(track.strip())
    if level.strip():
        clauses.append("level = ?")
        params.append(level.strip())
    if topic.strip():
        clauses.append("topic = ?")
        params.append(topic.strip())
    where = " AND ".join(clauses)
    select_clause = (
        "id, question_set_id, ordinal, track, level, category, topic, question, signals_json"
        if summary_only
        else "*"
    )
    with connect(db_path) as conn:
        migrate_connection(conn)
        rows = conn.execute(
            f"""
            SELECT {select_clause}
            FROM question_entries
            WHERE {where}
            ORDER BY ordinal
            """,
            params,
        ).fetchall()
    return [_row_to_question_entry(row) for row in rows]


def get_question_entry(
    db_path: Path | str,
    *,
    entry_id: int,
) -> Optional[Dict[str, Any]]:
    with connect(db_path) as conn:
        migrate_connection(conn)
        row = conn.execute(
            """
            SELECT *
            FROM question_entries
            WHERE id = ?
            """,
            (entry_id,),
        ).fetchone()
    return _row_to_question_entry(row) if row else None


def list_question_prefill_jobs(
    db_path: Path | str,
    *,
    matching_only: bool = True,
    open_only: bool = True,
    limit: int = 120,
) -> List[Dict[str, Any]]:
    return query_jobs(
        db_path,
        matching_only=matching_only,
        open_only=open_only,
        search="",
        stack="",
        limit=limit,
    )


def list_question_prefill_filter_specs(
    db_path: Path | str,
    *,
    matching_only: bool = True,
    open_only: bool = True,
) -> List[Dict[str, Any]]:
    specs = [{"matching_only": matching_only, "open_only": open_only, "search": "", "stack": ""}]
    for item in list_stack_names_by_categories(
        db_path,
        categories=("language", "domain"),
        matching_only=matching_only,
        open_only=open_only,
    ):
        specs.append(
            {
                "matching_only": matching_only,
                "open_only": open_only,
                "search": "",
                "stack": str(item.get("name") or ""),
            }
        )
    seen: set[str] = set()
    unique_specs: List[Dict[str, Any]] = []
    for spec in specs:
        key = make_filter_signature(spec)
        if key in seen:
            continue
        seen.add(key)
        unique_specs.append(spec)
    return unique_specs


def export_jobs_json(
    db_path: Path | str = DEFAULT_DB_PATH,
    out_path: Path | str = Path("company_jobs.json"),
    *,
    matching_only: bool = True,
    open_only: bool = True,
    companies: Optional[Sequence[str]] = None,
    portal: str = "",
    source_id: int = 0,
    source_tag: str = "",
    hn_mode: str = "",
    founding_only: bool = False,
    search: str = "",
    stack: str = "",
) -> int:
    jobs = query_jobs(
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
        limit=100000,
    )
    payload: List[Dict[str, Any]] = []
    for job in jobs:
        detail = get_job_detail(db_path, int(job["id"])) or job
        raw = detail.copy()
        raw.pop("raw_json", None)
        payload.append(raw)

    out = Path(out_path)
    fs.atomic_write_json(out, payload)
    return len(payload)


def execute_many(conn: sqlite3.Connection, sql: str, rows: Sequence[Sequence[Any]]) -> None:
    """Run batched SQL writes for the provided row tuples."""
    conn.executemany(sql, rows)
