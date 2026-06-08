"""Routing-Logik für filemind.

Basierend auf der Klassifikation entscheidet dieses Modul, wie Dateien
weiterverarbeitet werden. Alle Seiteneffekte (Dateioperationen,
Uploads) erfolgen innerhalb der Handler-Funktionen.

Die Implementierung ruft Integrations-Module als Stubs auf; falls
diese Funktionen nicht vorhanden sind, wird dies sauber geloggt.
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import Optional, Dict, Any
from datetime import datetime
import logging
import shutil
import os

from filemind.classification.classifier import classify_file
from filemind.config import get_config, get_section
from filemind.core.models import FileType, ClassificationResult
from filemind.logging_utils.logger import get_logger

logger = get_logger(__name__)


def _get_output_dir_for_file_type(file_type: FileType) -> str:
	"""Besorgt den Output-Ordner-Namen für einen Dateityp."""
	config = get_config()
	output_dirs = config.get("output_directories", {})

	return output_dirs.get(file_type.value, "Other")


def _get_storage_base_path() -> Path:
	"""Besorgt den Basis-Speicherpfad."""
	storage_config = get_section("storage", {})

	base_path = storage_config.get("base_path", "output")
	return Path(base_path)


def _handle_paperless_upload(file_info: "Any", file_type: FileType) -> bool:
	"""Versucht, ein Dokument zu Paperless NGX hochzuladen.

	Returns: True bei Erfolg, False bei Fehler oder wenn deaktiviert.
	"""
	paperless_config = get_section("paperless", {})

	if not paperless_config.get("enabled", False):
		logger.debug("Paperless ist deaktiviert")
		return False

	try:
		from filemind.paperless.client import upload_document

		path = file_info.path
		title = path.stem

		# Tags und Metadaten
		tags = [file_type.value]
		correspondent = None

		logger.info(f"Hochlade zu Paperless: {path.name}")

		result = upload_document(
			path,
			title=title,
			tags=tags,
			correspondent=correspondent,
			doc_type=file_type.value,
		)

		logger.info(f"Paperless Upload erfolgreich: Document ID {result.get('document')}")
		return True

	except ImportError:
		logger.warning("Paperless-Client nicht verfügbar")
		return False

	except Exception as e:
		logger.error(f"Fehler beim Paperless-Upload: {e}")
		return False


def _handle_ocr(file_info: "Any") -> Optional[str]:
	"""Versucht, OCR auf ein Bild anzuwenden.

	Returns: Extrahierter Text oder None bei Fehler/Deaktivierung.
	"""
	ocr_config = get_section("ocr", {})
	try:
		from filemind.integrations.ocr import ocr_to_text

		path = file_info.path
		logger.info(f"Führe OCR durch: {path.name}")

		text = ocr_to_text(path)
		logger.info(f"OCR erfolgreich: {len(text)} Zeichen extrahiert")

		return text

	except ImportError:
		logger.warning("OCR-Modul nicht verfügbar")
		return None

	except Exception as e:
		logger.error(f"Fehler bei OCR: {e}")
		return None


def _handle_deduplication(file_info: "Any") -> bool:
	"""Prüft auf Duplikate mittels Hash-Vergleich.

	Returns: True wenn Duplikat, False wenn neu.
	"""
	try:
		from filemind.storage.hash_store import is_duplicate, register_file

		path = file_info.path

		if is_duplicate(path):
			logger.warning(f"Duplikat erkannt: {path.name}")
			# Nicht löschen; nur überspringen. Caller entscheidet weiteres Verhalten.
			return True

		# Registrierung markiert die Datei als verarbeitet
		register_file(path)
		logger.debug(f"Datei registriert im Hash-Store: {path.name}")

		return False

	except ImportError:
		logger.warning("Hash-Store nicht verfügbar")
		return False

	except Exception as e:
		logger.error(f"Fehler bei Duplikat-Check: {e}")
		return False


def _handle_storage(file_info: "Any", file_type: FileType) -> bool:
	"""Speichert eine Datei im Zielordner basierend auf Dateityp.

	Returns: True bei Erfolg, False bei Fehler.
	"""
	# Neue Storage-Logik:
	# - Dokumente werden in `storage.base_documents_path` kopiert
	# - Bilder -> `storage.base_media_path\YYYY\<Country>\<City>\YYYY-MM-DD_name.ext`
	# - Andere Dateien ähnlich wie Bilder, Name aber nur aus Metadaten
	try:
		from filemind.integrations.metadata_extractor import (
			get_file_creation_date,
			get_country_city_from_file,
		)
		from filemind.integrations.ai_naming import generate_smart_name
		from filemind.storage.hash_store import compute_sha256, find_paths_by_hash, is_hash_in_directory, register_file

		config = _load_config()
		storage_cfg = config.get("storage", {})
		base_media = Path(storage_cfg.get("base_media_path", "/media/storage_media"))
		base_docs = Path(storage_cfg.get("base_documents_path", "/media/storage_main/scanner"))
		max_per_folder = int(storage_cfg.get("max_files_per_subfolder", 3000))

		path = Path(file_info.path)

		# Helper: ensure unique filename (append _1, _2 ...)
		def _ensure_unique_filename(p: Path) -> Path:
			if not p.exists():
				return p
			stem = p.stem
			suffix = p.suffix
			idx = 1
			while True:
				candidate = p.with_name(f"{stem}_{idx}{suffix}")
				if not candidate.exists():
					return candidate
				idx += 1

		# Helper: choose year folder with capacity logic
		def _choose_year_folder(base: Path, year: str) -> Path:
			# Try base/year, if too many items then year_1, year_2...
			candidate = base / year
			if not candidate.exists():
				return candidate
			def _count_recursive(p: Path) -> int:
				count = 0
				for _, dirs, files in os.walk(p):
					count += len(dirs) + len(files)
				return count

			if _count_recursive(candidate) < max_per_folder:
				return candidate
			idx = 1
			while True:
				cand = base / f"{year}_{idx}"
				if not cand.exists():
					return cand
				if _count_recursive(cand) < max_per_folder:
					return cand
				idx += 1

		# DOCUMENTS: copy as-is into base_docs
		if file_type in (FileType.DOCUMENT_IMAGE, FileType.TEXT_DOCUMENT):
			base_docs.mkdir(parents=True, exist_ok=True)
			file_hash = compute_sha256(path)
			# If identical file already exists in docs, skip copying
			if is_hash_in_directory(file_hash, base_docs):
				logger.info(f"Datei bereits in Dokumenten-Ziel vorhanden, überspringe: {path.name}")
				# Registriere die Quell-Datei dennoch
				register_file(path)
				return True

			target = base_docs / path.name
			target = _ensure_unique_filename(target)
			logger.info(f"Kopiere Dokument: {path} -> {target}")
			shutil.copy2(str(path), str(target))
			# Registriere Ziel und Quelle
			register_file(target)
			register_file(path)
			return True

		# IMAGES and OTHER
		# Determine date and year
		file_date = get_file_creation_date(path)
		year = file_date.split("-")[0]

		# Try to determine country/city
		loc = None
		try:
			loc = get_country_city_from_file(path)
		except Exception:
			loc = None

		country = None
		city = None
		if loc:
			country = loc.get("country")	
			city = loc.get("city")

		# Generate name depending on type
		if file_type == FileType.REAL_IMAGE:
			# Prefer AI naming
			try:
				stem = generate_smart_name(path)
			except Exception:
				stem = path.stem
		else:
			# Other files: use metadata_extractor fallback
			try:
				from filemind.integrations.metadata_extractor import extract_metadata_name
				meta_name = extract_metadata_name(path)
				stem = Path(meta_name).stem
			except Exception:
				stem = path.stem

		# sanitize stem
		stem = str(stem)
		stem = re.sub(r"[^a-z0-9_\-]+", "_", stem.lower()).strip("_")[:60]
		ext = path.suffix.lower()

		# Build base year folder
		year_folder = _choose_year_folder(base_media, year)
		# Build nested country/city structure
		if country:
			if city:
				target_dir = year_folder / country / city
			else:
				target_dir = year_folder / country
		else:
			# No country -> put directly into year folder
			target_dir = year_folder

		target_dir.mkdir(parents=True, exist_ok=True)

		# If an identical file already exists in target_dir (by hash), skip copying
		file_hash = compute_sha256(path)
		if is_hash_in_directory(file_hash, target_dir):
			logger.info(f"Identische Datei bereits im Ziel vorhanden, überspringe: {path.name}")
			register_file(path)
			return True

		# Final filename
		final_name = f"{file_date}_{stem}{ext}"
		target = target_dir / final_name
		# Ensure uniqueness when filename collision
		target = _ensure_unique_filename(target)

		logger.info(f"Kopiere Datei: {path} -> {target}")
		shutil.copy2(str(path), str(target))
		# Register both
		register_file(target)
		register_file(path)
		return True

	except ImportError as e:
		logger.warning(f"Storage-Module nicht verfügbar: {e}")
		return False

	except Exception as e:
		logger.error(f"Fehler beim Speichern: {e}")
		return False


def _handle_ai_naming(file_info: "Any", file_type: FileType) -> Optional[str]:
	"""Versucht, einen intelligenten Namen zu generieren.

	Returns: Neuer Name oder None bei Fehler/Deaktivierung.
	"""
	config = _load_config()
	ai_config = config.get("ai", {})

	if not ai_config.get("enabled", False):
		logger.debug("AI-Naming ist deaktiviert")
		return None

	try:
		from filemind.integrations.ai_naming import generate_smart_name

		path = file_info.path
		logger.debug(f"Generiere intelligenten Namen: {path.name}")

		smart_name = generate_smart_name(path)
		logger.info(f"Rohes Smart-Name-Ergebnis: {smart_name!r}")

		# If AI returned None (module disabled or error), propagate None
		if smart_name is None:
			logger.debug("AI-Naming lieferte None")
			return None

		# Build final filename: always prefix with YYYY-MM-DD and preserve original extension
		from uuid import uuid4
		# Use file creation date (Erstelldatum) from metadata_extractor
		try:
			from filemind.integrations.metadata_extractor import get_file_creation_date
			file_date = get_file_creation_date(path)
		except Exception:
			# Fallback to now if helper not available
			file_date = datetime.now().date().isoformat()
		orig_ext = path.suffix.lower()

		# If AI returned an empty string, fallback to unique id
		if isinstance(smart_name, str) and smart_name.strip() == "":
			stem = uuid4().hex[:8]
		else:
			# Take stem part (remove any extension if present)
			stem = Path(smart_name).stem if isinstance(smart_name, str) else str(smart_name)

		# Sanitize stem: keep lowercase, underscores and alnum
		stem = re.sub(r"[^a-z0-9_]+", "", stem.lower().replace(" ", "_"))[:30]
		if not stem:
			stem = uuid4().hex[:8]

		final_name = f"{file_date}_{stem}{orig_ext}"

		# Try to rename the file in-place so downstream storage sees the new name
		try:
			new_path = path.with_name(final_name)
			logger.info(f"Umbenennen: {path.name} -> {final_name}")
			path.rename(new_path)
			# Update file_info to reflect new path
			file_info.path = new_path
			return final_name
		except Exception as e:
			logger.error(f"Konnte Datei nicht umbenennen: {e}")
			# On failure fall back to returning None so storage can use default naming
			return None

	except ImportError:
		logger.warning("AI-Naming-Modul nicht verfügbar")
		return None

	except Exception as e:
		logger.error(f"Fehler bei AI-Naming: {e}")
		return None


def route_file(path: Path) -> bool:
	"""Hauptfunktion: Klassifiziert und routet eine Datei.

	Diese Funktion ist das Herzstück des Routers. Sie:
	1. Klassifiziert die Datei
	2. Prüft auf Duplikate
	3. Führt Dateitypabhängige Operationen durch
	4. Speichert die Datei im Zielordner

	Args:
		path: Dateipfad zur zu routenden Datei.

	Returns:
		True bei Erfolg, False bei Fehler.

	Examples:
		>>> from pathlib import Path
		>>> from filemind.routing.router import route_file
		>>> route_file(Path("input/document.pdf"))
		True
	"""
	path = Path(path)

	logger.info(f"Beginne Verarbeitung: {path.name}")

	try:
		# Prüfe ob Datei existiert
		if not path.exists():
			logger.error(f"Datei nicht gefunden: {path}")
			return False

		# 1. Klassifizierung
		logger.debug(f"Klassifiziere Datei: {path.name}")
		classification = classify_file(path)

		logger.info(
			f"Klassifizierung: {path.name} -> {classification.file_type.value} "
			f"(Vertrauen: {classification.confidence:.2f})"
		)

		file_info = classification.file_info
		file_type = classification.file_type

		# 2. Duplikat-Check
		if _handle_deduplication(file_info):
			logger.warning(f"Datei übersprungen (Duplikat): {path.name}")
			# Datei nicht löschen; sie wurde bereits verarbeitet
			return False

		# 3. Dateitypabhängige Verarbeitung
		if file_type == FileType.DOCUMENT_IMAGE:
			logger.debug("Verarbeite als Dokumentenbild")
			# Versuche OCR
			ocr_text = _handle_ocr(file_info)
			# Versuche Paperless-Upload
			_handle_paperless_upload(file_info, file_type)
			# Speichere auch lokal
			_handle_storage(file_info, file_type)

		elif file_type == FileType.TEXT_DOCUMENT:
			logger.debug("Verarbeite als Textdokument")
			# Versuche Paperless-Upload
			_handle_paperless_upload(file_info, file_type)
			# Speichere auch lokal
			_handle_storage(file_info, file_type)

		elif file_type == FileType.REAL_IMAGE:
			logger.debug("Verarbeite als Bild")
			# Generiere intelligenten Namen
			_handle_ai_naming(file_info, file_type)
			# Speichere
			_handle_storage(file_info, file_type)

		elif file_type in (FileType.VIDEO, FileType.AUDIO, FileType.ARCHIVES, FileType.OTHER):
			logger.debug(f"Verarbeite als {file_type.value}")
			# Generiere intelligenten Namen
			_handle_ai_naming(file_info, file_type)
			# Speichere
			_handle_storage(file_info, file_type)

		logger.info(f"Verarbeitung abgeschlossen: {path.name}")
		return True

	except Exception as e:
		logger.error(f"Fehler beim Routing von {path}: {e}", exc_info=True)
		return False


