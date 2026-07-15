import json
import time
from pathlib import Path

from jobscraper.scraping import core


def test_candidate_report_row_promotable_requires_valid_importable_source() -> None:
    row = {
        "status": "valid",
        "job_count": 3,
        "sample_titles": ["Senior Systems Engineer"],
        "suggested_source": {
            "company": "Example Systems",
            "ats": "greenhouse",
            "token": "examplesystems",
            "enabled": True,
        },
    }

    assert core._candidate_report_row_is_promotable(row) is True


def test_candidate_report_row_rejects_placeholder_only_feeds() -> None:
    row = {
        "status": "valid",
        "job_count": 1,
        "sample_titles": ["Test Job"],
        "suggested_source": {
            "company": "Placeholder Inc",
            "ats": "smartrecruiters",
            "token": "placeholder",
            "enabled": True,
        },
    }

    assert core._candidate_report_row_is_promotable(row) is False


def test_candidate_report_row_rejects_mixed_demo_smartrecruiters_samples() -> None:
    row = {
        "status": "valid",
        "job_count": 25,
        "sample_titles": ["Full Stack Developer", "SN - Demo - Stage::Role", "Test Job"],
        "suggested_source": {
            "company": "Noisy SmartRecruiters",
            "ats": "smartrecruiters",
            "token": "noisy",
            "enabled": True,
        },
    }

    assert core._candidate_report_row_is_promotable(row) is False


def test_candidate_report_row_rejects_exact_test_title_but_allows_test_engineer() -> None:
    exact_test_row = {
        "status": "valid",
        "job_count": 1,
        "sample_titles": ["test"],
        "suggested_source": {
            "company": "Placeholder Inc",
            "ats": "smartrecruiters",
            "token": "placeholder",
            "enabled": True,
        },
    }
    test_engineer_row = {
        "status": "valid",
        "job_count": 1,
        "sample_titles": ["Test Engineer"],
        "suggested_source": {
            "company": "QA Inc",
            "ats": "smartrecruiters",
            "token": "qa",
            "enabled": True,
        },
    }

    assert core._candidate_report_row_is_promotable(exact_test_row) is False
    assert core._candidate_report_row_is_promotable(test_engineer_row) is True


def test_probe_and_promote_watchlist_appends_only_verified_unique_rows(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sources_path = tmp_path / "sources.json"
    watchlist_path = tmp_path / "watchlist.json"
    report_path = tmp_path / "candidate_report.json"
    sources_path.write_text(
        json.dumps(
            [
                {
                    "company": "Existing Co",
                    "ats": "greenhouse",
                    "token": "existing",
                    "enabled": True,
                }
            ]
        ),
        encoding="utf-8",
    )
    watchlist_path.write_text("[]", encoding="utf-8")

    def fake_report(*, candidates_path, out_path, limit=0, concurrency=8):
        rows = [
            {
                "company": "New Co",
                "ats": "lever",
                "status": "valid",
                "job_count": 2,
                "sample_titles": ["Backend Engineer"],
                "suggested_source": {
                    "company": "New Co",
                    "ats": "lever",
                    "token": "newco",
                    "tags": ["systems"],
                },
            },
            {
                "company": "Existing Co",
                "ats": "greenhouse",
                "status": "valid",
                "job_count": 2,
                "sample_titles": ["Backend Engineer"],
                "suggested_source": {
                    "company": "Existing Co",
                    "ats": "greenhouse",
                    "token": "existing",
                },
            },
            {
                "company": "Bad Co",
                "ats": "smartrecruiters",
                "status": "valid",
                "job_count": 1,
                "sample_titles": ["Test Job"],
                "suggested_source": {
                    "company": "Bad Co",
                    "ats": "smartrecruiters",
                    "token": "badco",
                },
            },
        ]
        Path(out_path).write_text(json.dumps(rows), encoding="utf-8")
        return rows

    monkeypatch.setattr(core, "write_candidate_discovery_report", fake_report)

    result = core.probe_and_promote_watchlist(
        candidates_path=watchlist_path,
        sources_path=sources_path,
        report_path=report_path,
    )

    rows = json.loads(sources_path.read_text(encoding="utf-8"))
    assert result["promoted"] == 1
    assert result["duplicate_skipped"] == 1
    assert result["rejected"] == 1
    assert [row["company"] for row in rows] == ["Existing Co", "New Co"]
    assert rows[1]["enabled"] is True
    assert rows[1]["discovery_notes"].startswith("Probe verified 2 importable jobs")


def test_candidate_discovery_is_bounded_parallel_and_stable(tmp_path: Path, monkeypatch) -> None:
    candidates_path = tmp_path / "watchlist.json"
    report_path = tmp_path / "candidate_report.json"
    candidates = [
        {
            "company": f"Company {index:03d}",
            "surfaces": {"greenhouse": [{"token": f"company-{index:03d}"}]},
        }
        for index in range(100)
    ]
    candidates_path.write_text(json.dumps(candidates), encoding="utf-8")
    state = {"current": 0, "max": 0}

    async def fake_probe(_session, source):
        state["current"] += 1
        state["max"] = max(state["max"], state["current"])
        try:
            await core.asyncio.sleep(0.1)
            return [
                {
                    "source_job_id": source["token"],
                    "title": "Software Engineer",
                    "location": "Remote",
                    "job_url": f"https://example.test/{source['token']}",
                }
            ]
        finally:
            state["current"] -= 1

    monkeypatch.setattr(core, "_probe_candidate_direct", fake_probe)

    started = time.perf_counter()
    report = core.write_candidate_discovery_report(
        candidates_path=candidates_path,
        out_path=report_path,
        concurrency=8,
    )
    elapsed = time.perf_counter() - started

    assert elapsed < 2.0
    assert state["max"] <= 8
    assert len(report) == 100
    assert [row["company"] for row in report] == [f"Company {index:03d}" for index in range(100)]
    assert all(row["status"] == "valid" for row in report)
