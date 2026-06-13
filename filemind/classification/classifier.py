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
from typing import Iterable, Optional, Tuple

from filemind.config import get_section
from filemind.core.models import ClassificationResult, FileInfo, FileType
from filemind.logging_utils.logger import get_logger

logger = get_logger(__name__)

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

_TEXT_EXTENSIONS: set[str] = {"pdf", "doc", "docx", "odt", "txt", "md", "rtf"}

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


def _normalize_extensions(extensions: Iterable[str]) -> set[str]:
    return {
        extension.strip().lstrip(".").lower()
        for extension in extensions
        if isinstance(extension, str) and extension.strip()
    }


def _get_classification_extensions() -> dict[str, set[str]]:
    classification = get_section("classification", default={})

    return {
        "image": _normalize_extensions(classification.get("image_extensions", []))
        or _IMAGE_EXTENSIONS,
        "text_document": _normalize_extensions(classification.get("text_document_extensions", []))
        or _TEXT_EXTENSIONS,
        "video": _normalize_extensions(classification.get("video_extensions", []))
        or _VIDEO_EXTENSIONS,
        "audio": _normalize_extensions(classification.get("audio_extensions", []))
        or _AUDIO_EXTENSIONS,
        "archive": _normalize_extensions(classification.get("archive_extensions", []))
        or _ARCHIVE_EXTENSIONS,
    }


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
    doc_indicators: Iterable[str] = (
        "scan",
        "scanned",
        "document",
        "doc",
        "receipt",
        "invoice",
        "page",
    )
    if any(token in name for token in doc_indicators):
        return 30.0

    # TIFF/TIF werden oft für Scans verwendet
    if ext in {"tif", "tiff"}:
        return 25.0

    # Standard-Fallback: kein erkennbarer Text
    return 0.0


def _get_document_image_thresholds() -> Tuple[int, float]:
    """Liest die Schwellen für die Dokumentenbild-Erkennung aus der Konfiguration.

    Returns:
        (min_words, max_colorfulness)
        - min_words: Mindestanzahl per OCR erkannter Wörter, damit ein Bild
          überhaupt als Dokument in Frage kommt ("viel Text").
        - max_colorfulness: Maximaler Farbwert (Hasler/Süsstrunk), bis zu dem ein
          textreiches Bild noch als Dokument gilt. Darüber wird es als (buntes)
          Foto behandelt, z. B. eine Einladungskarte.
    """

    classification = get_section("classification", default={})

    try:
        min_words = int(classification.get("document_image_min_words", 8))
    except (TypeError, ValueError):
        min_words = 8

    try:
        max_colorfulness = float(classification.get("document_image_max_colorfulness", 18.0))
    except (TypeError, ValueError):
        max_colorfulness = 18.0

    return min_words, max_colorfulness


def _compute_colorfulness(path: Path) -> Optional[float]:
    """Misst, wie farbig ein Bild ist (Metrik nach Hasler/Süsstrunk).

    Ein Wert nahe 0 bedeutet (nahezu) Graustufen, wie sie für gescannte oder
    abfotografierte Dokumente typisch sind. Höhere Werte stehen für kräftige
    Farben (Fotos, bunte Karten). Grobe Orientierung: < 15 wenig farbig,
    ~33 mäßig farbig, > 45 deutlich farbig.

    Returns:
        Den Farbwert oder None, wenn das Bild nicht analysiert werden kann.
    """

    try:
        import numpy as np
        from PIL import Image

        with Image.open(path) as img:
            img = img.convert("RGB")
            # Für ein Farbmaß reicht eine verkleinerte Version - das hält die
            # Analyse auch bei großen Fotos schnell.
            img.thumbnail((512, 512))
            arr = np.asarray(img, dtype="float32")

        red, green, blue = arr[..., 0], arr[..., 1], arr[..., 2]
        rg = red - green
        yb = 0.5 * (red + green) - blue
        std_root = float(np.sqrt(rg.std() ** 2 + yb.std() ** 2))
        mean_root = float(np.sqrt(rg.mean() ** 2 + yb.mean() ** 2))
        return std_root + 0.3 * mean_root

    except Exception as exc:
        logger.debug(f"Farbanalyse fehlgeschlagen für {path.name}: {exc}")
        return None


def _classify_image_type(path: Path) -> Tuple[FileType, float]:
    """Entscheidet zwischen Dokumentenbild und echtem Foto.

    Ein Bild gilt nur dann als Dokument, wenn OCR viel Text findet UND das Bild
    farbarm ist. Bunte Bilder mit Text (z. B. Geburtstagseinladungskarten)
    bleiben echte Fotos. Findet OCR kaum Text, greifen Dateiname-/Endung-Heuristiken.

    Returns:
        (FileType, confidence)
    """

    min_words, max_colorfulness = _get_document_image_thresholds()

    text = _extract_text_from_image(path)
    word_count = len(re.findall(r"\w+", text))

    if word_count >= min_words:
        colorfulness = _compute_colorfulness(path)
        if colorfulness is None or colorfulness <= max_colorfulness:
            color_repr = f"{colorfulness:.1f}" if colorfulness is not None else "n/a"
            logger.debug(
                f"{path.name}: {word_count} Wörter, Farbwert={color_repr} -> Dokumentenbild"
            )
            return FileType.DOCUMENT_IMAGE, 0.75
        logger.info(
            f"{path.name}: viel Text ({word_count} Wörter), aber farbig "
            f"(Farbwert={colorfulness:.1f} > {max_colorfulness}) -> als Foto behandelt"
        )
        return FileType.REAL_IMAGE, 0.9

    # Wenig/kein OCR-Text: auf Dateiname und Endung zurückfallen.
    name = path.name.lower()
    ext = _ext_of(path)
    doc_indicators: Iterable[str] = (
        "scan",
        "scanned",
        "document",
        "doc",
        "receipt",
        "invoice",
        "page",
    )
    if any(token in name for token in doc_indicators):
        return FileType.DOCUMENT_IMAGE, 0.75
    if ext in {"tif", "tiff"}:
        return FileType.DOCUMENT_IMAGE, 0.75
    return FileType.REAL_IMAGE, 0.9


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
    extensions = _get_classification_extensions()

    # Textdokumente
    if ext in extensions["text_document"]:
        return ClassificationResult(
            file_info=file_info, file_type=FileType.TEXT_DOCUMENT, confidence=0.98
        )

    # Videos
    if ext in extensions["video"]:
        return ClassificationResult(file_info=file_info, file_type=FileType.VIDEO, confidence=0.98)

    # Audio
    if ext in extensions["audio"]:
        return ClassificationResult(file_info=file_info, file_type=FileType.AUDIO, confidence=0.9)

    # Archive
    if ext in extensions["archive"]:
        return ClassificationResult(
            file_info=file_info, file_type=FileType.ARCHIVES, confidence=0.98
        )

    # Bilder (real_image vs. document_image): Dokument nur bei viel Text UND
    # wenig Farbe; bunte Bilder mit Text bleiben echte Fotos.
    if ext in extensions["image"]:
        file_type, confidence = _classify_image_type(path)
        return ClassificationResult(
            file_info=file_info,
            file_type=file_type,
            confidence=confidence,
        )

    # Fallback: OTHER
    return ClassificationResult(
        file_info=file_info,
        file_type=FileType.OTHER,
        confidence=0.5,
    )
