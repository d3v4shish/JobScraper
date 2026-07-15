import json
from pathlib import Path

from jobscraper.storage import db
from jobscraper.ui import tasks


def _write_sources(path: Path) -> None:
    path.write_text(
        json.dumps(
            [
                {
                    "company": "Roadmap Co",
                    "ats": "remotive_api",
                    "url": "https://remotive.com/api/remote-jobs",
                    "entry_url": "https://remotive.com/remote-jobs/software-dev",
                    "enabled": True,
                    "portal": "remotive",
                    "entry_kind": "public_api",
                    "auth_mode": "public",
                }
            ]
        ),
        encoding="utf-8",
    )


def test_ai_status_task_reports_openai_and_local_state(monkeypatch) -> None:
    monkeypatch.setattr(
        tasks.ai_client,
        "openai_config",
        lambda: {
            "api_key": "test-key",
            "base_url": "https://api.openai.com/v1",
            "model": "gpt-4o-mini",
            "organization": "",
            "project": "",
        },
    )
    monkeypatch.setattr(
        tasks.ai_client,
        "local_ai_status",
        lambda: {
            "ready": False,
            "provider": "ollama",
            "base_url": "http://127.0.0.1:11434",
            "model": "",
            "models": [],
            "label": "unreachable",
            "detail": "connection refused",
        },
    )

    result = tasks.load_ai_status_task()

    assert result["openai_ready"] is True
    assert result["openai_label"] == "configured"
    assert result["local_status"]["label"] == "unreachable"
    assert "OpenAI: configured" in result["summary"]
    assert "Local AI: unreachable" in result["summary"]
    assert "connection refused" in result["activity"]


def test_whole_db_roadmap_uses_summary_rows(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "jobs.sqlite"
    sources_path = tmp_path / "sources.json"
    _write_sources(sources_path)
    db.import_sources_report(db_path, sources_path, create_backup=False)
    source = db.list_sources(db_path)[0]
    with db.connect(db_path) as conn:
        db.migrate_connection(conn)
        db.upsert_job(
            conn,
            source,
            {
                "job_key": "roadmap:1",
                "source_job_id": "roadmap-1",
                "title": "Linux Platform Engineer",
                "location": "Remote",
                "department": "Engineering",
                "remote": True,
                "job_url": "https://example.test/roadmap-1",
                "apply_url": "https://example.test/roadmap-1/apply",
                "text": "Linux, Python, distributed systems, storage, and networking.",
            },
            {"passes_filter": True, "location_modes": ["remote"], "interest_tags": ["Linux"]},
            {"languages": ["Python"], "frameworks": [], "domains": ["Linux", "Storage"], "tools": [], "groups": []},
        )
        conn.commit()

    original_query_jobs = tasks.db.query_jobs
    calls: list[dict[str, object]] = []

    def record_query_jobs(*args, **kwargs):
        calls.append(dict(kwargs))
        return original_query_jobs(*args, **kwargs)

    monkeypatch.setattr(tasks.db, "query_jobs", record_query_jobs)

    result = tasks.build_roadmap_payload(
        db_path,
        scope_mode="all",
        selected_job_ids=[],
        current_job_filters={},
        selected_companies=[],
    )

    assert calls[0]["summary_only"] is True
    assert calls[0]["limit"] == 100000
    assert result["payload"]["scope_mode"] == "all"
    assert result["payload"]["sample_size"] == 1
    assert result["payload"]["refreshed_at"]
    assert "sample 1" in result["html"]
    assert "refreshed" in result["html"]
    assert "filters:" in result["html"]


def test_analytics_payload_discloses_scope_sample_refresh_and_filters(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    sources_path = tmp_path / "sources.json"
    _write_sources(sources_path)
    db.import_sources_report(db_path, sources_path, create_backup=False)
    source = db.list_sources(db_path)[0]
    with db.connect(db_path) as conn:
        db.migrate_connection(conn)
        db.upsert_job(
            conn,
            source,
            {
                "job_key": "analytics:1",
                "source_job_id": "analytics-1",
                "title": "Rust Storage Engineer",
                "location": "Remote",
                "department": "Engineering",
                "remote": True,
                "job_url": "https://example.test/analytics-1",
                "apply_url": "https://example.test/analytics-1/apply",
                "text": "Rust, Linux, and storage systems.",
            },
            {"passes_filter": True, "location_modes": ["remote"], "interest_tags": ["Rust"]},
            {"languages": ["Rust"], "frameworks": [], "domains": ["Storage"], "tools": [], "groups": []},
        )
        conn.commit()

    result = tasks.load_analytics_view_task(
        db_path,
        matching_only=True,
        open_only=True,
        companies=[],
        portal="remotive",
        source_id=0,
        source_tag="",
        hn_mode="",
        search="storage",
        stack="Rust",
        founding_only=False,
    )

    payload = result["payload"]
    assert payload["scope_label"] == "Current filters"
    assert payload["scope_mode"] == "filters"
    assert payload["sample_size"] == 1
    assert payload["refreshed_at"]
    assert "portal:remotive" in payload["active_filters"]
    assert "search:storage" in payload["active_filters"]
    assert "scope Current filters" in result["html"]
    assert "sample 1" in result["html"]
    assert "filters:" in result["html"]


def test_export_task_cancel_leaves_no_final_file(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    sources_path = tmp_path / "sources.json"
    _write_sources(sources_path)
    db.import_sources_report(db_path, sources_path, create_backup=False)
    source = db.list_sources(db_path)[0]
    with db.connect(db_path) as conn:
        db.migrate_connection(conn)
        db.upsert_job(
            conn,
            source,
            {
                "job_key": "export:1",
                "source_job_id": "export-1",
                "title": "Python Engineer",
                "location": "Remote",
                "remote": True,
                "job_url": "https://example.test/export-1",
                "apply_url": "https://example.test/export-1/apply",
                "text": "Python and SQLite.",
            },
            {"passes_filter": True, "location_modes": ["remote"]},
            {"languages": ["Python"], "frameworks": [], "domains": [], "tools": ["SQLite"], "groups": []},
        )
        conn.commit()

    out_path = tmp_path / "jobs.json"
    result = tasks.export_jobs_task(
        db_path,
        str(out_path),
        matching_only=True,
        open_only=True,
        companies=[],
        portal="",
        source_id=0,
        source_tag="",
        hn_mode="",
        founding_only=False,
        search="",
        stack="",
        should_cancel=lambda: True,
    )

    assert result["cancelled"] is True
    assert not out_path.exists()
