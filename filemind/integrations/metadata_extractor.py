"""Metadaten-Extraktion für filemind.

Dieses Modul bietet eine Abstraktionsschicht für die Extraktion von
Metadaten aus verschiedenen Dateitypen (Video, Audio, Archive, sonstige).
Aktuell ist eine Stub-Implementierung vorhanden, die später gegen spezialisierte
Tools (ffprobe für Videos, mutagen für Audio, etc.) ausgetauscht werden kann.

Funktionalität:
- extract_metadata_name(path: Path) -> str: Generiert Namen basierend auf
  Dateityp und Metadaten (ggf. Timestamp).
- Unterstützung für Videos, Audio, Archive und sonstige Dateien.
- Robuste Fehlerbehandlung und Logging.
"""

from __future__ import annotations

import json
import logging
import math
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from filemind.classification.classifier import classify_file
from filemind.config import get_language, get_section
from filemind.core.models import FileType
from filemind.performance import measure

logger = logging.getLogger(__name__)


def _reverse_geocode(lat: float, lon: float) -> Optional[Dict[str, str]]:
    """Versucht, Reverse-Geocoding mittels `geopy` durchzuführen.

    Die Sprache der Orts- und Ländernamen richtet sich nach der
    konfigurierten Zielsprache (``language`` in config.yaml), z. B.
    "Deutschland" (de) vs. "Germany" (en).

    Gibt ein Dict mit Keys `country` und `city` zurück oder `None`,
    falls kein Ergebnis oder `geopy` nicht verfügbar ist.
    """
    try:
        from geopy.geocoders import Nominatim
    except Exception:
        logger.warning(
            "geopy nicht installiert; Reverse-Geocoding übersprungen - "
            "Medien werden ohne Land/Stadt-Ordner abgelegt"
        )
        return None

    try:
        with measure("metadata.reverse_geocode") as measurement:
            geolocator = Nominatim(user_agent="filemind")
            loc = geolocator.reverse((lat, lon), language=get_language(), timeout=10)
            measurement["resolved"] = loc is not None
        if not loc:
            return None
        adr = loc.raw.get("address", {})
        country = adr.get("country")
        # city kann in verschiedenen Feldern stecken
        city = adr.get("city") or adr.get("town") or adr.get("village") or adr.get("municipality")
        return {"country": country, "city": city}
    except Exception as e:
        logger.warning(f"Reverse-Geocoding fehlgeschlagen für {lat},{lon}: {e}")
        return None


def get_country_city_from_file(
    path: Path, allow_ai_fallback: bool = True
) -> Optional[Dict[str, Any]]:
    """Ermittelt den Ort aus Metadaten oder, wenn GPS fehlt, aus dem Bild.

    Koordinaten aus unterstützten Metadatenquellen haben Vorrang und werden
    reverse-geocodiert. Ohne Koordinaten darf das Vision-Modell Land und Stadt
    unabhängig voneinander nur ab 95 % Konfidenz übernehmen.
    """
    try:
        classification = get_section("classification", {}) or {}
        configured_extensions = classification.get("image_extensions", [])
        if configured_extensions:
            image_extensions = {
                str(extension).strip().lstrip(".").casefold()
                for extension in configured_extensions
                if str(extension).strip()
            }
        else:
            image_extensions = {
                "jpg", "jpeg", "png", "webp", "bmp", "tiff", "tif",
                "heic", "heif", "gif", "nef",
            }
        if path.suffix.lstrip(".").casefold() not in image_extensions:
            return None

        gps = _extract_gps_from_image(path)
        if gps:
            lat = gps.get("lat")
            lon = gps.get("lon")
            if lat is None or lon is None:
                return None
            location = _reverse_geocode(lat, lon)
            if location:
                return location
            # Bei vorhandenen Koordinaten bleibt Geocoding die maßgebliche Quelle.
            logger.debug(
                f"Reverse-Geocoding lieferte kein Ergebnis für {path}; Datei wird ohne "
                f"Land/Stadt-Struktur abgelegt"
            )
            return None

        ai_config = get_section("ai", {}) or {}
        if not allow_ai_fallback or not ai_config.get("enabled", False):
            return None

        from filemind.integrations.ai_naming import infer_image_location

        return infer_image_location(path)
    except Exception as e:
        logger.debug(f"Konnte Country/City nicht ermitteln für {path}: {e}")
        return None


def extract_metadata_name(path: Path) -> str:
    """Extrahiert Metadaten und generiert einen aussagekräftigen Namen.

    Diese Funktion analysiert eine Datei und generiert einen Namen basierend
    auf ihrem Dateityp und verfügbaren Metadaten. Aktuell ist eine
    Stub-Implementierung vorhanden, die Timestamps und Dateitypen verwendet.

    Args:
            path: Pfad zur zu analysierenden Datei.

    Returns:
            Ein generierter Name basierend auf Dateityp und Metadaten.

    Raises:
            ValueError: Falls die Datei nicht existiert.
            RuntimeError: Falls die Metadaten-Extraktion fehlschlägt.

    Examples:
            >>> from pathlib import Path
            >>> name = extract_metadata_name(Path("video.mp4"))
            >>> print(name)
            video_20260525_120000

            >>> name = extract_metadata_name(Path("song.mp3"))
            >>> print(name)
            audio_20260525_120000

            >>> name = extract_metadata_name(Path("archive.zip"))
            >>> print(name)
            archive_20260525_120000
    """
    logger.debug(f"Metadaten-Extraktion gestartet für: {path}")

    # Validierung: Datei existiert?
    if not path.exists():
        error_msg = f"Datei nicht gefunden: {path}"
        logger.error(error_msg)
        raise ValueError(error_msg)

    # Klassifizierung durchführen
    try:
        classification = classify_file(path)
        file_type = classification.file_type
        logger.debug(f"Erkannter Dateityp: {file_type.value}")

        # Extraktion basierend auf Dateityp
        if file_type == FileType.VIDEO:
            name = _extract_video_metadata_name(path)
        elif file_type == FileType.AUDIO:
            name = _extract_audio_metadata_name(path)
        elif file_type == FileType.ARCHIVES:
            name = _extract_archive_metadata_name(path)
        elif file_type in (FileType.REAL_IMAGE, FileType.DOCUMENT_IMAGE):
            # Für Bilder: versuche GPS/EXIF auszulesen und verwende Geo-Info
            gps = _extract_gps_from_image(path)
            if gps:
                lat = gps.get("lat")
                lon = gps.get("lon")
                # Erzeuge einen aussagekräftigen Namen mit Geo-Kürzel.
                # Auch hier zählt das EXIF-Aufnahmedatum als Erstelldatum des
                # Bildes; nur wenn keines vorliegt, dient der Datei-Zeitstempel
                # als Rückfallebene.
                capture = _extract_image_capture_datetime(path)
                timestamp = (
                    capture.strftime("%Y%m%d_%H%M%S")
                    if capture is not None
                    else _get_file_timestamp(path)
                )
                name = f"photo_{lat:.6f}_{lon:.6f}_{timestamp}"
            else:
                name = _extract_generic_metadata_name(path)
        else:
            name = _extract_generic_metadata_name(path)

        logger.info(f"Metadaten verarbeitet: {path.name} -> {name}")
        return name

    except ValueError:
        raise
    except Exception as e:
        error_msg = f"Fehler bei Metadaten-Extraktion für {path}: {e}"
        logger.error(error_msg)
        raise RuntimeError(error_msg) from e


def _get_file_timestamp(path: Path) -> str:
    """Extrahiert den Änderungszeitstempel einer Datei.

    Args:
            path: Dateipfad.

    Returns:
            Zeitstempel im Format YYYYMMDD_HHMMSS.
    """
    try:
        mtime = path.stat().st_mtime
        dt = datetime.fromtimestamp(mtime)
        return dt.strftime("%Y%m%d_%H%M%S")
    except Exception as e:
        logger.warning(f"Fehler beim Auslesen des Timestamps von {path}: {e}")
        # Fallback: Aktuelles Datum/Zeit
        return datetime.now().strftime("%Y%m%d_%H%M%S")


# Untergrenze für plausible Aufnahmedaten: Digitalfotos entstehen frühestens in den
# 1990ern; ältere Werte (häufig 1970 = Unix-Epoch) sind ungültige Platzhalter.
_MIN_PLAUSIBLE_CAPTURE_YEAR = 1990


def _is_plausible_capture_datetime(value: datetime) -> bool:
    """Prüft, ob ein EXIF-Aufnahmedatum plausibel (gültig) ist.

    Ungültig sind Daten vor ``_MIN_PLAUSIBLE_CAPTURE_YEAR`` (typische
    Platzhalter wie 1970) sowie Daten in der Zukunft (fehlerhafte Kamera-Uhr).
    """
    if value.year < _MIN_PLAUSIBLE_CAPTURE_YEAR:
        return False
    if value.date() > datetime.now().date():
        return False
    return True


def _extract_image_capture_datetime(path: Path) -> Optional[datetime]:
    """Liest das Aufnahmedatum eines Bildes aus dessen EXIF-Daten.

    Maßgeblich ist **ausschließlich** ``DateTimeOriginal`` - der Zeitpunkt, zu dem
    das Bild tatsächlich aufgenommen wurde. Die EXIF-Felder ``DateTime`` (Zeitpunkt
    der letzten Bearbeitung durch Software) und ``DateTimeDigitized``
    (Digitalisierung) werden bewusst NICHT als Aufnahmedatum gewertet: Bei
    weitergereichten oder bearbeiteten Bildern (Messenger, Re-Export, Bildeditor)
    setzt Software diese Felder auf den Verarbeitungszeitpunkt. Dieser kann später
    liegen als die echten Datei-Zeitstempel und würde dann - da das Aufnahmedatum
    Vorrang hat - ein irreführendes Datum im Dateinamen erzeugen.

    Das gefundene Datum wird zusätzlich auf Plausibilität geprüft
    (siehe ``_is_plausible_capture_datetime``); unplausible Werte gelten als
    ungültig und führen zu ``None``.

    Gibt ``None`` zurück, wenn kein gültiges Aufnahmedatum vorliegt, die Datei
    kein lesbares Bild ist oder Pillow nicht installiert ist.
    """
    try:
        from PIL import Image
        from PIL.ExifTags import TAGS
    except Exception:
        logger.debug("Pillow nicht verfügbar; EXIF-Datum-Extraktion übersprungen")
        return None

    try:
        with Image.open(path) as img:
            exif = img._getexif()
        if not exif:
            return None

        tag_values = {TAGS.get(tag, tag): value for tag, value in exif.items()}
        raw = tag_values.get("DateTimeOriginal")
        if not raw:
            return None

        raw = str(raw).strip()
        # EXIF speichert Zeitstempel als "YYYY:MM:DD HH:MM:SS".
        captured: Optional[datetime] = None
        for fmt in ("%Y:%m:%d %H:%M:%S", "%Y:%m:%d"):
            try:
                captured = datetime.strptime(raw, fmt)
                break
            except ValueError:
                continue

        if captured is None:
            logger.debug(f"Unerwartetes EXIF-Datumsformat {raw!r} in {path}")
            return None

        if not _is_plausible_capture_datetime(captured):
            logger.debug(f"Unplausibles EXIF-Aufnahmedatum {captured.isoformat()} in {path}")
            return None

        return captured
    except Exception as e:
        logger.debug(f"Konnte EXIF-Aufnahmedatum nicht lesen für {path}: {e}")
        return None


def _get_file_modification_datetime(path: Path) -> Optional[datetime]:
    """Liest das Änderungsdatum (``st_mtime``) der Datei.

    Das Änderungsdatum überdauert in der Regel einen Kopier- oder Sync-Vorgang
    (``shutil.copy2`` sowie die meisten Sync-Tools übernehmen ``mtime``), während
    das Erstelldatum dabei auf den Kopierzeitpunkt gesetzt wird. Deshalb rangiert
    es in der Datums-Priorität vor dem Erstelldatum.

    Returns:
            ``datetime`` des letzten Änderungszeitpunkts oder ``None`` bei Fehler.
    """
    try:
        return datetime.fromtimestamp(path.stat().st_mtime)
    except Exception as e:
        logger.debug(f"Konnte Änderungsdatum nicht lesen für {path}: {e}")
        return None


def _get_file_creation_datetime(path: Path) -> Optional[datetime]:
    """Liest das Erstelldatum der Datei.

    Bevorzugt ``st_birthtime`` (echte Geburtszeit der Datei; verfügbar u. a. auf
    Windows, macOS/BSD und neueren Linux-Kernen via ``statx``). Fehlt sie, dient
    ``st_ctime`` als Rückfallebene - auf Windows ebenfalls die Erstellzeit, auf
    Linux der Zeitpunkt der letzten Inode-Änderung.

    Returns:
            ``datetime`` des Erstellzeitpunkts oder ``None`` bei Fehler.
    """
    try:
        stat_result = path.stat()
        birthtime = getattr(stat_result, "st_birthtime", None)
        timestamp = birthtime if birthtime is not None else stat_result.st_ctime
        return datetime.fromtimestamp(timestamp)
    except Exception as e:
        logger.debug(f"Konnte Erstelldatum nicht lesen für {path}: {e}")
        return None


def get_file_creation_date(path: Path) -> str:
    """Ermittelt das maßgebliche Datum einer Datei als ISO-String ``YYYY-MM-DD``.

    Zweistufige Bestimmung:

    1. Liegt ein gültiges **EXIF-Aufnahmedatum** (``DateTimeOriginal``) vor, wird
       dieses verwendet - es ist der tatsächliche Entstehungszeitpunkt des Bildes
       und hat Vorrang vor allen Datei-Zeitstempeln. So kann ein künstlich alter
       Datei-Zeitstempel (z. B. aus einem Restore) das Aufnahmedatum nicht
       überstimmen.
    2. Fehlt ein Aufnahmedatum, wird aus den übrigen *gültigen* Datumsangaben das
       **älteste** gewählt:

       - **Änderungsdatum** - Datei-``mtime``.
       - **Erstelldatum** - ``st_birthtime`` bzw. ``st_ctime`` der Datei.
       - **Aktuelles Datum** - immer verfügbar (Fallback, zugleich stets das jüngste).

       Hintergrund: Kopier-, Download- und Sync-Vorgänge setzen einzelne
       Zeitstempel auf den (späteren) Verarbeitungszeitpunkt; ohne Aufnahmedatum
       kommt der älteste der übrigen Werte dem Entstehungszeitpunkt am nächsten.

    Diese Funktion ist öffentlich, damit andere Module (z. B. Router)
    konsistent dasselbe Datum aus den Metadaten beziehen können.

    Args:
            path: Pfad zur Datei.

    Returns:
            Datum als ISO-String ``YYYY-MM-DD``.
    """
    # 1. Aufnahmedatum (EXIF DateTimeOriginal) hat Vorrang, sofern vorhanden und gültig.
    capture = _extract_image_capture_datetime(path)
    if capture is not None:
        return capture.date().isoformat()

    # 2. Andernfalls: ältestes der übrigen gültigen Datumsangaben.
    candidates: list[datetime] = []

    # Änderungsdatum (mtime)
    modified = _get_file_modification_datetime(path)
    if modified is not None:
        candidates.append(modified)

    # Erstelldatum (birthtime/ctime)
    created = _get_file_creation_datetime(path)
    if created is not None:
        candidates.append(created)

    # Aktuelles Datum ist immer verfügbar (Fallback und zugleich stets das jüngste).
    candidates.append(datetime.now())

    oldest = min(candidates)
    return oldest.date().isoformat()


def _dms_to_decimal(dms, ref: str) -> float:
    """Converts DMS tuple from EXIF to decimal degrees.

    dms is expected as a tuple of rationals: ((num, den), (num, den), (num, den))
    ref is one of 'N','S','E','W'.
    """

    def _to_float(value):
        # Handle IFDRational-like objects from Pillow, tuples, or plain numbers
        try:
            # Pillow IFDRational has numerator/denominator attributes
            if hasattr(value, "numerator") and hasattr(value, "denominator"):
                return float(value.numerator) / float(value.denominator)
            # If it's a (num, den) tuple
            if isinstance(value, (list, tuple)) and len(value) >= 2:
                return float(value[0]) / float(value[1])
            # Otherwise try casting to float
            return float(value)
        except Exception:
            raise

    try:
        degrees = _to_float(dms[0])
        minutes = _to_float(dms[1])
        seconds = _to_float(dms[2])
        dec = degrees + minutes / 60.0 + seconds / 3600.0
        if ref in ("S", "W"):
            dec = -dec
        return dec
    except Exception:
        raise


def _extract_gps_from_image(path: Path) -> Optional[Dict[str, float]]:
    """Liest GPS aus unterstützten Metadatenquellen, einschließlich XMP und RAW.

    Pillow bleibt als Fallback verfügbar, falls ExifTool nicht installiert ist.
    """
    if shutil.which("exiftool"):
        return _gps_from_metadata(_read_exiftool_metadata(path))
    return _extract_exif_gps_with_pillow(path)


def _read_exiftool_metadata(path: Path) -> list[Dict[str, Any]]:
    """Liest alle verfügbaren Metadaten, ohne Werte davon zu protokollieren."""
    executable = shutil.which("exiftool")
    if not executable:
        logger.debug("ExifTool nicht verfügbar; nutze den Pillow-EXIF-Fallback")
        return []

    try:
        with measure("metadata.exiftool") as measurement:
            result = subprocess.run(
                [executable, "-json", "-n", "-G1", "-a", "-api", "RequestAll=3", str(path)],
                capture_output=True,
                check=True,
                text=True,
                timeout=30,
            )
            payload = json.loads(result.stdout)
        if isinstance(payload, list):
            records = [item for item in payload if isinstance(item, dict)]
            measurement["metadata_tag_count"] = sum(len(record) for record in records)
            return records
    except Exception as e:
        logger.debug(f"ExifTool-Metadaten konnten nicht gelesen werden für {path}: {e}")
    return []


def _gps_from_metadata(metadata: list[Dict[str, Any]]) -> Optional[Dict[str, float]]:
    """Findet gültige GPS-Paare gruppenweise, damit Tags nicht vermischt werden."""
    groups: Dict[str, Dict[str, Any]] = {}
    for record in metadata:
        for key, value in record.items():
            group, _, tag = key.rpartition(":")
            normalized_tag = "".join(char for char in tag.casefold() if char.isalnum())
            if normalized_tag.startswith("gps"):
                groups.setdefault(group, {})[normalized_tag] = value

    # Quelldaten haben Vorrang vor Composite-Tags, die ExifTool daraus ableitet.
    ordered_groups = sorted(groups, key=lambda group: group.casefold().startswith("composite"))
    for group in ordered_groups:
        values = groups[group]
        lat = _coordinate_value(values.get("gpslatitude"))
        lon = _coordinate_value(values.get("gpslongitude"))
        if lat is None or lon is None:
            continue

        lat = _apply_coordinate_ref(lat, values.get("gpslatituderef"), negative_ref="s")
        lon = _apply_coordinate_ref(lon, values.get("gpslongituderef"), negative_ref="w")
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return {"lat": lat, "lon": lon}
    return None


def _coordinate_value(value: Any) -> Optional[float]:
    try:
        coordinate = float(value)
        return coordinate if math.isfinite(coordinate) else None
    except (TypeError, ValueError):
        return None


def _apply_coordinate_ref(value: float, reference: Any, negative_ref: str) -> float:
    if isinstance(reference, str) and reference.casefold() in ("n", "s", "e", "w"):
        return -abs(value) if reference.casefold() == negative_ref else abs(value)
    return value


def _extract_exif_gps_with_pillow(path: Path) -> Optional[Dict[str, float]]:
    """Liest EXIF-GPS mit Pillow, wenn ExifTool nicht verfügbar ist."""
    try:
        from PIL import Image
        from PIL.ExifTags import GPSTAGS, TAGS
    except Exception:
        logger.debug("Pillow nicht verfügbar; GPS-Extraktion übersprungen")
        return None

    try:
        with Image.open(path) as img:
            exif_reader = getattr(img, "getexif", None)
            exif = exif_reader() if callable(exif_reader) else None
            gps_values = None
            if exif:
                get_ifd = getattr(exif, "get_ifd", None)
                if callable(get_ifd):
                    try:
                        gps_values = get_ifd(34853)
                    except Exception:
                        gps_values = None
                for tag, value in exif.items():
                    if TAGS.get(tag, tag) == "GPSInfo" and isinstance(value, dict):
                        gps_values = value

            if not gps_values:
                legacy_reader = getattr(img, "_getexif", None)
                legacy_exif = legacy_reader() if callable(legacy_reader) else None
                if legacy_exif:
                    for tag, value in legacy_exif.items():
                        if TAGS.get(tag, tag) == "GPSInfo" and isinstance(value, dict):
                            gps_values = value
                            break

        if not gps_values:
            return None

        gps_info = {GPSTAGS.get(tag, tag): value for tag, value in gps_values.items()}

        if not gps_info:
            return None

        lat = None
        lon = None
        if "GPSLatitude" in gps_info and "GPSLatitudeRef" in gps_info:
            lat = _dms_to_decimal(gps_info["GPSLatitude"], gps_info["GPSLatitudeRef"])
        if "GPSLongitude" in gps_info and "GPSLongitudeRef" in gps_info:
            lon = _dms_to_decimal(gps_info["GPSLongitude"], gps_info["GPSLongitudeRef"])

        if lat is None or lon is None:
            return None

        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return {"lat": lat, "lon": lon}
        return None

    except Exception as e:
        logger.warning(f"Fehler beim Auslesen von EXIF/GPS aus {path}: {e}")
        return None


def _extract_video_metadata_name(path: Path) -> str:
    """Generiert einen Namen für Video-Dateien.

    Stub-Implementierung: Nutzt Dateityp und Timestamp.
    Zukünftig: Integration mit ffprobe für Video-Metadaten (Dauer, Auflösung, Codec).

    Args:
            path: Pfad zur Video-Datei.

    Returns:
            Generierter Name.
    """
    timestamp = _get_file_timestamp(path)
    file_size_mb = path.stat().st_size / (1024 * 1024)

    # Stub: Verwende Timestamp und Dateigröße
    name = f"video_{timestamp}_{int(file_size_mb)}mb"

    logger.debug(
        f"Video-Metadaten (Stub): {path.name} -> {name} " f"(Größe: {file_size_mb:.1f} MB)"
    )

    return name


def _extract_audio_metadata_name(path: Path) -> str:
    """Generiert einen Namen für Audio-Dateien.

    Stub-Implementierung: Nutzt Dateityp und Timestamp.
    Zukünftig: Integration mit mutagen für Audio-Metadaten (Artist, Titel, Album, Dauer).

    Args:
            path: Pfad zur Audio-Datei.

    Returns:
            Generierter Name.
    """
    timestamp = _get_file_timestamp(path)
    file_size_kb = path.stat().st_size / 1024

    # Stub: Verwende Timestamp und Dateigröße
    name = f"audio_{timestamp}_{int(file_size_kb)}kb"

    logger.debug(
        f"Audio-Metadaten (Stub): {path.name} -> {name} " f"(Größe: {file_size_kb:.1f} KB)"
    )

    return name


def _extract_archive_metadata_name(path: Path) -> str:
    """Generiert einen Namen für Archive.

    Stub-Implementierung: Nutzt Dateityp und Timestamp.
    Zukünftig: Integration mit zipfile, tarfile, etc. für Archiv-Informationen
    (Anzahl Dateien, Gesamtgröße, Kompressionsmethode).

    Args:
            path: Pfad zur Archiv-Datei.

    Returns:
            Generierter Name.
    """
    timestamp = _get_file_timestamp(path)
    file_size_mb = path.stat().st_size / (1024 * 1024)

    # Stub: Verwende Timestamp und Dateigröße
    name = f"archive_{timestamp}_{int(file_size_mb)}mb"

    logger.debug(
        f"Archiv-Metadaten (Stub): {path.name} -> {name} " f"(Größe: {file_size_mb:.1f} MB)"
    )

    return name


def _extract_generic_metadata_name(path: Path) -> str:
    """Generiert einen Namen für sonstige Dateitypen.

    Fallback-Implementierung für unbekannte Dateitypen.
    Nutzt Dateiendung, Dateigröße und Timestamp.

    Args:
            path: Pfad zur Datei.

    Returns:
            Generierter Name.
    """
    timestamp = _get_file_timestamp(path)
    file_extension = path.suffix.lower()[1:]  # Ohne führenden Punkt
    file_size = path.stat().st_size

    # Bestimme Größenunit
    if file_size > 1024 * 1024:
        size_str = f"{file_size / (1024 * 1024):.1f}mb"
    elif file_size > 1024:
        size_str = f"{file_size / 1024:.1f}kb"
    else:
        size_str = f"{file_size}b"

    # Generiere Namen
    name = f"file_{file_extension}_{timestamp}_{size_str}"

    logger.debug(f"Generische Metadaten: {path.name} -> {name} " f"(Größe: {size_str})")

    return name


# Zukünftige Implementierungen können hier eingefügt werden:
#
# Video-Metadaten (ffprobe):
# - _extract_video_duration(path: Path) -> str
# - _extract_video_resolution(path: Path) -> tuple[int, int]
# - _extract_video_codec(path: Path) -> str
#
# Audio-Metadaten (mutagen):
# - _extract_audio_title(path: Path) -> Optional[str]
# - _extract_audio_artist(path: Path) -> Optional[str]
# - _extract_audio_album(path: Path) -> Optional[str]
# - _extract_audio_duration(path: Path) -> str
#
# Archiv-Metadaten (zipfile, tarfile):
# - _count_archive_files(path: Path) -> int
# - _extract_archive_compression(path: Path) -> str
# - _calculate_archive_uncompressed_size(path: Path) -> int
#
# Diese können dann in den jeweiligen _extract_*_metadata_name()-Funktionen
# aufgerufen werden, um aussagekräftigere Namen zu generieren.


def _get_metadata(path: Path) -> Dict[str, Any]:
    """Sammelt verfügbare Metadaten einer Datei.

    Diese Funktion ist eine Schnittstelle für zukünftige Implementierungen.
    Später kann sie Metadaten von verschiedenen Tools abrufen.

    Args:
            path: Dateipfad.

    Returns:
            Wörterbuch mit verfügbaren Metadaten.
    """
    classification = classify_file(path)
    # Bei Bildern versuche GPS-Informationen zu extrahieren
    gps = None
    if classification.file_type in (FileType.REAL_IMAGE, FileType.DOCUMENT_IMAGE):
        gps = _extract_gps_from_image(path)

    metadata = {
        "path": str(path),
        "filename": path.name,
        "extension": path.suffix.lower(),
        "size_bytes": path.stat().st_size,
        "timestamp": _get_file_timestamp(path),
        "file_type": classification.file_type.value,
        "gps": gps,
    }

    # Zukünftig: Dateityp-spezifische Metadaten hinzufügen
    # if classification.file_type == FileType.VIDEO:
    #     metadata["duration"] = _extract_video_duration(path)
    #     metadata["resolution"] = _extract_video_resolution(path)
    # elif classification.file_type == FileType.AUDIO:
    #     metadata["title"] = _extract_audio_title(path)
    #     metadata["artist"] = _extract_audio_artist(path)

    logger.debug(f"Metadaten gesammelt: {metadata}")
    return metadata
