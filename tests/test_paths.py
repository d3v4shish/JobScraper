from pathlib import Path

from jobscraper import paths


def test_default_workspace_root_name() -> None:
    assert paths.default_workspace_root().name == "JobScraper"


def test_default_runtime_paths() -> None:
    assert paths.default_db_path().name == "jobs.sqlite"
    assert paths.default_sources_path().name == "sources.json"
    assert paths.log_path().parts[-2:] == ("logs", "jobscraper.log")
    assert paths.backups_dir().name == "backups"
    assert paths.reports_dir().name == "reports"


def test_packaged_icon_paths_exist() -> None:
    assert paths.app_icon_path().name == "app_icon.png"
    assert paths.app_icon_path().exists()
    assert paths.app_icon_ico_path().name == "app_icon.ico"
    assert paths.app_icon_ico_path().exists()
    assert paths.app_icon_light_path().name == "app_icon_light.png"
    assert paths.app_icon_light_path().exists()
    assert paths.app_icon_light_ico_path().name == "app_icon_light.ico"
    assert paths.app_icon_light_ico_path().exists()
