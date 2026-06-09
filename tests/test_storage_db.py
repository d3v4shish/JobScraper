import json
from pathlib import Path

import pytest

from jobscraper import paths
from jobscraper.storage import db
from jobscraper.ui.tasks import preview_source_import_task


def _write_sources(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(json.dumps(rows), encoding="utf-8")


def _source_row(company: str = "Remotive Test") -> dict[str, object]:
    return {
        "company": company,
        "ats": "remotive_api",
        "url": "https://remotive.com/api/remote-jobs",
        "entry_url": "https://remotive.com/remote-jobs/software-dev",
        "enabled": True,
        "portal": "remotive",
        "entry_kind": "public_api",
        "auth_mode": "public",
        "search_terms": ["software engineer"],
        "locations": ["remote"],
    }


def test_import_sources_report_previews_backs_up_and_imports(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "backups_dir", lambda workspace_root=None: tmp_path / "backups")
    db_path = tmp_path / "jobs.sqlite"
    sources_path = tmp_path / "sources.json"
    _write_sources(sources_path, [_source_row()])

    db.init_db(db_path)
    preview = db.preview_source_import(db_path, sources_path)
    assert preview_source_import_task(db_path, sources_path) == preview
    assert preview["total"] == 1
    assert preview["new"] == 1
    assert preview["stale_disabled"] == 0

    report = db.import_sources_report(db_path, sources_path, create_backup=True)
    assert report["count"] == 1
    assert Path(report["backup_path"]).exists()

    rows = db.list_sources(db_path)
    assert len(rows) == 1
    assert rows[0]["company"] == "Remotive Test"
    assert rows[0]["enabled"] is True


def test_packaged_sources_validate_and_have_adapters(tmp_path: Path) -> None:
    from jobscraper.scraping import core

    preview = db.preview_source_import(tmp_path / "jobs.sqlite", paths.bundled_sources_path())
    assert preview["total"] >= 240

    rows = json.loads(paths.bundled_sources_path().read_text(encoding="utf-8"))
    missing = sorted({str(row["ats"]) for row in rows} - set(core.ADAPTERS))
    assert missing == []

    expected_new_sources = {
        "arbeitnow_api",
        "arc_dev_search",
        "builtin_jobs",
        "climatebase_jobs",
        "cybersecjobs_search",
        "devsnap_search",
        "foundit_search",
        "himalayas_search",
        "amazon_jobs",
        "apple_jobs",
        "ibm_careers",
        "microsoft_careers",
        "naukri_search",
        "oracle_careers",
        "uber_careers",
        "welcome_to_the_jungle_search",
    }
    expected_new_ats = {
        "adp",
        "bamboohr",
        "breezy_hr",
        "comeet",
        "icims",
        "jobvite",
        "oracle_taleo",
        "paylocity",
        "pinpoint",
        "rippling",
        "sap_successfactors",
        "teamtailor",
        "ukg",
        "ultipro",
        "workable",
    }
    assert expected_new_sources <= db.SUPPORTED_ATS
    assert expected_new_sources <= set(core.ADAPTERS)
    assert expected_new_ats <= db.SUPPORTED_ATS
    assert expected_new_ats <= set(core.ADAPTERS)


def test_public_sources_do_not_include_auth_backed_or_token_sources(tmp_path: Path) -> None:
    from jobscraper.scraping import core

    preview = db.preview_source_import(tmp_path / "jobs.sqlite", paths.bundled_sources_path())
    assert preview["total"] > 240

    rows = json.loads(paths.bundled_sources_path().read_text(encoding="utf-8"))
    blocked_ats = {
        "indeed_saved_view",
        "linkedin_saved_view",
        "upwork_search",
        "adzuna_api",
        "usajobs_api",
        "jooble_api",
        "ziprecruiter_api",
        "jobspy_search",
        "browser_discovery",
        "wellfound_search",
        "meta_careers",
    }
    blocked_portals = {"linkedin", "indeed", "upwork", "jobspy_linkedin", "jobspy_indeed"}
    assert [row for row in rows if str(row.get("auth_mode") or "public") != "public"] == []
    assert sorted({str(row.get("ats") or "") for row in rows} & blocked_ats) == []
    assert sorted({str(row.get("portal") or "") for row in rows} & blocked_portals) == []
    assert [row for row in rows if row.get("browser_required")] == []
    assert [row for row in rows if "browser_backend" in row] == []
    assert [row for row in rows if "wait_selector" in row] == []
    assert blocked_ats.isdisjoint(db.SUPPORTED_ATS)
    assert blocked_ats.isdisjoint(core.ADAPTERS)
    assert not hasattr(core, "portal_preflight")
    assert not (paths.project_root() / "src" / "jobscraper" / "storage" / "sessions.py").exists()
    assert not (paths.project_root() / "src" / "jobscraper" / "runtime" / "portal_login.py").exists()
    assert not (paths.project_root() / "src" / "jobscraper" / "scraping" / "qwebengine_helper.py").exists()


def test_import_sources_rejects_non_public_auth_mode(tmp_path: Path) -> None:
    source = _source_row("Private Source")
    source["auth_mode"] = "browser_import"
    sources_path = tmp_path / "sources.json"
    _write_sources(sources_path, [source])

    with pytest.raises(ValueError, match="unsupported auth_mode"):
        db.preview_source_import(tmp_path / "jobs.sqlite", sources_path)


def test_import_sources_rejects_jobspy_adapter(tmp_path: Path) -> None:
    source = _source_row("JobSpy")
    source["ats"] = "jobspy_search"
    source["portal"] = "jobspy_google"
    source["url"] = "https://www.google.com/search?q=software+engineer+jobs"
    source["entry_url"] = "https://www.google.com/search?q=software+engineer+jobs"
    source["token"] = ""
    sources_path = tmp_path / "sources.json"
    _write_sources(sources_path, [source])

    with pytest.raises(ValueError, match="unsupported ats"):
        db.preview_source_import(tmp_path / "jobs.sqlite", sources_path)


def test_import_sources_rejects_duplicate_identities(tmp_path: Path) -> None:
    sources_path = tmp_path / "sources.json"
    _write_sources(sources_path, [_source_row(), _source_row()])

    with pytest.raises(ValueError, match="duplicate source identity"):
        db.preview_source_import(tmp_path / "jobs.sqlite", sources_path)


def test_import_sources_disables_stale_db_only_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "backups_dir", lambda workspace_root=None: tmp_path / "backups")
    db_path = tmp_path / "jobs.sqlite"
    first_sources = tmp_path / "sources_first.json"
    second_sources = tmp_path / "sources_second.json"
    _write_sources(first_sources, [_source_row("Keep Me"), _source_row("Disable Me")])
    _write_sources(second_sources, [_source_row("Keep Me")])

    db.import_sources_report(db_path, first_sources, create_backup=False)
    preview = db.preview_source_import(db_path, second_sources)
    assert preview["stale_disabled"] == 1

    db.import_sources_report(db_path, second_sources, create_backup=True)
    rows = {row["company"]: row for row in db.list_sources(db_path)}
    assert rows["Keep Me"]["enabled"] is True
    assert rows["Disable Me"]["enabled"] is False
    assert rows["Disable Me"]["last_status"] == "disabled"


def test_update_source_status_tracks_health_counters_and_duration(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    sources_path = tmp_path / "sources.json"
    _write_sources(sources_path, [_source_row()])
    db.import_sources_report(db_path, sources_path, create_backup=False)

    source_id = int(db.list_sources(db_path)[0]["id"])
    with db.connect(db_path) as conn:
        db.migrate_connection(conn)
        db.update_source_status(conn, source_id, status="direct_api", error="", duration_ms=123)
        db.update_source_status(conn, source_id, status="error", error="boom", duration_ms=7)
        conn.commit()

    row = db.list_sources(db_path)[0]
    assert row["success_count"] == 1
    assert row["failure_count"] == 1
    assert row["last_duration_ms"] == 7
    assert row["last_error"] == "boom"


def test_summary_job_query_uses_cached_stack_summary(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    sources_path = tmp_path / "sources.json"
    _write_sources(sources_path, [_source_row()])
    db.import_sources_report(db_path, sources_path, create_backup=False)
    source = db.list_sources(db_path)[0]

    with db.connect(db_path) as conn:
        db.migrate_connection(conn)
        job_id = db.upsert_job(
            conn,
            source,
            {
                "job_key": "remotive:test-1",
                "source_job_id": "test-1",
                "title": "Senior Python Engineer",
                "location": "Remote",
                "remote": True,
                "job_url": "https://example.com/jobs/test-1",
                "apply_url": "https://example.com/jobs/test-1/apply",
                "text": "Python, PyQt, and SQLite production tooling.",
            },
            {"passes_filter": True, "location_modes": ["remote"]},
            {
                "languages": ["Python"],
                "frameworks": ["PyQt"],
                "domains": ["Security"],
                "tools": ["SQLite"],
                "groups": ["Systems"],
            },
        )
        cached = conn.execute(
            "SELECT detected_stack FROM job_stack_summary WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        conn.commit()

    assert cached is not None
    assert "Python" in cached["detected_stack"]

    rows = db.query_jobs(db_path, matching_only=True, open_only=True, summary_only=True)
    assert len(rows) == 1
    assert "Python" in rows[0]["detected_stack"]
    assert "SQLite" in rows[0]["detected_stack"]

    detail = db.get_job_detail(db_path, int(rows[0]["id"]))
    assert detail is not None
    assert detail["detected_stack"] == rows[0]["detected_stack"]


def test_upsert_job_summarizes_oversized_raw_payload(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    sources_path = tmp_path / "sources.json"
    _write_sources(sources_path, [_source_row()])
    db.import_sources_report(db_path, sources_path, create_backup=False)
    source = db.list_sources(db_path)[0]

    with db.connect(db_path) as conn:
        db.migrate_connection(conn)
        job_id = db.upsert_job(
            conn,
            source,
            {
                "job_key": "remotive:large-raw",
                "source_job_id": "large-raw",
                "title": "Large Raw Payload",
                "location": "Remote",
                "remote": True,
                "job_url": "https://example.com/jobs/large-raw",
                "apply_url": "https://example.com/jobs/large-raw/apply",
                "text": "The normalized text remains authoritative.",
                "raw": {"content": "x" * (db.MAX_RAW_JSON_BYTES * 2), "id": "large-raw"},
            },
            {"passes_filter": True, "location_modes": ["remote"]},
            {"languages": [], "frameworks": [], "domains": [], "tools": [], "groups": []},
        )
        raw_json = conn.execute("SELECT raw_json FROM jobs WHERE id = ?", (job_id,)).fetchone()["raw_json"]
        conn.commit()

    assert len(raw_json.encode("utf-8")) <= db.MAX_RAW_JSON_BYTES
    payload = json.loads(raw_json)
    assert payload["id"] == "large-raw"
    assert "_jobscraper_raw_truncated" in payload
