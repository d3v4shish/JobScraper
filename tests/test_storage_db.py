import json
from pathlib import Path

import pytest

from jobscraper import paths
from jobscraper.storage import db


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
    preview_source_import_task = pytest.importorskip("jobscraper.ui.tasks").preview_source_import_task
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
    assert preview["total"] >= 280

    rows = json.loads(paths.bundled_sources_path().read_text(encoding="utf-8"))
    missing = sorted({str(row["ats"]) for row in rows} - set(core.ADAPTERS))
    assert missing == []
    bundled_identities = {
        (str(row["company"]), str(row["ats"]), str(row.get("token") or ""))
        for row in rows
    }
    expected_batch_a_identities = {
        ("Docker", "ashby", "docker"),
        ("Mozilla", "greenhouse", "mozilla"),
        ("Cohere", "ashby", "cohere"),
        ("Mistral AI", "lever", "mistral"),
        ("Lambda Labs", "ashby", "lambda"),
        ("DigitalOcean", "greenhouse", "digitalocean98"),
        ("ElevenLabs", "ashby", "elevenlabs"),
        ("Runway", "ashby", "runway"),
        ("xAI", "greenhouse", "xai"),
        ("Harvey", "ashby", "harvey"),
        ("Decagon", "ashby", "decagon"),
        ("LlamaIndex", "ashby", "llamaindex"),
        ("Vapi", "ashby", "vapi"),
        ("Physical Intelligence", "ashby", "physicalintelligence"),
        ("Crusoe", "ashby", "crusoe"),
        ("Nebius", "greenhouse", "nebius"),
        ("Figure AI", "greenhouse", "figureai"),
        ("MinIO", "greenhouse", "minio"),
    }
    expected_batch_b_identities = {
        ("Wayve", "greenhouse", "wayve"),
    }
    expected_batch_d_identities = {
        ("Palo Alto Networks", "paloalto_search", ""),
        ("Two Sigma", "twosigma_search", ""),
    }
    assert expected_batch_a_identities <= bundled_identities
    assert expected_batch_b_identities <= bundled_identities
    assert expected_batch_d_identities <= bundled_identities

    expected_new_sources = {
        "arbeitnow_api",
        "ai_jobs_search",
        "arc_dev_search",
        "builtin_jobs",
        "climatebase_jobs",
        "cutshort_search",
        "cybersecjobs_search",
        "data_jobs_search",
        "datayoshi_search",
        "devsnap_search",
        "echojobs_search",
        "flexjobs_search",
        "foundit_search",
        "golangprojects_search",
        "hiringcafe_search",
        "himalayas_search",
        "hirist_search",
        "instahyre_search",
        "aijobsnet_search",
        "amazon_jobs",
        "apple_jobs",
        "ibm_careers",
        "jobicy_api",
        "jobs24x_search",
        "jobspresso_search",
        "justremote_search",
        "levels_fyi_jobs",
        "microsoft_careers",
        "ml_jobs_search",
        "naukri_search",
        "nodesk_search",
        "oracle_careers",
        "otta_search",
        "paloalto_search",
        "remote_co_search",
        "remote_rocketship_search",
        "remotefront_search",
        "rust_jobs_search",
        "themuse_api",
        "timesjobs_search",
        "twosigma_search",
        "uber_careers",
        "underdog_search",
        "welcome_to_the_jungle_search",
        "workingnomads_api",
        "workingnomads_search",
        "yc_work_at_startup",
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
    assert preview["total"] > 280

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


def _upsert_search_job(
    db_path: Path,
    source: dict[str, object],
    *,
    key: str,
    title: str,
    company: str = "Searchable Co",
    location: str = "Remote",
    department: str = "Engineering",
    text: str = "Build reliable services with Python.",
) -> None:
    with db.connect(db_path) as conn:
        db.migrate_connection(conn)
        db.upsert_job(
            conn,
            source,
            {
                "job_key": key,
                "source_job_id": key,
                "company": company,
                "ats": source["ats"],
                "title": title,
                "location": location,
                "department": department,
                "employment_type": "Full-time",
                "remote": True,
                "job_url": f"https://example.test/{key}",
                "apply_url": f"https://example.test/{key}/apply",
                "text": text,
                "raw": {},
            },
            {
                "matched_required_words": [],
                "matched_include_words": [],
                "matched_include_group": [],
                "matched_builtin_groups": [],
                "location_modes": ["remote"],
                "interest_tags": ["Python"],
                "passes_filter": True,
            },
            {"languages": ["Python"], "frameworks": [], "domains": [], "tools": [], "groups": []},
        )
        conn.commit()


def test_job_text_search_uses_and_maintains_fts(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    sources_path = tmp_path / "sources.json"
    _write_sources(sources_path, [_source_row("Searchable Co")])
    db.import_sources_report(db_path, sources_path, create_backup=False)
    source = db.list_sources(db_path)[0]

    _upsert_search_job(db_path, source, key="title", title="Linux Platform Engineer")
    _upsert_search_job(db_path, source, key="company", title="Backend Engineer", company="Kernel Labs")
    _upsert_search_job(db_path, source, key="location", title="Backend Engineer", location="Bengaluru")
    _upsert_search_job(db_path, source, key="department", title="Backend Engineer", department="Infrastructure")
    _upsert_search_job(db_path, source, key="text", title="Backend Engineer", text="Own observability for storage clusters.")

    assert [row["job_key"] for row in db.query_jobs(db_path, matching_only=False, search="linux", limit=10)] == ["title"]
    assert [row["job_key"] for row in db.query_jobs(db_path, matching_only=False, search="kernel", limit=10)] == ["company"]
    assert [row["job_key"] for row in db.query_jobs(db_path, matching_only=False, search="bengaluru", limit=10)] == ["location"]
    assert [row["job_key"] for row in db.query_jobs(db_path, matching_only=False, search="infrastructure", limit=10)] == ["department"]
    assert [row["job_key"] for row in db.query_jobs(db_path, matching_only=False, search="observability", limit=10)] == ["text"]

    with db.connect(db_path) as conn:
        db.migrate_connection(conn)
        fts_count = conn.execute("SELECT COUNT(*) AS c FROM jobs_fts").fetchone()["c"]
        conn.execute("DELETE FROM jobs WHERE job_key = 'title'")
        conn.commit()
    assert fts_count == 5
    assert db.query_jobs(db_path, matching_only=False, search="linux", limit=10) == []


def test_job_text_search_rebuilds_stale_fts(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    sources_path = tmp_path / "sources.json"
    _write_sources(sources_path, [_source_row("Searchable Co")])
    db.import_sources_report(db_path, sources_path, create_backup=False)
    source = db.list_sources(db_path)[0]
    _upsert_search_job(db_path, source, key="title", title="Linux Platform Engineer")

    with db.connect(db_path) as conn:
        db.migrate_connection(conn)
        conn.execute("DELETE FROM jobs_fts")
        conn.commit()

    assert db.query_jobs(db_path, matching_only=False, search="linux", limit=10) == []

    db._MIGRATED_DB_PATHS.clear()
    db.init_db(db_path)

    rows = db.query_jobs(db_path, matching_only=False, search="linux", limit=10)
    assert [row["job_key"] for row in rows] == ["title"]


def test_export_jobs_json_uses_batched_details(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "jobs.sqlite"
    sources_path = tmp_path / "sources.json"
    out_path = tmp_path / "jobs.json"
    _write_sources(sources_path, [_source_row("Export Co")])
    db.import_sources_report(db_path, sources_path, create_backup=False)
    source = db.list_sources(db_path)[0]
    _upsert_search_job(db_path, source, key="export-1", title="Export Engineer")
    _upsert_search_job(db_path, source, key="export-2", title="Export Platform Engineer")
    first_job = db.query_jobs(db_path, matching_only=False, open_only=True, search="export", limit=1)[0]
    expected_detail = db.get_job_detail(db_path, int(first_job["id"]))
    assert expected_detail is not None

    def fail_per_row_detail(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("export must not call get_job_detail per row")

    original_export_detail_rows = db._export_detail_rows
    batched_calls: list[list[int]] = []

    def record_batched_details(conn: object, job_ids: list[int]) -> dict[int, object]:
        batched_calls.append(list(job_ids))
        return original_export_detail_rows(conn, job_ids)

    monkeypatch.setattr(db, "get_job_detail", fail_per_row_detail)
    monkeypatch.setattr(db, "get_job_details", fail_per_row_detail)
    monkeypatch.setattr(db, "_export_detail_rows", record_batched_details)

    count = db.export_jobs_json(db_path, out_path, matching_only=False, open_only=True)

    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert count == 2
    assert len(payload) == 2
    assert len(batched_calls) == 1
    assert len(batched_calls[0]) == 2
    assert all("raw_json" not in row for row in payload)
    assert all("raw" in row for row in payload)
    exported_first = next(row for row in payload if row["id"] == expected_detail["id"])
    assert set(exported_first) == set(expected_detail)


def _prepared_job_rows(count: int) -> list[tuple[dict[str, object], dict[str, object], dict[str, list[str]]]]:
    rows: list[tuple[dict[str, object], dict[str, object], dict[str, list[str]]]] = []
    for index in range(count):
        rows.append(
            (
                {
                    "job_key": f"batch:{index}",
                    "source_job_id": f"batch-{index}",
                    "company": "Batch Co",
                    "ats": "remotive_api",
                    "title": f"Batch Engineer {index}",
                    "location": "Remote",
                    "department": "Engineering",
                    "employment_type": "Full-time",
                    "remote": True,
                    "job_url": f"https://example.test/batch/{index}",
                    "apply_url": f"https://example.test/batch/{index}/apply",
                    "text": "Remote Python Linux platform engineering.",
                    "raw": {"id": index},
                },
                {
                    "matched_required_words": [],
                    "matched_include_words": ["python"],
                    "matched_include_group": [],
                    "matched_builtin_groups": [],
                    "location_modes": ["remote"],
                    "interest_tags": ["Python"],
                    "passes_filter": index % 2 == 0,
                },
                {
                    "languages": ["Python"],
                    "frameworks": [],
                    "domains": ["Systems"],
                    "tools": ["Linux"],
                    "groups": [],
                },
            )
        )
    return rows


def test_upsert_jobs_batch_is_idempotent_and_preserves_related_counts(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    sources_path = tmp_path / "sources.json"
    _write_sources(sources_path, [_source_row("Batch Co")])
    db.import_sources_report(db_path, sources_path, create_backup=False)
    source = db.list_sources(db_path)[0]
    prepared_rows = _prepared_job_rows(12)

    with db.connect(db_path) as conn:
        db.migrate_connection(conn)
        ids_by_key = db.upsert_jobs_batch(conn, source, prepared_rows, seen_at=123)
        db.upsert_jobs_batch(conn, source, prepared_rows, seen_at=456)
        counts = {
            "jobs": conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0],
            "matches": conn.execute("SELECT COUNT(*) FROM job_matches").fetchone()[0],
            "stack": conn.execute("SELECT COUNT(*) FROM job_stack").fetchone()[0],
            "summary": conn.execute("SELECT COUNT(*) FROM job_stack_summary").fetchone()[0],
            "matching": conn.execute("SELECT COUNT(*) FROM job_matches WHERE passes_filter = 1").fetchone()[0],
            "fts": conn.execute("SELECT COUNT(*) FROM jobs_fts").fetchone()[0],
        }
        conn.commit()

    assert len(ids_by_key) == 12
    assert counts == {
        "jobs": 12,
        "matches": 12,
        "stack": 48,
        "summary": 12,
        "matching": 6,
        "fts": 12,
    }
