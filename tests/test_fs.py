import json
import os
import stat
from pathlib import Path

from jobscraper.storage import fs


def test_atomic_write_json_is_private_and_complete(tmp_path: Path) -> None:
    path = tmp_path / "config" / "session.json"
    fs.atomic_write_json(path, {"cookies": [{"name": "sid", "value": "secret"}]})

    assert json.loads(path.read_text(encoding="utf-8"))["cookies"][0]["value"] == "secret"
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == fs.PRIVATE_FILE_MODE
        assert stat.S_IMODE(path.parent.stat().st_mode) == fs.PRIVATE_DIR_MODE


def test_atomic_write_relative_file_does_not_rechmod_cwd(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    before = stat.S_IMODE(tmp_path.stat().st_mode) if os.name != "nt" else None

    fs.atomic_write_json("export.json", [{"id": 1}])

    assert json.loads((tmp_path / "export.json").read_text(encoding="utf-8")) == [{"id": 1}]
    if os.name != "nt":
        assert stat.S_IMODE(tmp_path.stat().st_mode) == before
