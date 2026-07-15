import json
from pathlib import Path

from jobscraper import bootstrap, paths


def test_default_settings_payload_excludes_legacy_migration() -> None:
    payload = bootstrap.default_settings_payload()

    assert "migration" not in payload
    assert payload["http_concurrency"] == 32
    assert payload["first_run_tutorial_dismissed"] is False


def test_load_settings_ignores_legacy_migration_payload(tmp_path: Path, monkeypatch) -> None:
    workspace_root = tmp_path / "workspace"
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "db_path": str(workspace_root / "custom.sqlite"),
                "migration": {
                    "completed": True,
                    "source_root": "/tmp/legacy-root",
                    "errors": ["should be ignored"],
                },
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(paths, "settings_path", lambda workspace_root=None: settings_path)
    monkeypatch.setattr(paths, "default_workspace_root", lambda: workspace_root)

    payload = bootstrap.load_settings()

    assert payload["db_path"] == str(workspace_root / "custom.sqlite")
    assert "migration" not in payload
