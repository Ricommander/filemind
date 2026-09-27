"""Daemon-Einstiegspunkt für filemind.

Dieses Modul implementiert den Hauptprozess von filemind. Es überwacht
eingeconfigurierte Input-Ordner auf neue Dateien und leitet diese
an das Routing-System weiter.

Funktionalität:
- Überwacht ein oder mehrere Input-Ordner.
- Nutzt Polling zur Dateierkennung (watchdog optional).
- Für jede neue Datei: routing.route_file(path).
- Langläufiger Daemon-Prozess mit Signal-Handling.
- Robuste Fehlerbehandlung pro Datei.
- Nur Orchestrierung, keine Business-Logik.

Konfiguration: config.yaml
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Set

from filemind import __version__
from filemind.config import get_config_file, get_section, set_config_file
from filemind.performance import finish_performance, initialize_performance, measure, record_event
from filemind.routing.router import route_file

logger = logging.getLogger(__name__)

# Konstanten
CONFIG_FILE = get_config_file()
DEFAULT_POLL_INTERVAL = 5  # Sekunden
DEFAULT_INPUT_DIRS = [Path.cwd() / "input"]

# Globale State
_running = True
_watched_files: Dict[str, float] = {}  # Pfad -> Last Modified Time
_lock = threading.Lock()


def _load_config() -> Dict[str, Any]:
    """Lädt die Daemon-Konfiguration aus der zentralen filemind-Konfiguration."""
    return get_section("daemon", {})


def _get_input_directories() -> List[Dict[str, object]]:
    """Besorgt die Input-Ordner aus Konfiguration oder Defaults.

    Returns:
            Liste von Input-Verzeichnissen.
    """
    daemon_config = _load_config()
    input_dirs_raw = daemon_config.get("input_directories", None)

    # Support two shapes in config:
    # - list of strings: ["/path/to/dir", ...]
    # - list of dicts: [{"path": "/path", "action": "copy"}, ...]
    dirs: List[Dict[str, object]] = []
    if input_dirs_raw is None:
        # Fallback to default
        dirs = [{"path": DEFAULT_INPUT_DIRS[0], "action": "move"}]
    elif isinstance(input_dirs_raw, str):
        dirs = [{"path": Path(input_dirs_raw), "action": "move"}]
    elif isinstance(input_dirs_raw, list):
        for d in input_dirs_raw:
            if isinstance(d, str):
                dirs.append({"path": Path(d), "action": "move"})
            elif isinstance(d, dict):
                p = Path(d.get("path") or d.get("dir") or d.get("directory"))
                action = str(d.get("action", "move")).lower()
                if action not in ("move", "copy"):
                    action = "move"
                dirs.append({"path": p, "action": action})
    else:
        dirs = [{"path": DEFAULT_INPUT_DIRS[0], "action": "move"}]

    # Erstelle Ordner falls nötig
    valid_dirs: List[Dict[str, object]] = []
    for entry in dirs:
        input_dir = entry["path"]
        try:
            input_dir.mkdir(parents=True, exist_ok=True)
            valid_dirs.append(entry)
            logger.info(f"Input-Ordner konfiguriert: {input_dir} (action={entry['action']})")
        except Exception as e:
            logger.error(f"Fehler beim Erstellen von Input-Ordner {input_dir}: {e}")

    return valid_dirs


def _get_poll_interval() -> float:
    """Besorgt das Polling-Intervall aus Konfiguration.

    Returns:
            Polling-Intervall in Sekunden.
    """
    daemon_config = _load_config()
    interval = daemon_config.get("poll_interval", DEFAULT_POLL_INTERVAL)

    try:
        interval = float(interval)
        if interval <= 0:
            raise ValueError("Polling-Intervall muss > 0 sein")
        return interval
    except (ValueError, TypeError):
        logger.warning(
            f"Ungültiges Polling-Intervall: {interval}, " f"nutze Default: {DEFAULT_POLL_INTERVAL}s"
        )
        return DEFAULT_POLL_INTERVAL


def _signal_handler(signum: int, frame) -> None:
    """Signal-Handler für sauberes Beenden.

    Args:
            signum: Signal-Nummer.
            frame: Stack-Frame.
    """
    global _running

    sig_name = signal.Signals(signum).name
    logger.info(f"Signal {sig_name} empfangen, fahre herunter...")
    _running = False


def _get_files_in_directory_impl(directory: Path, recursive: bool = False) -> List[Path]:
    """Besorgt alle Dateien in einem Verzeichnis.

    Args:
            directory: Verzeichnispfad.
            recursive: Falls True, auch Unterverzeichnisse durchsuchen.

    Returns:
            Liste von Dateipfaden.
    """
    files = []

    try:
        if not directory.exists():
            logger.debug(f"Verzeichnis existiert nicht: {directory}")
            return files

        if recursive:
            pattern = "**/*"
        else:
            pattern = "*"

        for item in directory.glob(pattern):
            if item.is_file():
                files.append(item)

    except Exception as e:
        logger.warning(f"Fehler beim Durchsuchen von {directory}: {e}")

    return files


def _get_files_in_directory(directory: Path, recursive: bool = False) -> List[Path]:
    with measure("daemon.directory_scan", recursive=recursive) as measurement:
        files = _get_files_in_directory_impl(directory, recursive=recursive)
        measurement["files_found"] = len(files)
        return files


def _get_directory_contents_impl(directory: Path) -> tuple[List[Path], List[Path]]:
    """Liest direkte Dateien und Unterordner mit nur einer Verzeichnisabfrage."""
    files: List[Path] = []
    subdirectories: List[Path] = []

    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                try:
                    if entry.is_file(follow_symlinks=True):
                        files.append(Path(entry.path))
                    elif entry.is_dir(follow_symlinks=True):
                        subdirectories.append(Path(entry.path))
                except OSError as e:
                    logger.warning(f"Fehler beim Prüfen von {entry.path}: {e}")
    except FileNotFoundError:
        logger.debug(f"Verzeichnis existiert nicht: {directory}")
    except OSError as e:
        logger.warning(f"Fehler beim Durchsuchen von {directory}: {e}")

    return files, subdirectories


def _get_directory_contents(directory: Path) -> tuple[List[Path], List[Path]]:
    with measure("daemon.directory_scan") as measurement:
        files, subdirectories = _get_directory_contents_impl(directory)
        measurement["files_found"] = len(files)
        measurement["directories_found"] = len(subdirectories)
        return files, subdirectories


def _get_subdirectories_in_directory_impl(directory: Path) -> List[Path]:
    """Besorgt alle direkten Unterverzeichnisse eines Verzeichnisses.

    Args:
            directory: Verzeichnispfad.

    Returns:
            Liste von Unterverzeichnissen.
    """
    subdirs: List[Path] = []

    try:
        if not directory.exists():
            logger.debug(f"Verzeichnis existiert nicht: {directory}")
            return subdirs

        for item in directory.iterdir():
            if item.is_dir():
                subdirs.append(item)

    except Exception as e:
        logger.warning(f"Fehler beim Ermitteln von Unterverzeichnissen in {directory}: {e}")

    return subdirs


def _get_subdirectories_in_directory(directory: Path) -> List[Path]:
    with measure("daemon.subdirectory_scan") as measurement:
        subdirectories = _get_subdirectories_in_directory_impl(directory)
        measurement["directories_found"] = len(subdirectories)
        return subdirectories


def _is_file_ready(path: Path, min_stable_time: float = 1.0) -> bool:
    """Prüft, ob eine Datei bereit zur Verarbeitung ist.

    Dies verhindert, dass teilweise geschriebene Dateien verarbeitet werden.

    Args:
            path: Dateipfad.
            min_stable_time: Minimale Zeit ohne Änderungen (Sekunden).

    Returns:
            True, falls die Datei stabil ist.
    """
    try:
        current_mtime = path.stat().st_mtime
        key = str(path)

        with _lock:
            if key not in _watched_files:
                _watched_files[key] = current_mtime
                return False

            last_mtime = _watched_files[key]

            if current_mtime != last_mtime:
                _watched_files[key] = current_mtime
                return False

            # Prüfe stabilitätszeit
            time_diff = time.time() - current_mtime
            return time_diff >= min_stable_time

    except Exception as e:
        logger.warning(f"Fehler beim Prüfen der Datei-Stabilität: {e}")
        return False


def _process_file(
    file_path: Path, action: str = "move", reprocess_processed: bool = False
) -> bool:
    """Verarbeitet eine einzelne Datei.

    Args:
            file_path: Dateipfad.

    Returns:
            True bei Erfolg, False bei Fehler.
    """
    try:
        logger.debug(f"Verarbeite Datei: {file_path} (action={action})")
        route_result = route_file(
            file_path, action=action, reprocess_processed=reprocess_processed
        )
        # No info-level log for successful processing to avoid spam
        return route_result if reprocess_processed else True

    except Exception as e:
        logger.error(f"Fehler bei der Verarbeitung von {file_path}: {e}")
        return False


def _cleanup_watched_files() -> None:
    """Bereinigt die watched_files um nicht mehr existierende Dateien."""
    try:
        with _lock:
            # Entferne Einträge für nicht mehr existierende Dateien
            to_remove = []
            for filename in _watched_files.keys():
                # Wir können hier nicht den vollständigen Pfad prüfen,
                # daher nutzen wir einen Fallback-Timeout von 1 Stunde
                if time.time() - _watched_files[filename] > 3600:
                    to_remove.append(filename)

            for filename in to_remove:
                del _watched_files[filename]
                logger.debug(f"Entferne aus Cache: {filename}")

    except Exception as e:
        logger.warning(f"Fehler beim Bereinigen des Caches: {e}")


def _remove_empty_subdirectories(roots: List[Path]) -> None:
    """Entfernt leere Unterverzeichnisse, lässt konfigurierte Wurzeln bestehen."""
    for root in roots:
        try:
            subdirectories = sorted(
                (path for path in root.rglob("*") if path.is_dir()),
                key=lambda path: len(path.parts),
                reverse=True,
            )
            for subdirectory in subdirectories:
                try:
                    subdirectory.rmdir()
                except OSError:
                    continue
        except OSError as exc:
            logger.warning(f"Leere Unterordner konnten nicht bereinigt werden: {exc}")


def _scan_input_directories(input_dirs: List[Dict[str, object]]) -> Dict[str, int]:
    totals = {
        "directories_scanned": 0,
        "files_seen": 0,
        "files_ready": 0,
        "files_completed": 0,
        "files_failed": 0,
    }

    for input_entry in input_dirs:
        try:
            input_dir = input_entry["path"]
            action = input_entry.get("action", "move")
            queue: List[Path] = [input_dir]

            while queue and _running:
                current_dir = queue.pop(0)
                totals["directories_scanned"] += 1
                files, subdirectories = _get_directory_contents(current_dir)
                totals["files_seen"] += len(files)

                for file_path in files:
                    if not _running:
                        break
                    if _is_file_ready(file_path):
                        totals["files_ready"] += 1
                        if _process_file(file_path, action=action):
                            totals["files_completed"] += 1
                        else:
                            totals["files_failed"] += 1

                queue.extend(subdirectories)

        except Exception as e:
            logger.error(f"Fehler beim Durchsuchen von {input_dir}: {e}")

    _remove_empty_subdirectories([Path(entry["path"]) for entry in input_dirs])
    return totals


def _reprocess_existing_files(
    reprocess_files: Set[Path], storage_roots: List[Path]
) -> Dict[str, int]:
    """Verarbeitet gespeicherte Dateien erneut und meldet anonymisierten Fortschritt."""
    file_paths = tuple(reprocess_files)
    progress = {
        "files_total": len(file_paths),
        "files_attempted": 0,
        "files_completed": 0,
        "files_failed": 0,
        "files_missing": 0,
        "files_in_flight": 0,
    }
    progress_lock = threading.Lock()
    stop_heartbeat = threading.Event()

    def _report_progress(status: str) -> None:
        with progress_lock:
            fields = {**progress, "files_remaining": len(reprocess_files)}
        record_event("daemon.reprocess_progress", status=status, **fields)
        logger.info(
            "Reprocessing-Fortschritt: "
            f"status={status}, gesamt={fields['files_total']}, "
            f"versucht={fields['files_attempted']}, "
            f"abgeschlossen={fields['files_completed']}, "
            f"fehlgeschlagen={fields['files_failed']}, "
            f"fehlend={fields['files_missing']}, "
            f"in_arbeit={fields['files_in_flight']}, "
            f"offen={fields['files_remaining']}"
        )

    def _heartbeat() -> None:
        while not stop_heartbeat.wait(60):
            _report_progress("running")

    heartbeat = threading.Thread(
        target=_heartbeat,
        name="filemind-reprocess-progress",
        daemon=True,
    )
    heartbeat.start()
    _report_progress("started")

    try:
        for file_path in file_paths:
            with progress_lock:
                progress["files_attempted"] += 1
                progress["files_in_flight"] = 1

            if not file_path.exists():
                with progress_lock:
                    progress["files_missing"] += 1
                    progress["files_in_flight"] = 0
                    reprocess_files.discard(file_path)
            else:
                succeeded = _process_file(
                    file_path, action="move", reprocess_processed=True
                )
                with progress_lock:
                    progress["files_in_flight"] = 0
                    if succeeded:
                        progress["files_completed"] += 1
                        reprocess_files.discard(file_path)
                    else:
                        progress["files_failed"] += 1

            if progress["files_attempted"] % 1000 == 0:
                _report_progress("running")
    finally:
        stop_heartbeat.set()
        heartbeat.join()
        _remove_empty_subdirectories(storage_roots)

    with progress_lock:
        progress["files_remaining"] = len(reprocess_files)
    _report_progress("completed" if not reprocess_files else "incomplete")
    return progress


def run_daemon(foreground: bool = True, reprocess_processed: bool = False) -> None:
    """Startet den Filemind-Daemon.

    Args:
            foreground: Falls True, läuft der Daemon im Vordergrund.
                    Falls False, wird als Hintergrund-Prozess gestartet.

    Examples:
            >>> run_daemon(foreground=True)
    """
    global _running

    _running = True

    # Signal-Handler registrieren
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    logger.info("=" * 60)
    logger.info("filemind Daemon gestartet")
    logger.info("filemind Version: %s", __version__)
    logger.info("=" * 60)

    # Besorge Konfiguration
    input_dirs = _get_input_directories()
    poll_interval = _get_poll_interval()
    reprocess_enabled = reprocess_processed or bool(_load_config().get("reprocess_processed", False))
    reprocess_files: Set[Path] | None = None
    reprocess_roots: List[Path] = []
    reprocess_summary = {
        "files_total": 0,
        "files_attempted": 0,
        "files_completed": 0,
        "files_failed": 0,
        "files_missing": 0,
    }
    if reprocess_enabled:
        storage_cfg = get_section("storage", {})
        reprocess_roots = [
            Path(storage_cfg[key])
            for key in ("base_media_path", "base_documents_path")
            if storage_cfg.get(key)
        ]
        reprocess_files = {
            file_path
            for root in reprocess_roots
            for file_path in _get_files_in_directory(root, recursive=True)
        }
        logger.info(
            f"Erneute Verarbeitung angefordert: {len(reprocess_files)} gespeicherte Dateien"
        )
        reprocess_summary["files_total"] = len(reprocess_files)

    if not input_dirs:
        logger.error("Keine Input-Ordner konfiguriert, beende.")
        sys.exit(1)

    logger.info(f"Überwache {len(input_dirs)} Input-Ordner mit {poll_interval}s Intervall")

    # Register existing files in target storage to allow duplicate detection
    try:
        from filemind.storage.hash_store import get_registered_sizes, register_file

        storage_cfg = get_section("storage", {})
        base_media = Path(storage_cfg.get("base_media_path", ""))
        base_docs = Path(storage_cfg.get("base_documents_path", ""))

        checked_count = 0
        hashed_count = 0
        registered_sizes = get_registered_sizes()

        def _register_all_files_under(root: Path) -> None:
            nonlocal checked_count, hashed_count
            if not root or not root.exists():
                return
            batch: List[Path] = []

            def _register_batch(paths: List[Path]) -> None:
                nonlocal checked_count, hashed_count
                for path in paths:
                    checked_count += 1
                    try:
                        if registered_sizes.get(str(path)) != path.stat().st_size:
                            register_file(path)
                            hashed_count += 1
                    except Exception:
                        continue
                    finally:
                        if checked_count % 1000 == 0:
                            logger.info(
                                f"Duplikatprüfung läuft: {checked_count} Ziel-Dateien "
                                f"geprüft ({hashed_count} neu gehasht)"
                            )

            for dirpath, _, filenames in os.walk(root):
                for filename in filenames:
                    batch.append(Path(dirpath) / filename)
                    if len(batch) >= 500:
                        _register_batch(batch)
                        batch = []
            if batch:
                _register_batch(batch)

        with measure("daemon.startup_hash_index") as measurement:
            _register_all_files_under(base_media)
            _register_all_files_under(base_docs)
            measurement["files_checked"] = checked_count
            measurement["files_hashed"] = hashed_count
        logger.info(
            f"Vorhandene Ziel-Dateien registriert für Duplikatprüfung "
            f"({checked_count} geprüft, {hashed_count} neu gehasht)"
        )
    except Exception as e:
        logger.warning(f"Konnte vorhandene Ziel-Dateien nicht registrieren: {e}")

    processed_count = 0
    error_count = 0

    try:
        while _running:
            try:
                with measure("daemon.poll_cycle") as measurement:
                    if reprocess_files:
                        with measure("daemon.reprocess_existing_files") as reprocess_measurement:
                            reprocess_measurement["files_pending"] = len(reprocess_files)
                            pass_summary = _reprocess_existing_files(
                                reprocess_files, reprocess_roots
                            )
                            for key in (
                                "files_attempted",
                                "files_completed",
                                "files_failed",
                                "files_missing",
                            ):
                                reprocess_summary[key] += pass_summary[key]
                            reprocess_measurement.update(pass_summary)
                    cycle_totals = _scan_input_directories(input_dirs)
                    processed_count += cycle_totals["files_completed"]
                    error_count += cycle_totals["files_failed"]
                    measurement.update(cycle_totals)

                # Periodic Cleanup
                if processed_count % 100 == 0 and processed_count > 0:
                    _cleanup_watched_files()

                # Warte vor nächstem Poll
                time.sleep(poll_interval)

            except KeyboardInterrupt:
                logger.info("Keyboard Interrupt empfangen")
                _running = False
                break

            except Exception as e:
                logger.error(f"Fehler in Daemon-Loop: {e}", exc_info=True)
                # Weiterfahren, nicht abbrechen
                time.sleep(poll_interval)

    except Exception as e:
        logger.critical(f"Kritischer Fehler im Daemon: {e}", exc_info=True)
        sys.exit(1)

    finally:
        record_event(
            "daemon.run_summary",
            input_files_completed=processed_count,
            input_files_failed=error_count,
            reprocess_files_total=reprocess_summary["files_total"],
            reprocess_files_attempted=reprocess_summary["files_attempted"],
            reprocess_files_completed=reprocess_summary["files_completed"],
            reprocess_files_failed=reprocess_summary["files_failed"],
            reprocess_files_missing=reprocess_summary["files_missing"],
            reprocess_files_remaining=len(reprocess_files or ()),
        )
        logger.info("=" * 60)
        logger.info(
            f"filemind Daemon beendet (verarbeitet: {processed_count}, "
            f"Fehler: {error_count}, Reprocessing offen: {len(reprocess_files or ())})"
        )
        logger.info("=" * 60)


def main() -> None:
    """Einstiegspunkt für Kommandozeilen-Aufruf."""
    import argparse

    parser = argparse.ArgumentParser(
        description="filemind - Intelligente Datei-Verwaltung mit KI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Beispiele:
  python -m filemind.main                # Starte Daemon
  python -m filemind.main --help         # Zeige Hilfe
		""",
    )

    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log-Level (default: INFO)",
    )

    parser.add_argument(
        "--config",
        type=str,
        default=str(CONFIG_FILE),
        help=f"Pfad zur Konfigurationsdatei (default: {CONFIG_FILE})",
    )

    parser.add_argument(
        "--input-dir",
        type=str,
        help="Überschreibe Input-Ordner aus Konfiguration",
    )

    parser.add_argument(
        "--reinit-hash-store",
        action="store_true",
        help="Lösche und initialisiere die Hash-Store DB neu bevor der Daemon startet",
    )
    parser.add_argument(
        "--reprocess-processed",
        action="store_true",
        help="Verarbeite vorhandene Input-Dateien einmalig erneut, ohne Hash-Deduplizierung",
    )

    args = parser.parse_args()

    # Setze Konfigurationsdatei und Logging
    set_config_file(args.config)
    from filemind.logging_utils.logger import get_logger, set_log_level
    from filemind.storage.hash_store import reset_hash_store

    get_logger(__name__)

    try:
        set_log_level(args.log_level)
    except ValueError as e:
        logger.warning(f"Warnung: {e}")

    # If requested via CLI or config, reinitialize the hash-store DB
    try:
        storage_cfg = get_section("storage", {})
        cfg_reinit = bool(storage_cfg.get("reinit_hash_store", False))
        if args.reinit_hash_store or cfg_reinit:
            logger.info("Initialisiere Hash-Store DB neu (requested)")
            reset_hash_store()
    except Exception as e:
        logger.warning(f"Fehler beim Reinitialisieren des Hash-Stores: {e}")
    logger.info(f"filemind Daemon startet mit Log-Level: {args.log_level}")

    try:
        initialize_performance()
    except Exception as e:
        logger.warning(f"Performance-Messung konnte nicht gestartet werden: {type(e).__name__}")

    # Starte Daemon
    try:
        run_daemon(foreground=True, reprocess_processed=args.reprocess_processed)
    except Exception as e:
        logger.critical(f"Daemon-Fehler: {e}", exc_info=True)
        sys.exit(1)
    finally:
        finish_performance()


if __name__ == "__main__":
    main()
