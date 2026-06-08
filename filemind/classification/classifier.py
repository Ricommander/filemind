"""Datei-Klassifizierer für filemind.

Dieses Modul entscheidet anhand von Erweiterungen und einer kleinen
Bild-Text-Heuristik, welchem `FileType` eine Datei zugeordnet werden soll.
Es enthält ausschließlich klare, testbare Logik ohne KI-Aufrufe.

Funktionen:
 - `classify_file(path: Path) -> ClassificationResult`
 - `detect_text_in_image(path: Path) -> float` (Stub)
 - `guess_mime_type(path: Path) -> str`
"""

from __future__ import annotations

import mimetypes
import re
from datetime import datetime
from pathlib import Path
from typing import Iterable

from filemind.core.models import FileInfo, FileType, ClassificationResult


# Bekannte Erweiterungen gruppiert nach Typ
_IMAGE_EXTENSIONS: set[str] = {
    "jpg",
    "jpeg",
    "png",
    "webp",
    "bmp",
    "tiff",
    "tif",
    "heic",
    "heif",
    "gif",
}

_TEXT_EXTENSIONS: set[str] = {"pdf", "doc", "docx", "txt", "md", "rtf"}

_VIDEO_EXTENSIONS: set[str] = {"mp4", "mov", "mkv", "avi", "webm"}

_AUDIO_EXTENSIONS: set[str] = {"mp3", "aac", "flac", "wav", "aiff", "m4a"}

_ARCHIVE_EXTENSIONS: set[str] = {
    "zip",
    "rar",
    "7z",
    "tar",
    "gz",
    "bz2",
    "xz",
    "zst",
    "cab",
    "arj",
}


def _ext_of(path: Path) -> str:
    """Gebe die Dateiendung (ohne Punkt, klein) zurück.

    Wenn keine Endung vorhanden ist, wird ein leerer String zurückgegeben.
    """

    return path.suffix.lstrip(".").lower()


def guess_mime_type(path: Path) -> str:
    """Versucht, den MIME-Typ anhand des Dateinamens/der Endung zu erraten.

    Diese Funktion nutzt `mimetypes.guess_type` und gibt im Fehlerfall
    `application/octet-stream` zurück.
    """

    mime, _ = mimetypes.guess_type(path.as_posix())
    return mime or "application/octet-stream"


def _score_text_density(text: str) -> float:
    """Berechnet einen Prozentwert basierend auf der Menge erkannten Texts."""

    normalized_text = text.strip()
    if not normalized_text:
        return 0.0

    words = re.findall(r"\w+", normalized_text)
    if not words:
        return 0.0

    score = min(100.0, float(len(words)) * 10.0)
    return score


def _extract_text_from_image(path: Path) -> str:
    """Extrahiert Text aus einem Bild mithilfe der OCR-Implementierung.

    Bei Fehlern oder nicht unterstützten Bildern wird ein leerer String
    zurückgegeben, damit auf heuristische Annahmen zurückgefallen werden kann.
    """

    try:
        from filemind.integrations.ocr import ocr_to_text

        return ocr_to_text(path)
    except Exception:
        return ""


def detect_text_in_image(path: Path) -> float:
    """Schätzt den Anteil erkennbaren Textes in einem Bild als Prozentwert.

    Die Funktion nutzt jetzt die OCR-Implementierung aus
    `integrations.ocr.ocr_to_text`. Wenn OCR fehlschlägt, greift sie auf
    einfache Heuristiken (Dateiname, typische Extensions für Scans) zurück.

    - Rückgabewerte > 20.0 deuten auf dokumentenartige Bilder hin.
    """

    name = path.name.lower()
    ext = _ext_of(path)

    # OCR anstoßen und den erkannten Text bewerten.
    text = _extract_text_from_image(path)
    score = _score_text_density(text)
    if score > 0.0:
        return score

    # Heuristische Hinweise im Dateinamen
    doc_indicators: Iterable[str] = ("scan", "scanned", "document", "doc", "receipt", "invoice", "page")
    if any(token in name for token in doc_indicators):
        return 30.0

    # TIFF/TIF werden oft für Scans verwendet
    if ext in {"tif", "tiff"}:
        return 25.0

    # Standard-Fallback: kein erkennbarer Text
    return 0.0


def _build_file_info(path: Path) -> FileInfo:
    """Erstellt ein `FileInfo`-Objekt aus dem Dateisystem.

    Raises `FileNotFoundError` wenn `path` nicht existiert oder keine Datei ist.
    """

    if not path.exists():
        raise FileNotFoundError(f"Datei nicht gefunden: {path}")
    stat = path.stat()
    return FileInfo(
        path=path,
        size=int(stat.st_size),
        mime_type=guess_mime_type(path),
        extension=_ext_of(path),
        created_at=datetime.fromtimestamp(stat.st_ctime),
        modified_at=datetime.fromtimestamp(stat.st_mtime),
    )


def classify_file(path: Path) -> ClassificationResult:
    """Klassifiziert die angegebene Datei und liefert ein `ClassificationResult`.

    Die Entscheidung basiert auf der Dateiendung und bei Bildern zusätzlich
    auf dem (stub) ermittelten Textanteil.

    Raises:
        FileNotFoundError: wenn die Datei nicht existiert.
    """

    path = Path(path)
    file_info = _build_file_info(path)
    ext = file_info.extension

    # Textdokumente
    if ext in _TEXT_EXTENSIONS:
        return ClassificationResult(file_info=file_info, file_type=FileType.TEXT_DOCUMENT, confidence=0.98)

    # Videos
    if ext in _VIDEO_EXTENSIONS:
        return ClassificationResult(file_info=file_info, file_type=FileType.VIDEO, confidence=0.98)

    # Audio
    if ext in _AUDIO_EXTENSIONS:
        return ClassificationResult(file_info=file_info, file_type=FileType.AUDIO, confidence=0.9)

    # Archive
    if ext in _ARCHIVE_EXTENSIONS:
        return ClassificationResult(file_info=file_info, file_type=FileType.ARCHIVES, confidence=0.98)

    # Bilder (real_image vs. document_image)
    if ext in _IMAGE_EXTENSIONS:
        text_confidence = detect_text_in_image(path)

        if text_confidence > 20.0:
            # Wahrscheinlich ein gescanntes Dokument oder ähnlich
            return ClassificationResult(
                file_info=file_info,
                file_type=FileType.DOCUMENT_IMAGE,
                confidence=0.75,
            )
        else:
            # Normale Fotografie
            return ClassificationResult(
                file_info=file_info,
                file_type=FileType.REAL_IMAGE,
                confidence=0.9,
            )

    # Fallback: OTHER
    return ClassificationResult(
        file_info=file_info,
        file_type=FileType.OTHER,
        confidence=0.5,
    )
