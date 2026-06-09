import tomllib
from pathlib import Path


def test_public_package_has_no_fragile_scraping_dependencies() -> None:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    payload = tomllib.loads(pyproject.read_text(encoding="utf-8"))

    dependencies = set(payload["project"]["dependencies"])
    assert "python-jobspy" not in dependencies
    assert "playwright" not in dependencies
    assert "PyQt6-WebEngine" not in dependencies
    assert "jobspy" not in payload["project"]["optional-dependencies"]
