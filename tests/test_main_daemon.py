"""Unit-Tests für die Daemon-Hilfsfunktionen in ``main.py``."""

from __future__ import annotations

from pathlib import Path

import pytest

import main as daemon
from filemind.config import reload_config, set_config_file


def _write_config(tmp_path: Path, content: str) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(content, encoding="utf-8")
    set_config_file(config_path)
    reload_config()


def test_get_files_in_directory_non_recursive(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_bytes(b"a")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.txt").write_bytes(b"b")

    files = daemon._get_files_in_directory(tmp_path, recursive=False)

    assert sorted(f.name for f in files) == ["a.txt"]


def test_get_files_in_directory_recursive(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_bytes(b"a")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.txt").write_bytes(b"b")

    files = daemon._get_files_in_directory(tmp_path, recursive=True)

    assert sorted(f.name for f in files) == ["a.txt", "b.txt"]


def test_get_files_in_missing_directory_returns_empty(tmp_path: Path) -> None:
    assert daemon._get_files_in_directory(tmp_path / "missing") == []


def test_get_input_directories_supports_strings_and_dicts(tmp_path: Path) -> None:
    dir_a = tmp_path / "watch_a"
    dir_b = tmp_path / "watch_b"
    dir_c = tmp_path / "watch_c"
    _write_config(
        tmp_path,
        f"""
daemon:
  input_directories:
    - "{dir_a.as_posix()}"
    - path: "{dir_b.as_posix()}"
      action: "copy"
    - path: "{dir_c.as_posix()}"
      action: "invalid"
""",
    )

    dirs = daemon._get_input_directories()

    assert [(d["path"], d["action"]) for d in dirs] == [
        (dir_a, "move"),
        (dir_b, "copy"),
        (dir_c, "move"),  # ungültige Action fällt auf "move" zurück
    ]
    # Verzeichnisse wurden angelegt
    assert dir_a.is_dir() and dir_b.is_dir() and dir_c.is_dir()


def test_get_poll_interval_from_config(tmp_path: Path) -> None:
    _write_config(tmp_path, "daemon:\n  poll_interval: 42\n")

    assert daemon._get_poll_interval() == 42.0


@pytest.mark.parametrize("value", ["abc", -5, 0])
def test_get_poll_interval_invalid_falls_back_to_default(tmp_path: Path, value) -> None:
    _write_config(tmp_path, f"daemon:\n  poll_interval: {value}\n")

    assert daemon._get_poll_interval() == daemon.DEFAULT_POLL_INTERVAL


def test_is_file_ready_requires_stable_mtime(tmp_path: Path) -> None:
    file_path = tmp_path / "incoming.bin"
    file_path.write_bytes(b"partial")

    # Erster Kontakt: Datei wird nur vorgemerkt
    assert daemon._is_file_ready(file_path, min_stable_time=0.0) is False
    # Zweiter Kontakt mit unveränderter mtime: Datei ist bereit
    assert daemon._is_file_ready(file_path, min_stable_time=0.0) is True
