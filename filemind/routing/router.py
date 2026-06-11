"""Routing-Logik für filemind.

Basierend auf der Klassifikation entscheidet dieses Modul, wie Dateien
weiterverarbeitet werden. Alle Seiteneffekte (Dateioperationen)
erfolgen innerhalb der Handler-Funktionen.

Die Implementierung ruft Integrations-Module als Stubs auf; falls
diese Funktionen nicht vorhanden sind, wird dies sauber geloggt.
"""

from __future__ import annotations

import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from filemind.classification.classifier import classify_file
from filemind.config import get_config, get_section
from filemind.core.models import FileType
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


def _handle_ocr(file_info: "Any") -> Optional[str]:
    """Versucht, OCR auf ein Bild anzuwenden.

    Returns: Extrahierter Text oder None bei Fehler/Deaktivierung.
    """
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
        from filemind.storage.hash_store import is_duplicate

        path = file_info.path

        if is_duplicate(path):
            logger.warning(f"Duplikat erkannt: {path.name}")
            # Nicht löschen; nur überspringen. Caller entscheidet weiteres Verhalten.
            return True

        return False

    except ImportError:
        logger.warning("Hash-Store nicht verfügbar")
        return False

    except Exception as e:
        logger.error(f"Fehler bei Duplikat-Check: {e}")
        return False


def _handle_storage(
    file_info: "Any", file_type: FileType, action: str = "move", ai_name: Optional[str] = None
) -> bool:
    """Speichert eine Datei im Zielordner basierend auf Dateityp.

    Returns: True bei Erfolg, False bei Fehler.
    """
    # Neue Storage-Logik:
    # - Dokumente werden in `storage.base_documents_path` kopiert
    # - Bilder -> `storage.base_media_path\YYYY\<Country>\<City>\YYYY-MM-DD_name.ext`
    # - Andere Dateien ähnlich wie Bilder, Name aber nur aus Metadaten
    try:
        from filemind.integrations.ai_naming import generate_smart_name
        from filemind.integrations.metadata_extractor import (
            get_country_city_from_file,
            get_file_creation_date,
        )
        from filemind.storage.hash_store import (
            compute_sha256,
            is_hash_in_directory,
            register_file,
        )

        config = get_config()
        storage_cfg = config.get("storage", {})
        base_media = Path(storage_cfg.get("base_media_path", "/media/storage_media"))
        base_docs = Path(storage_cfg.get("base_documents_path", "/media/storage_main/scanner"))
        max_files_per_folder = int(storage_cfg.get("max_files_per_folder", 3000))

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
            # Try base/year, if too many files then year_1, year_2...
            candidate = base / year
            if not candidate.exists():
                return candidate

            def _count_recursive(p: Path) -> int:
                count = 0
                for root, _, files in os.walk(p):
                    count += len(files)
                return count

            if _count_recursive(candidate) < max_files_per_folder:
                return candidate
            idx = 1
            while True:
                cand = base / f"{year}_{idx}"
                if not cand.exists():
                    return cand
                if _count_recursive(cand) < max_files_per_folder:
                    return cand
                idx += 1

        # DOCUMENTS: verschiebe als Ganzes in base_docs
        if file_type in (FileType.DOCUMENT_IMAGE, FileType.TEXT_DOCUMENT):
            base_docs.mkdir(parents=True, exist_ok=True)
            file_hash = compute_sha256(path)
            # If identical file already exists in docs, skip moving
            if is_hash_in_directory(file_hash, base_docs):
                logger.warning(
                    f"Datei bereits in Dokumenten-Ziel vorhanden, überspringe: {path.name}"
                )
                register_file(path)
                return True

            target = base_docs / path.name
            target = _ensure_unique_filename(target)
            logger.debug(f"Verschiebe Dokument: {path} -> {target} (action={action})")
            if action == "copy":
                shutil.copy2(str(path), str(target))
            else:
                shutil.move(str(path), str(target))
            register_file(target)
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
        stem: str | None = None
        if file_type == FileType.REAL_IMAGE:
            # Prefer AI naming for real photos; use suggested ai_name if provided
            try:
                if ai_name:
                    stem = ai_name
                else:
                    stem = generate_smart_name(path)
            except Exception:
                stem = None

        if not stem:
            # Use metadata-based naming as fallback for all file types
            try:
                from filemind.integrations.metadata_extractor import extract_metadata_name

                meta_name = extract_metadata_name(path)
                stem = Path(str(meta_name)).stem
            except Exception:
                stem = path.stem

        if not stem:
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
            logger.warning(f"Identische Datei bereits im Ziel vorhanden, überspringe: {path.name}")
            register_file(path)
            return True

        # Final filename
        final_name = f"{file_date}_{stem}{ext}"
        target = target_dir / final_name
        # Ensure uniqueness when filename collision
        target = _ensure_unique_filename(target)

        logger.debug(f"Verschiebe Datei: {path} -> {target} (action={action})")
        if action == "copy":
            shutil.copy2(str(path), str(target))
        else:
            shutil.move(str(path), str(target))
        register_file(target)
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
    config = get_config()
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
        elif isinstance(smart_name, str):
            # Use AI suggestion's stem (remove any extension)
            stem = Path(smart_name).stem
        else:
            stem = str(smart_name)

        # Sanitize stem: keep lowercase, underscores and alnum
        stem = re.sub(r"[^a-z0-9_]+", "", stem.lower().replace(" ", "_"))[:30]
        if not stem:
            stem = uuid4().hex[:8]

        final_name = f"{file_date}_{stem}{orig_ext}"

        # Do NOT rename source file in-place. Return the suggested stem (no date, no ext)
        logger.info(f"AI-Naming schlägt vor: {final_name}")
        return stem

    except ImportError:
        logger.warning("AI-Naming-Modul nicht verfügbar")
        return None

    except Exception as e:
        logger.error(f"Fehler bei AI-Naming: {e}")
        return None


def route_file(path: Path, action: str = "move") -> bool:
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

    logger.debug(f"Beginne Verarbeitung: {path.name}")

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

        def _call_storage_safe(fi, ft, action_val, ai_name_val=None):
            # Try calling the storage handler with new signature; if tests monkeypatch
            # a simple fake that doesn't accept kwargs, fall back to positional call.
            try:
                return _handle_storage(fi, ft, action=action_val, ai_name=ai_name_val)
            except TypeError:
                # Fallback: call as legacy signature
                return _handle_storage(fi, ft)

        # 2. Duplikat-Check
        if _handle_deduplication(file_info):
            logger.warning(f"Datei übersprungen (Duplikat): {path.name}")
            # Datei nicht löschen; sie wurde bereits verarbeitet
            return False

        # 3. Dateitypabhängige Verarbeitung
        if file_type == FileType.DOCUMENT_IMAGE:
            logger.debug("Verarbeite als Dokumentenbild")
            _handle_ocr(file_info)
            _call_storage_safe(file_info, file_type, action)

        elif file_type == FileType.TEXT_DOCUMENT:
            logger.debug("Verarbeite als Textdokument")
            _call_storage_safe(file_info, file_type, action)

        elif file_type == FileType.REAL_IMAGE:
            logger.debug("Verarbeite als Bild")
            # Generiere intelligenten Namen (liefert Vorschlag, benennt nicht um)
            ai_suggested = _handle_ai_naming(file_info, file_type)
            # Speichere (verwende vorgeschlagenen Namen wenn vorhanden)
            _call_storage_safe(file_info, file_type, action, ai_name_val=ai_suggested)

        elif file_type in (FileType.VIDEO, FileType.AUDIO, FileType.ARCHIVES, FileType.OTHER):
            logger.debug(f"Verarbeite als {file_type.value}")
            # Generiere intelligenten Namen (liefert Vorschlag, benennt nicht um)
            ai_suggested = _handle_ai_naming(file_info, file_type)
            # Speichere (verwende vorgeschlagenen Namen wenn vorhanden)
            _call_storage_safe(file_info, file_type, action, ai_name_val=ai_suggested)

        logger.debug(f"Verarbeitung abgeschlossen: {path.name}")
        return True

    except Exception as e:
        logger.error(f"Fehler beim Routing von {path}: {e}", exc_info=True)
        return False
