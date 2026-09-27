"""Unit-Tests für die Daemon-Hilfsfunktionen in ``main.py``."""

from __future__ import annotations

from pathlib import Path

import pytest

import main as daemon
from filemind import __version__
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


def test_daemon_logs_application_version_at_startup(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(daemon.signal, "signal", lambda *args: None)
    monkeypatch.setattr(daemon, "_get_input_directories", lambda: [])
    monkeypatch.setattr(daemon, "_get_poll_interval", lambda: 5.0)
    monkeypatch.setattr(daemon, "_load_config", lambda: {})

    with caplog.at_level("INFO", logger="main"), pytest.raises(SystemExit):
        daemon.run_daemon()

    assert f"filemind Version: {__version__}" in caplog.text


def test_process_file_propagates_routing_failure_during_reprocessing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    file_path = tmp_path / "stored.txt"
    file_path.write_text("content", encoding="utf-8")
    monkeypatch.setattr(daemon, "route_file", lambda *args, **kwargs: False)

    assert daemon._process_file(file_path, reprocess_processed=True) is False
    assert daemon._process_file(file_path, reprocess_processed=False) is True


def test_scan_input_directories_reports_file_and_directory_counts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    input_dir = tmp_path / "input"
    nested_dir = input_dir / "nested"
    nested_dir.mkdir(parents=True)
    (input_dir / "one.txt").write_text("one", encoding="utf-8")
    (nested_dir / "two.txt").write_text("two", encoding="utf-8")
    monkeypatch.setattr(daemon, "_running", True)
    monkeypatch.setattr(daemon, "_is_file_ready", lambda path: True)
    monkeypatch.setattr(daemon, "_process_file", lambda path, action: True)

    totals = daemon._scan_input_directories([{"path": input_dir, "action": "copy"}])

    assert totals == {
        "directories_scanned": 2,
        "files_seen": 2,
        "files_ready": 2,
        "files_completed": 2,
        "files_failed": 0,
    }


def test_reprocess_files_are_forced_once_and_empty_storage_subdirectories_removed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    storage_root = tmp_path / "storage"
    empty_dir = storage_root / "empty" / "nested"
    empty_dir.mkdir(parents=True)
    file_path = empty_dir / "already-processed.txt"
    file_path.write_text("content", encoding="utf-8")
    reprocess_files = {file_path}
    calls = {"path": None, "action": None, "reprocess": False}
    progress_events = []

    def fake_process(path, action, reprocess_processed=False):
        calls.update(path=path, action=action, reprocess=reprocess_processed)
        path.unlink()
        return True

    monkeypatch.setattr(daemon, "_process_file", fake_process)
    monkeypatch.setattr(
        daemon,
        "record_event",
        lambda event, **fields: progress_events.append({"event": event, **fields}),
    )

    summary = daemon._reprocess_existing_files(reprocess_files, [storage_root])

    assert calls == {"path": file_path, "action": "move", "reprocess": True}
    assert reprocess_files == set()
    assert summary == {
        "files_total": 1,
        "files_attempted": 1,
        "files_completed": 1,
        "files_failed": 0,
        "files_missing": 0,
        "files_in_flight": 0,
        "files_remaining": 0,
    }
    assert [event["status"] for event in progress_events] == ["started", "completed"]
    assert progress_events[-1]["files_completed"] == 1
    assert "path" not in progress_events[-1]
    assert storage_root.is_dir()
    assert not (storage_root / "empty").exists()


def test_failed_reprocessing_file_stays_pending_for_retry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    file_path = storage_root / "stored.txt"
    file_path.write_text("content", encoding="utf-8")
    reprocess_files = {file_path}
    progress_events = []
    monkeypatch.setattr(daemon, "_process_file", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        daemon,
        "record_event",
        lambda event, **fields: progress_events.append({"event": event, **fields}),
    )

    summary = daemon._reprocess_existing_files(reprocess_files, [storage_root])

    assert summary["files_attempted"] == 1
    assert summary["files_completed"] == 0
    assert summary["files_failed"] == 1
    assert summary["files_remaining"] == 1
    assert reprocess_files == {file_path}
    assert progress_events[-1]["status"] == "incomplete"
    assert progress_events[-1]["files_failed"] == 1


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
