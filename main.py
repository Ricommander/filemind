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
import time
import threading
from pathlib import Path
from typing import List, Optional, Set, Dict, Any
from datetime import datetime

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


def _get_input_directories() -> List[Path]:
	"""Besorgt die Input-Ordner aus Konfiguration oder Defaults.

	Returns:
		Liste von Input-Verzeichnissen.
	"""
	daemon_config = _load_config()
	input_dirs_raw = daemon_config.get("input_directories", None)

	if input_dirs_raw is None:
		# Fallback zu Defaults
		input_dirs = DEFAULT_INPUT_DIRS
	elif isinstance(input_dirs_raw, str):
		# Einzelner Ordner als String
		input_dirs = [Path(input_dirs_raw)]
	elif isinstance(input_dirs_raw, list):
		# Liste von Ordnern
		input_dirs = [Path(d) for d in input_dirs_raw]
	else:
		input_dirs = DEFAULT_INPUT_DIRS

	# Erstelle Ordner falls nötig
	valid_dirs = []
	for input_dir in input_dirs:
		try:
			input_dir.mkdir(parents=True, exist_ok=True)
			valid_dirs.append(input_dir)
			logger.info(f"Input-Ordner konfiguriert: {input_dir}")
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
			f"Ungültiges Polling-Intervall: {interval}, "
			f"nutze Default: {DEFAULT_POLL_INTERVAL}s"
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

		with _lock:
			if path.name not in _watched_files:
				_watched_files[path.name] = current_mtime
				return False

			last_mtime = _watched_files[path.name]

			if current_mtime != last_mtime:
				_watched_files[path.name] = current_mtime
				return False

			# Prüfe stabilitätszeit
			time_diff = time.time() - current_mtime
			return time_diff >= min_stable_time

	except Exception as e:
		logger.warning(f"Fehler beim Prüfen der Datei-Stabilität: {e}")
		return False


def _process_file(file_path: Path) -> bool:
	"""Verarbeitet eine einzelne Datei.

	Args:
		file_path: Dateipfad.

	Returns:
		True bei Erfolg, False bei Fehler.
	"""
	try:
		logger.debug(f"Verarbeite Datei: {file_path}")
		route_file(file_path)
		logger.info(f"Datei erfolgreich verarbeitet: {file_path.name}")
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

	processed_count = 0
	error_count = 0

	try:
		while _running:
			try:
				# Durchsuche alle Input-Ordner
				for input_dir in input_dirs:
					try:
						files = _get_files_in_directory(input_dir, recursive=False)

						for file_path in files:
							if not _running:
								break

							# Prüfe ob Datei stabil ist
							if _is_file_ready(file_path):
								if _process_file(file_path):
									processed_count += 1
								else:
									error_count += 1

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
		logger.info(f"filemind Daemon beendet (verarbeitet: {processed_count}, Fehler: {error_count})")
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

	args = parser.parse_args()

	# Setze Konfigurationsdatei und Logging
	set_config_file(args.config)
	from filemind.logging_utils.logger import get_logger, set_log_level

	get_logger(__name__)

	try:
		set_log_level(args.log_level)
	except ValueError as e:
		logger.warning(f"Warnung: {e}")

	logger.info(f"filemind Daemon startet mit Log-Level: {args.log_level}")

	# Starte Daemon
	try:
		run_daemon(foreground=True)
	except Exception as e:
		logger.critical(f"Daemon-Fehler: {e}", exc_info=True)
		sys.exit(1)


if __name__ == "__main__":
	main()
