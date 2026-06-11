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
from typing import Any, Dict, List

from filemind.config import get_config_file, get_section, set_config_file
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


def _get_files_in_directory(directory: Path, recursive: bool = False) -> List[Path]:
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


def _get_subdirectories_in_directory(directory: Path) -> List[Path]:
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


def _process_file(file_path: Path, action: str = "move") -> bool:
    """Verarbeitet eine einzelne Datei.

    Args:
            file_path: Dateipfad.

    Returns:
            True bei Erfolg, False bei Fehler.
    """
    try:
        logger.debug(f"Verarbeite Datei: {file_path} (action={action})")
        route_file(file_path, action=action)
        # No info-level log for successful processing to avoid spam
        return True

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


def run_daemon(foreground: bool = True) -> None:
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
    logger.info("=" * 60)

    # Besorge Konfiguration
    input_dirs = _get_input_directories()
    poll_interval = _get_poll_interval()

    if not input_dirs:
        logger.error("Keine Input-Ordner konfiguriert, beende.")
        sys.exit(1)

    logger.info(f"Überwache {len(input_dirs)} Input-Ordner mit {poll_interval}s Intervall")

    # Register existing files in target storage to allow duplicate detection
    try:
        from filemind.storage.hash_store import register_file

        storage_cfg = get_section("storage", {})
        base_media = Path(storage_cfg.get("base_media_path", ""))
        base_docs = Path(storage_cfg.get("base_documents_path", ""))

        def _register_all_files_under(root: Path) -> None:
            if not root or not root.exists():
                return
            for dirpath, _, filenames in os.walk(root):
                for fname in filenames:
                    try:
                        p = Path(dirpath) / fname
                        register_file(p)
                    except Exception:
                        continue

        _register_all_files_under(base_media)
        _register_all_files_under(base_docs)
        logger.info("Vorhandene Ziel-Dateien registriert für Duplikatprüfung")
    except Exception as e:
        logger.warning(f"Konnte vorhandene Ziel-Dateien nicht registrieren: {e}")

    processed_count = 0
    error_count = 0

    try:
        while _running:
            try:
                # Durchsuche alle Input-Ordner
                for input_entry in input_dirs:
                    try:
                        input_dir = input_entry["path"]
                        action = input_entry.get("action", "move")
                        queue: List[Path] = [input_dir]

                        while queue and _running:
                            current_dir = queue.pop(0)

                            files = _get_files_in_directory(current_dir, recursive=False)

                            for file_path in files:
                                if not _running:
                                    break

                                # Prüfe ob Datei stabil ist
                                if _is_file_ready(file_path):
                                    if _process_file(file_path, action=action):
                                        processed_count += 1
                                    else:
                                        error_count += 1

                            subdirs = _get_subdirectories_in_directory(current_dir)
                            queue.extend(subdirs)

                    except Exception as e:
                        logger.error(f"Fehler beim Durchsuchen von {input_dir}: {e}")

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
        logger.info("=" * 60)
        logger.info(
            f"filemind Daemon beendet (verarbeitet: {processed_count}, Fehler: {error_count})"
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

    # Starte Daemon
    try:
        run_daemon(foreground=True)
    except Exception as e:
        logger.critical(f"Daemon-Fehler: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
