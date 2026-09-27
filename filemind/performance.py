"""Structured, privacy-conscious performance diagnostics for filemind."""

from __future__ import annotations

import contextvars
import json
import logging
import os
import platform
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from importlib import metadata
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

import psutil

from filemind import __version__
from filemind.config import get_language, get_section

_logger = logging.getLogger("filemind.performance")
_process = psutil.Process()
_context: contextvars.ContextVar[Dict[str, Any]] = contextvars.ContextVar(
    "filemind_performance_context", default={}
)
_handler: Optional[RotatingFileHandler] = None
_enabled = False
_run_id: Optional[str] = None
_run_started_ns: Optional[int] = None


def _package_version(package: str) -> Optional[str]:
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return None


def _safe_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return None


def _input_action_counts(input_directories: Any) -> Dict[str, int]:
    if isinstance(input_directories, str):
        entries = [input_directories]
    elif isinstance(input_directories, list):
        entries = input_directories
    else:
        entries = []

    counts = {"move": 0, "copy": 0}
    for entry in entries:
        action = entry.get("action", "move") if isinstance(entry, dict) else "move"
        action = str(action).lower()
        counts[action if action in counts else "move"] += 1
    return counts


def _configuration_snapshot(
    logging_config: Dict[str, Any],
    performance_config: Dict[str, Any],
    daemon_config: Dict[str, Any],
    storage_config: Dict[str, Any],
    classification_config: Dict[str, Any],
    ai_config: Dict[str, Any],
    max_log_mb: int,
    backup_count: int,
) -> Dict[str, Any]:
    extension_lists = {
        key: [item for item in classification_config.get(key, []) if isinstance(item, str)]
        for key in (
            "image_extensions",
            "text_document_extensions",
            "video_extensions",
            "audio_extensions",
            "archive_extensions",
        )
    }
    return {
        "language": get_language(),
        "logging": {
            "level": _safe_scalar(logging_config.get("level", "INFO")),
            "retention_days": _safe_scalar(logging_config.get("retention_days", 7)),
            "console_enabled": bool(logging_config.get("console_enabled", True)),
            "console_level": _safe_scalar(logging_config.get("console_level", "WARNING")),
        },
        "performance": {
            "enabled": bool(performance_config.get("enabled", True)),
            "max_log_mb": max_log_mb,
            "backup_count": backup_count,
        },
        "daemon": {
            "poll_interval_seconds": _safe_scalar(daemon_config.get("poll_interval", 5)),
            "input_directory_count": (
                1
                if isinstance(daemon_config.get("input_directories"), str)
                else len(daemon_config.get("input_directories") or [])
                if isinstance(daemon_config.get("input_directories"), list)
                else 0
            ),
            "input_action_counts": _input_action_counts(
                daemon_config.get("input_directories")
            ),
            "reprocess_processed": bool(daemon_config.get("reprocess_processed", False)),
        },
        "storage": {
            "max_files_per_folder": _safe_scalar(
                storage_config.get("max_files_per_folder", 3000)
            ),
            "reinit_hash_store": bool(storage_config.get("reinit_hash_store", False)),
        },
        "classification": {
            "document_image_min_words": _safe_scalar(
                classification_config.get("document_image_min_words", 8)
            ),
            "document_image_max_colorfulness": _safe_scalar(
                classification_config.get("document_image_max_colorfulness", 18.0)
            ),
            "extensions": extension_lists,
        },
        "ai": {
            "enabled": bool(ai_config.get("enabled", False)),
            "provider": _safe_scalar(ai_config.get("provider")),
            "model": _safe_scalar(os.environ.get("OLLAMA_MODEL") or ai_config.get("model")),
            "timeout_seconds": _safe_scalar(ai_config.get("timeout", 300)),
            "max_image_size": _safe_scalar(ai_config.get("max_image_size", 1024)),
            "num_ctx": _safe_scalar(ai_config.get("num_ctx", 8192)),
        },
    }


def _emit(event: Dict[str, Any]) -> None:
    if not _enabled:
        return
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "run_id": _run_id,
        "pid": _process.pid,
        **event,
    }
    _logger.info(json.dumps(record, ensure_ascii=True, separators=(",", ":"), sort_keys=True))


def record_event(event: str, **fields: Any) -> None:
    """Emit a structured, non-file event such as bounded daemon progress."""
    _emit({**fields, **_context.get(), "event": event})


def initialize_performance() -> bool:
    """Enable bounded JSONL reporting and emit host/runtime metadata."""
    global _enabled, _handler, _run_id, _run_started_ns

    config = get_section("performance", {}) or {}
    if not config.get("enabled", True):
        _enabled = False
        return False

    logging_config = get_section("logging", {}) or {}
    log_dir = Path(logging_config.get("log_dir", Path.cwd() / ".filemind" / "logs"))
    log_dir.mkdir(parents=True, exist_ok=True)

    try:
        max_log_mb = max(1, int(config.get("max_log_mb", 10)))
    except (TypeError, ValueError):
        max_log_mb = 10
    try:
        backup_count = max(1, int(config.get("backup_count", 3)))
    except (TypeError, ValueError):
        backup_count = 3

    target = log_dir / "performance.jsonl"
    if _handler is None or Path(_handler.baseFilename) != target.resolve():
        if _handler is not None:
            _logger.removeHandler(_handler)
            _handler.close()
        _handler = RotatingFileHandler(
            target,
            maxBytes=max_log_mb * 1024 * 1024,
            backupCount=backup_count,
            encoding="utf-8",
        )
        _handler.setFormatter(logging.Formatter("%(message)s"))
        _logger.addHandler(_handler)

    _logger.setLevel(logging.INFO)
    _logger.propagate = False
    _enabled = True
    _run_id = uuid.uuid4().hex
    _run_started_ns = time.perf_counter_ns()

    ai_config = get_section("ai", {}) or {}
    classification_config = get_section("classification", {}) or {}
    storage_config = get_section("storage", {}) or {}
    try:
        memory = psutil.virtual_memory()
        total_memory = memory.total
        available_memory = memory.available
    except Exception:
        total_memory = None
        available_memory = None
    try:
        cpu_frequency = psutil.cpu_freq()
        current_cpu_frequency = cpu_frequency.current if cpu_frequency else None
        max_cpu_frequency = cpu_frequency.max if cpu_frequency else None
    except Exception:
        current_cpu_frequency = None
        max_cpu_frequency = None
    daemon_config = get_section("daemon", {}) or {}

    system = {
        "application_version": __version__,
        "os": platform.platform(),
        "architecture": platform.machine(),
        "processor": platform.processor() or None,
        "cpu_logical_count": psutil.cpu_count(logical=True),
        "cpu_physical_count": psutil.cpu_count(logical=False),
        "cpu_frequency_current_mhz": current_cpu_frequency,
        "cpu_frequency_max_mhz": max_cpu_frequency,
        "memory_total_bytes": total_memory,
        "memory_available_bytes": available_memory,
        "python": sys.version.split()[0],
        "packages": {
            name: _package_version(name)
            for name in ("easyocr", "torch", "torchvision", "psutil")
        },
        "ocr": {"engine": "easyocr", "device": "cpu", "quantize": False},
        "ai": {
            "enabled": bool(ai_config.get("enabled", False)),
            "provider": ai_config.get("provider"),
            "model": os.environ.get("OLLAMA_MODEL") or ai_config.get("model"),
            "max_image_size": ai_config.get("max_image_size"),
        },
        "daemon": {
            "poll_interval_seconds": daemon_config.get("poll_interval"),
            "input_directory_count": (
                1
                if isinstance(daemon_config.get("input_directories"), str)
                else len(daemon_config.get("input_directories") or [])
                if isinstance(daemon_config.get("input_directories"), list)
                else 0
            ),
        },
        "configuration": _configuration_snapshot(
            logging_config,
            config,
            daemon_config,
            storage_config,
            classification_config,
            ai_config,
            max_log_mb,
            backup_count,
        ),
    }
    try:
        system["process_rss_bytes"] = _process.memory_info().rss
    except Exception:
        system["process_rss_bytes"] = None
    _emit({"event": "run_start", "system": system})
    return True


def finish_performance() -> None:
    """Emit one run summary and flush the performance log."""
    if not _enabled:
        return
    elapsed_ms = None
    if _run_started_ns is not None:
        elapsed_ms = (time.perf_counter_ns() - _run_started_ns) / 1_000_000
    try:
        rss_bytes = _process.memory_info().rss
    except Exception:
        rss_bytes = None
    _emit({"event": "run_end", "duration_ms": elapsed_ms, "rss_bytes": rss_bytes})
    if _handler is not None:
        _handler.flush()


@contextmanager
def file_context(path: Path) -> Iterator[str]:
    """Correlate a file's stage records without recording its name or path."""
    if not _enabled:
        yield ""
        return
    existing_context = _context.get()
    if "file_id" in existing_context:
        yield existing_context["file_id"]
        return
    path = Path(path)
    try:
        size_bytes: Optional[int] = path.stat().st_size
    except OSError:
        size_bytes = None
    file_id = uuid.uuid4().hex
    token = _context.set(
        {
            **_context.get(),
            "file_id": file_id,
            "file_size_bytes": size_bytes,
            "file_extension": path.suffix.lower(),
        }
    )
    try:
        yield file_id
    finally:
        _context.reset(token)


@contextmanager
def measure(stage: str, **fields: Any) -> Iterator[Dict[str, Any]]:
    """Measure one operation's wall/CPU time and process RSS change."""
    mutable_fields = dict(fields)
    if not _enabled:
        yield mutable_fields
        return

    wall_start = time.perf_counter_ns()
    cpu_start = time.process_time_ns()
    try:
        rss_start: Optional[int] = _process.memory_info().rss
    except Exception:
        rss_start = None
    error_type: Optional[str] = None

    try:
        yield mutable_fields
    except BaseException as exc:
        error_type = type(exc).__name__
        raise
    finally:
        wall_end = time.perf_counter_ns()
        cpu_end = time.process_time_ns()
        try:
            rss_end: Optional[int] = _process.memory_info().rss
        except Exception:
            rss_end = None

        event = {
            **_context.get(),
            **mutable_fields,
            "event": "measurement",
            "stage": stage,
            "status": "error" if error_type else "ok",
            "duration_ms": (wall_end - wall_start) / 1_000_000,
            "process_cpu_ms": (cpu_end - cpu_start) / 1_000_000,
            "rss_before_bytes": rss_start,
            "rss_after_bytes": rss_end,
            "rss_delta_bytes": rss_end - rss_start if rss_start is not None and rss_end is not None else None,
        }
        bytes_processed = event.get("bytes_processed")
        if isinstance(bytes_processed, (int, float)) and wall_end > wall_start:
            event["throughput_mib_per_sec"] = bytes_processed / 1024**2 / (
                (wall_end - wall_start) / 1_000_000_000
            )
        if error_type:
            event["error_type"] = error_type
        _emit(event)
