"""Logging-System für filemind.

Dieses Modul konfiguriert und verwaltet das Logging für das filemind-Projekt.
Es nutzt TimedRotatingFileHandler für Log-Rotation und unterstützt
Konfiguration über config.yaml.

Funktionalität:
- get_logger(name: str) -> Logger: Besorgt einen konfigurierten Logger.
- TimedRotatingFileHandler mit konfigurierbarer Retention.
- Standardformat: timestamp, level, module, message.
- Thread-sichere Logging-Verwaltung.
"""

from __future__ import annotations

import logging
import os
import threading
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Optional, Dict, Any

from filemind.config import get_section

# Konstanten
DEFAULT_LOG_DIR = Path.cwd() / ".filemind" / "logs"
DEFAULT_LOG_LEVEL = logging.INFO
DEFAULT_RETENTION_DAYS = 7

# Globale Lock für Thread-Safety
_logging_lock = threading.Lock()

# Log-Format
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Cache für Logger-Instanzen
_loggers: Dict[str, logging.Logger] = {}


def _load_logging_config() -> Dict[str, Any]:
	"""Lädt Logging-Konfiguration aus der zentralen filemind-Konfiguration."""
	try:
		return get_section("logging", {})

	except Exception as e:
		print(f"Warnung: Fehler beim Laden von Logging-Konfiguration: {e}")
		return {}


def _setup_root_logger() -> None:
	"""Konfiguriert den Root-Logger einmalig.

	Diese Funktion wird beim ersten Aufruf ausgeführt und richtet
	die Standard-Logging-Handler und -Format ein.
	"""
	root_logger = logging.getLogger()

	# Falls bereits konfiguriert, nichts tun
	if root_logger.handlers:
		return

	# Lade Konfiguration
	logging_config = _load_logging_config()

	# Extrahiere Werte mit Defaults
	log_dir = Path(logging_config.get("log_dir", DEFAULT_LOG_DIR))
	log_level_str = logging_config.get("level", "INFO").upper()
	retention_days = logging_config.get("retention_days", DEFAULT_RETENTION_DAYS)
	console_enabled = logging_config.get("console_enabled", True)
	console_level_str = logging_config.get("console_level", "WARNING").upper()

	# Konvertiere Log-Level Strings zu Integers
	try:
		log_level = getattr(logging, log_level_str, DEFAULT_LOG_LEVEL)
	except (ValueError, AttributeError):
		log_level = DEFAULT_LOG_LEVEL

	try:
		console_level = getattr(logging, console_level_str, logging.WARNING)
	except (ValueError, AttributeError):
		console_level = logging.WARNING

	# Erstelle Log-Verzeichnis
	log_dir.mkdir(parents=True, exist_ok=True)

	# Konfiguriere Root-Logger
	root_logger.setLevel(log_level)

	# Formatter
	formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)

	# TimedRotatingFileHandler
	log_file = log_dir / "filemind.log"
	file_handler = TimedRotatingFileHandler(
		str(log_file),
		when="midnight",  # Rotiere täglich um Mitternacht
		interval=1,
		backupCount=retention_days,
		encoding="utf-8",
	)
	file_handler.setLevel(log_level)
	file_handler.setFormatter(formatter)
	root_logger.addHandler(file_handler)

	# Console Handler (optional)
	if console_enabled:
		console_handler = logging.StreamHandler()
		console_handler.setLevel(console_level)
		console_handler.setFormatter(formatter)
		root_logger.addHandler(console_handler)

	# Log Initialisierung
	root_logger.debug(
		f"Logging initialisiert: log_dir={log_dir}, "
		f"level={log_level_str}, retention={retention_days} Tage"
	)
	root_logger.info("filemind Logging System gestartet")


def get_logger(name: str) -> logging.Logger:
	"""Besorgt einen konfigurierten Logger für ein Modul.

	Diese Funktion nutzt Caching und ist thread-sicher. Logger werden
	nach Modulnamen gecacht, um Performance zu optimieren.

	Args:
		name: Name des Loggers (üblicherweise __name__ des Moduls).

	Returns:
		Ein konfigurierter logging.Logger.

	Examples:
		>>> from filemind.logging.logger import get_logger
		>>> logger = get_logger(__name__)
		>>> logger.info("Wichtige Information")
		>>> logger.error("Fehlermeldung")
	"""
	with _logging_lock:
		# Setup Root-Logger beim ersten Aufruf
		if not logging.getLogger().handlers:
			_setup_root_logger()

		# Prüfe Cache
		if name in _loggers:
			return _loggers[name]

		# Erstelle neuen Logger
		logger = logging.getLogger(name)
		_loggers[name] = logger

		return logger


def set_log_level(level: str) -> None:
	"""Setzt den globalen Log-Level zur Laufzeit.

	Args:
		level: Log-Level als String (z. B. "DEBUG", "INFO", "WARNING", "ERROR").

	Raises:
		ValueError: Falls Log-Level ungültig ist.

	Examples:
		>>> set_log_level("DEBUG")
	"""
	valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}

	level_upper = level.upper()
	if level_upper not in valid_levels:
		raise ValueError(
			f"Ungültiger Log-Level: {level}. Erlaubt: {valid_levels}"
		)

	with _logging_lock:
		if not logging.getLogger().handlers:
			get_logger(__name__)

		root_logger = logging.getLogger()
		log_level = getattr(logging, level_upper)
		root_logger.setLevel(log_level)

		# Setze Level für alle Handler
		for handler in root_logger.handlers:
			handler.setLevel(log_level)

		logger = get_logger(__name__)
		logger.info(f"Log-Level geändert zu: {level_upper}")


def get_log_file_path() -> Path:
	"""Besorgt den Pfad zur aktuellen Log-Datei.

	Returns:
		Pfad zur Log-Datei.
	"""
	logging_config = _load_logging_config()
	log_dir = Path(logging_config.get("log_dir", DEFAULT_LOG_DIR))
	return log_dir / "filemind.log"


def clear_logs(older_than_days: int = 0) -> int:
	"""Löscht alte Log-Dateien.

	Args:
		older_than_days: Nur Logs älter als diese Anzahl Tage löschen.

	Returns:
		Anzahl gelöschter Log-Dateien.

	Examples:
		>>> deleted = clear_logs(older_than_days=30)
		>>> print(f"Gelöschte Log-Dateien: {deleted}")
	"""
	import time

	log_file_path = get_log_file_path()
	log_dir = log_file_path.parent

	if not log_dir.exists():
		return 0

	logger = get_logger(__name__)
	deleted_count = 0
	current_time = time.time()

	try:
		for log_file in log_dir.glob("filemind.log*"):
			file_age_days = (current_time - log_file.stat().st_mtime) / 86400
			if file_age_days > older_than_days:
				log_file.unlink()
				deleted_count += 1
				logger.debug(f"Gelöschte alte Log-Datei: {log_file.name}")

		if deleted_count > 0:
			logger.info(f"{deleted_count} alte Log-Dateien gelöscht")

	except Exception as e:
		logger.warning(f"Fehler beim Löschen von Log-Dateien: {e}")

	return deleted_count