from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

import pytest

from filemind import __version__, performance
from filemind.integrations import ocr


@pytest.fixture
def performance_log(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    previous_handler = performance._handler
    if previous_handler is not None:
        performance._logger.removeHandler(previous_handler)
        previous_handler.close()
    performance._handler = None
    performance._enabled = False

    sections = {
        "performance": {"enabled": True, "max_log_mb": 1, "backup_count": 1},
        "logging": {
            "log_dir": str(tmp_path),
            "level": "DEBUG",
            "retention_days": 9,
            "console_enabled": False,
            "console_level": "ERROR",
        },
        "daemon": {
            "poll_interval": 42,
            "input_directories": [
                {"path": "/private/input-a", "action": "copy"},
                {"path": "/private/input-b", "action": "move"},
            ],
            "reprocess_processed": True,
        },
        "storage": {
            "base_media_path": "/private/media",
            "max_files_per_folder": 1234,
            "reinit_hash_store": False,
        },
        "classification": {
            "document_image_min_words": 15,
            "document_image_max_colorfulness": 13.0,
            "image_extensions": [".jpg", ".png"],
        },
        "ai": {
            "enabled": True,
            "provider": "test",
            "model": "test-model",
            "timeout": 901,
            "max_image_size": 640,
            "num_ctx": 16384,
            "url": "http://private-host:11434",
        },
    }
    monkeypatch.setattr(
        performance,
        "get_section",
        lambda section, default=None: sections.get(section, default or {}),
    )
    monkeypatch.setattr(performance, "get_language", lambda: "de")
    performance.initialize_performance()
    log_path = tmp_path / "performance.jsonl"

    yield log_path

    handler = performance._handler
    if handler is not None:
        performance._logger.removeHandler(handler)
        handler.close()
    performance._handler = previous_handler
    performance._enabled = False
    if previous_handler is not None:
        performance._logger.addHandler(previous_handler)
    performance._logger.propagate = True


def _read_events(log_path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]


def test_measurement_records_correlated_machine_metrics_without_file_path(
    performance_log: Path, tmp_path: Path
) -> None:
    image = tmp_path / "private-document-name.jpg"
    image.write_bytes(b"image-data")

    with performance.file_context(image) as file_id:
        with performance.measure("ocr.readtext", engine="easyocr") as fields:
            fields["characters_detected"] = 17

    events = _read_events(performance_log)
    start_event, measurement = events

    assert start_event["event"] == "run_start"
    assert start_event["system"]["application_version"] == __version__
    assert start_event["system"]["cpu_logical_count"] is not None
    assert start_event["system"]["memory_total_bytes"] > 0
    config = start_event["system"]["configuration"]
    assert config["language"] == "de"
    assert config["daemon"]["poll_interval_seconds"] == 42
    assert config["daemon"]["reprocess_processed"] is True
    assert config["daemon"]["input_action_counts"] == {"move": 1, "copy": 1}
    assert config["storage"]["max_files_per_folder"] == 1234
    assert config["classification"]["document_image_min_words"] == 15
    assert config["ai"]["timeout_seconds"] == 901
    assert config["ai"]["max_image_size"] == 640
    assert config["ai"]["num_ctx"] == 16384
    assert "/private" not in json.dumps(config)
    assert "private-host" not in json.dumps(config)
    assert measurement["event"] == "measurement"
    assert measurement["stage"] == "ocr.readtext"
    assert measurement["file_id"] == file_id
    assert measurement["file_size_bytes"] == len(b"image-data")
    assert measurement["file_extension"] == ".jpg"
    assert measurement["duration_ms"] >= 0
    assert measurement["process_cpu_ms"] >= 0
    assert "rss_after_bytes" in measurement
    assert measurement["characters_detected"] == 17
    assert "private-document-name" not in performance_log.read_text(encoding="utf-8")


def test_failed_measurement_records_error_type_and_reraises(performance_log: Path) -> None:
    with pytest.raises(ValueError, match="expected failure"):
        with performance.measure("storage.copy"):
            raise ValueError("expected failure")

    measurement = _read_events(performance_log)[-1]
    assert measurement["stage"] == "storage.copy"
    assert measurement["status"] == "error"
    assert measurement["error_type"] == "ValueError"
    assert "expected failure" not in json.dumps(measurement)


def test_record_event_emits_structured_status_without_file_path(
    performance_log: Path, tmp_path: Path
) -> None:
    performance.record_event(
        "daemon.reprocess_progress",
        files_total=5,
        files_attempted=2,
        files_remaining=3,
    )

    event = _read_events(performance_log)[-1]
    assert event["event"] == "daemon.reprocess_progress"
    assert event["files_total"] == 5
    assert event["files_attempted"] == 2
    assert "path" not in event
    assert str(tmp_path) not in json.dumps(event)


def test_ocr_measurement_includes_recognized_character_count(
    monkeypatch: pytest.MonkeyPatch, performance_log: Path, tmp_path: Path
) -> None:
    class FakeReader:
        def readtext(self, path: str, detail: int, paragraph: bool) -> list[str]:
            return ["some", " text"]

    monkeypatch.setattr(ocr, "_get_ocr_reader", lambda: FakeReader())

    assert ocr._perform_ocr(tmp_path / "sample.jpg") == "some text"

    measurement = next(event for event in _read_events(performance_log) if event.get("stage") == "ocr.readtext")
    assert measurement["characters_detected"] == len("some text")
