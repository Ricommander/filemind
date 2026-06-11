"""Zentrale Datamodelle für das Projekt `filemind`.

Dieses Modul enthält ausschließlich einfache, dokumentierte Datenklassen
und Enums zur Beschreibung von Dateien, Klassifikationsergebnissen,
Zielpfaden und Hash-Ergebnissen. Es gehört keine Business-Logik hierhin.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Final


class FileType(Enum):
    """Typen von Dateien, wie sie im Projekt verwendet werden.

        Werte:
        - REAL_IMAGE: Ein echtes Foto (z. B. Kameraaufnahme).
        - DOCUMENT_IMAGE: Ein gescannter oder photographierter Dokumenten-Image.
        - TEXT_DOCUMENT: Ein digitales Textdokument (z. B. PDF, DOCX, TXT).
        - VIDEO: Eine Videodatei.
        - AUDIO: Eine Audiodatei.
    - ARCHIVES: Archivdateien wie ZIP, RAR, etc.
        - OTHER: Sonstige Dateitypen.
    """

    REAL_IMAGE = "real_image"
    DOCUMENT_IMAGE = "document_image"
    TEXT_DOCUMENT = "text_document"
    VIDEO = "video"
    AUDIO = "audio"
    ARCHIVES = "archives"
    OTHER = "other"


@dataclass
class FileInfo:
    """Basisinformationen über eine Datei.

    Attributes:
            path: Vollständiger Dateipfad als `Path`.
            size: Dateigröße in Bytes.
            mime_type: MIME-Typ der Datei (z. B. "image/jpeg").
            extension: Dateiendung ohne führenden Punkt (z. B. "jpg").
            created_at: Erstellungszeitpunkt der Datei.
            modified_at: Letzter Änderungszeitpunkt der Datei.
    """

    path: Path
    size: int
    mime_type: str
    extension: str
    created_at: datetime
    modified_at: datetime


@dataclass
class ClassificationResult:
    """Ergebnis der automatischen Klassifikation einer Datei.

    Attributes:
            file_info: Die zugehörigen `FileInfo`-Metadaten.
            file_type: Das erkannte `FileType`.
            confidence: Vertrauenswert der Klassifikation zwischen 0.0 und 1.0.
    """

    file_info: FileInfo
    file_type: FileType
    confidence: float


@dataclass
class TargetLocation:
    """Beschreibt einen Zielpfad, unter dem eine Datei abgelegt werden soll.

    Hinweis: Dieses Modell enthält nur die Datenfelder. Die Erstellung
    oder Validierung des tatsächlichen Pfads erfolgt nicht in diesem Modul.

    Attributes:
            base_folder: Basisordner als `Path` (z. B. Archiv-Root).
            year: Ziel-Jahresordner (z. B. 2026).
            postfix: Numerischer Postfix zur Unterscheidung gleichzeitiger Ziele.
            full_path: Vollständiger Zielpfad als `Path`.
    """

    base_folder: Path
    year: int
    postfix: int
    full_path: Path


@dataclass
class HashResult:
    """Ergebnis einer Hash-Berechnung für eine Datei.

    Attributes:
            sha256: Hexadezimale SHA-256 Repräsentation der Datei.
            is_duplicate: Kennzeichnet, ob die Datei als Duplikat erkannt wurde.
    """

    sha256: str
    is_duplicate: bool = False


__all__: Final = [
    "FileType",
    "FileInfo",
    "ClassificationResult",
    "TargetLocation",
    "HashResult",
]
