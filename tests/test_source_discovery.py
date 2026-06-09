import json
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

    def fake_report(*, candidates_path, out_path, limit=0):
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
