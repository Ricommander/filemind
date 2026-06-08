"""OCR-Schnittstelle für filemind.

Dieses Modul bietet eine saubere Abstraktionsschicht für OCR-Operationen.
Die Implementierung verwendet EasyOCR, um zuverlässig Text aus Bildern
zu extrahieren.

Funktionalität:
- ocr_to_text(path: Path) -> str: Extrahiert Text aus einer Bilddatei.
- Robuste Fehlerbehandlung und Logging.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from easyocr import Reader
from filemind.logging_utils.logger import get_logger

logger = get_logger(__name__)

SUPPORTED_FORMATS = {".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".gif"}
_reader: Optional[Reader] = None


def _get_ocr_reader() -> Reader:
    """Initialisiert und cached den EasyOCR-Reader."""
    global _reader
    if _reader is None:
        try:
            _reader = Reader(["de", "en"], gpu=False)
        except Exception as e:
            error_msg = f"OCR-Reader konnte nicht initialisiert werden: {e}"
            logger.error(error_msg)
            raise RuntimeError(error_msg) from e
    return _reader


def _normalize_text(text: str) -> str:
    """Normalisiert OCR-Ausgabe und entfernt überflüssige Leerzeichen."""
    return re.sub(r"\s+", " ", text).strip()


def _perform_ocr(path: Path) -> str:
    """Führt die OCR-Erkennung auf einer Bilddatei aus."""
    try:
        reader = _get_ocr_reader()
        results = reader.readtext(str(path), detail=0, paragraph=True)

        if isinstance(results, str):
            text = results
        else:
            text = " ".join(result for result in results if result)

        return _normalize_text(text)

    except Exception as e:
        error_msg = f"OCR-Verarbeitung fehlgeschlagen für {path}: {e}"
        logger.error(error_msg)
        raise RuntimeError(error_msg) from e


def ocr_to_text(path: Path) -> str:
    """Extrahiert Text aus einer Bilddatei mittels OCR.

    Args:
        path: Pfad zur Bilddatei (z. B. PNG, JPG, TIFF).

    Returns:
        Der extrahierte Text.

    Raises:
        ValueError: Falls die Datei nicht existiert oder kein
            unterstütztes Format hat.
        RuntimeError: Falls die OCR-Verarbeitung fehlschlägt.
    """
    logger.debug(f"OCR-Verarbeitung gestartet für: {path}")

    if not path.exists():
        error_msg = f"Datei nicht gefunden: {path}"
        logger.error(error_msg)
        raise ValueError(error_msg)

    if not path.is_file():
        error_msg = f"Pfad ist keine Datei: {path}"
        logger.error(error_msg)
        raise ValueError(error_msg)

    if not _validate_image_file(path):
        error_msg = f"Nicht unterstütztes Format: {path.suffix}"
        logger.error(error_msg)
        raise ValueError(error_msg)

    text = _perform_ocr(path)
    logger.info(f"OCR erfolgreich für '{path.name}' - erkannter Text-Länge: {len(text)}")
    logger.debug(f"OCR-Ausgabe: {text}")
    return text


def _validate_image_file(path: Path) -> bool:
    """Validiert, ob eine Datei eine unterstützte Bilddatei ist."""
    return path.suffix.lower() in SUPPORTED_FORMATS
