"""Verwaltung der Zielordner-Struktur für filemind.

Dieses Modul verwaltet die Zielordner-Struktur für sortierte Dateien.
Basisordner: Pictures, Videos, Audio, Archives, Other.
Unterordner-Format: YYYY_NN (z. B. 2026_01, 2026_02, ...).
Maximal 3000 Dateien pro Unterordner.

Funktionalität:
- ensure_directory(path: Path): Erstellt Verzeichnis falls nötig.
- get_target_subfolder(base_folder: Path, year: int) -> Path:
  Findet oder erstellt passenden YYYY_NN Ordner.
- Robuste Fehlerbehandlung und Logging.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from filemind.logging_utils.logger import get_logger

logger = get_logger(__name__)

# Konstanten
MAX_FILES_PER_SUBFOLDER: Final[int] = 3000
BASE_FOLDERS: Final[set[str]] = {"Pictures", "Videos", "Audio", "Archives", "Other"}


def ensure_directory(path: Path) -> None:
	"""Erstellt ein Verzeichnis, falls es nicht existiert.

	Args:
		path: Pfad zum Verzeichnis, das erstellt werden soll.

	Raises:
		RuntimeError: Falls das Verzeichnis nicht erstellt werden kann.

	Examples:
		>>> from pathlib import Path
		>>> ensure_directory(Path("storage/Pictures/2026_01"))
		>>> # Verzeichnis existiert nun
	"""
	try:
		path.mkdir(parents=True, exist_ok=True)
		logger.debug(f"Verzeichnis sichergestellt: {path}")
	except Exception as e:
		error_msg = f"Fehler beim Erstellen des Verzeichnisses {path}: {e}"
		logger.error(error_msg)
		raise RuntimeError(error_msg) from e


def get_target_subfolder(base_folder: Path, year: int) -> Path:
	"""Findet oder erstellt einen Zielordner für ein Jahr.

	Diese Funktion findet einen existierenden YYYY_NN Ordner innerhalb
	von base_folder, der weniger als MAX_FILES_PER_SUBFOLDER Dateien enthält.
	Falls alle existierenden Ordner voll sind, wird ein neuer erstellt.

	Args:
		base_folder: Basis-Verzeichnis (z. B. Pictures, Videos).
		year: Jahreszahl (z. B. 2026).

	Returns:
		Pfad zum Zielordner (z. B. Pictures/2026_01).

	Raises:
		ValueError: Falls base_folder ungültig ist.
		RuntimeError: Falls der Ordner nicht erstellt/geöffnet werden kann.

	Examples:
		>>> from pathlib import Path
		>>> target = get_target_subfolder(Path("storage/Pictures"), 2026)
		>>> print(target)
		storage/Pictures/2026_01

		>>> # Falls 2026_01 voll ist, wird 2026_02 genutzt
		>>> target = get_target_subfolder(Path("storage/Pictures"), 2026)
		>>> print(target)
		storage/Pictures/2026_02
	"""
	logger.debug(f"Suche Zielordner: base_folder={base_folder}, year={year}")

	# Validierung
	if not base_folder.name in BASE_FOLDERS:
		logger.warning(
			f"base_folder '{base_folder.name}' entspricht nicht Standard-Namen. "
			f"Erlaubt: {BASE_FOLDERS}"
		)

	# Stelle sicher, dass base_folder existiert
	ensure_directory(base_folder)

	# Finde passenden YYYY_NN Ordner
	try:
		subfolder_number = _get_next_subfolder_number(base_folder, year)
		subfolder_name = f"{year:04d}_{subfolder_number:02d}"
		target_path = base_folder / subfolder_name

		# Stelle sicher, dass der Zielordner existiert
		ensure_directory(target_path)

		logger.info(
			f"Zielordner gefunden/erstellt: {target_path} "
			f"({_count_files_in_directory(target_path)} Dateien)"
		)

		return target_path

	except Exception as e:
		error_msg = (
			f"Fehler beim Finden des Zielordners für {year} in {base_folder}: {e}"
		)
		logger.error(error_msg)
		raise RuntimeError(error_msg) from e


def _count_files_in_directory(path: Path) -> int:
	"""Zählt die Anzahl der Dateien in einem Verzeichnis (nicht rekursiv).

	Args:
		path: Verzeichnispfad.

	Returns:
		Anzahl der Dateien (nicht Verzeichnisse).
	"""
	if not path.exists():
		return 0

	try:
		file_count = sum(1 for item in path.iterdir() if item.is_file())
		logger.debug(f"Zähle Dateien in {path}: {file_count}")
		return file_count
	except Exception as e:
		logger.warning(f"Fehler beim Zählen der Dateien in {path}: {e}")
		return 0


def _get_next_subfolder_number(base_folder: Path, year: int) -> int:
	"""Findet die nächste verfügbare Unterfolder-Nummer für ein Jahr.

	Diese Funktion iteriert durch bestehende YYYY_NN Ordner und findet
	den ersten, der weniger als MAX_FILES_PER_SUBFOLDER Dateien enthält.
	Falls alle voll sind, wird eine neue Nummer zurückgegeben.

	Args:
		base_folder: Basis-Verzeichnis (z. B. Pictures, Videos).
		year: Jahreszahl (z. B. 2026).

	Returns:
		Die Unterfolder-Nummer (NN), z. B. 1 für 2026_01.
	"""
	logger.debug(f"Finde nächste Subfolder-Nummer: year={year}, base={base_folder}")

	# Stelle sicher, dass base_folder existiert
	if not base_folder.exists():
		logger.debug(f"base_folder {base_folder} existiert nicht, starte mit _01")
		return 1

	# Suche nach existierenden YYYY_NN Ordnern
	year_prefix = f"{year:04d}_"
	existing_subfolders = []

	try:
		for item in base_folder.iterdir():
			if item.is_dir() and item.name.startswith(year_prefix):
				# Extrahiere die Nummer
				try:
					number_str = item.name.split("_")[1]
					number = int(number_str)
					existing_subfolders.append((number, item))
				except (ValueError, IndexError):
					logger.warning(f"Ungültiges Ordnerformat: {item.name}")
					continue
	except Exception as e:
		logger.warning(f"Fehler beim Durchsuchen von {base_folder}: {e}")

	# Sortiere nach Nummer
	existing_subfolders.sort(key=lambda x: x[0])

	logger.debug(f"Gefundene Unterordner für {year}: {[x[0] for x in existing_subfolders]}")

	# Finde den ersten nicht-vollen Ordner
	for number, subfolder_path in existing_subfolders:
		file_count = _count_files_in_directory(subfolder_path)
		if file_count < MAX_FILES_PER_SUBFOLDER:
			logger.debug(
				f"Gefundener nicht-voller Ordner: {subfolder_path.name} "
				f"({file_count}/{MAX_FILES_PER_SUBFOLDER} Dateien)"
			)
			return number

	# Falls alle voll oder keine existieren, nutze nächste Nummer
	next_number = (
		existing_subfolders[-1][0] + 1 if existing_subfolders else 1
	)
	logger.debug(f"Erstelle neuen Subfolder: {year:04d}_{next_number:02d}")

	return next_number


def get_base_folders(root: Path) -> dict[str, Path]:
	"""Erstellt oder findet alle Standard-Basis-Ordner.

	Args:
		root: Wurzelpfad für Basis-Ordner.

	Returns:
		Wörterbuch mit Basis-Ordnernamen als Schlüssel und Pfaden als Werte.

	Examples:
		>>> from pathlib import Path
		>>> folders = get_base_folders(Path("storage"))
		>>> print(folders["Pictures"])
		storage/Pictures
	"""
	logger.debug(f"Initialisiere Basis-Ordner in: {root}")

	base_folders_dict = {}
	for folder_name in BASE_FOLDERS:
		folder_path = root / folder_name
		ensure_directory(folder_path)
		base_folders_dict[folder_name] = folder_path
		logger.info(f"Basis-Ordner vorbereitet: {folder_path}")

	return base_folders_dict


# Hilfsfunktion für Debugging und Verwaltung
def get_folder_stats(base_folder: Path) -> dict:
	"""Sammelt Statistiken über einen Basis-Ordner und seine Unterordner.

	Args:
		base_folder: Basis-Verzeichnis (z. B. Pictures, Videos).

	Returns:
		Wörterbuch mit Statistiken über Unterordner und Dateien.

	Examples:
		>>> from pathlib import Path
		>>> stats = get_folder_stats(Path("storage/Pictures"))
		>>> print(stats)
		{'2026_01': 1500, '2026_02': 2999, ...}
	"""
	stats = {}

	if not base_folder.exists():
		logger.debug(f"Ordner existiert nicht: {base_folder}")
		return stats

	try:
		for subfolder in sorted(base_folder.iterdir()):
			if subfolder.is_dir() and "_" in subfolder.name:
				file_count = _count_files_in_directory(subfolder)
				stats[subfolder.name] = file_count
				logger.debug(f"{subfolder.name}: {file_count} Dateien")
	except Exception as e:
		logger.warning(f"Fehler beim Sammeln von Statistiken für {base_folder}: {e}")

	return stats
