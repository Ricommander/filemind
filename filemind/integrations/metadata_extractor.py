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

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from filemind.classification.classifier import classify_file
from filemind.config import get_language
from filemind.core.models import FileType

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
        geolocator = Nominatim(user_agent="filemind")
        loc = geolocator.reverse((lat, lon), language=get_language(), timeout=10)
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


def get_country_city_from_file(path: Path) -> Optional[Dict[str, Optional[str]]]:
    """Extrahiert GPS aus Datei und führt Reverse-Geocoding aus.

    Returns: {"country": str|None, "city": str|None} oder None.
    """
    try:
        gps = _extract_gps_from_image(path)
        if not gps:
            return None
        lat = gps.get("lat")
        lon = gps.get("lon")
        if lat is None or lon is None:
            return None
        location = _reverse_geocode(lat, lon)
        if location:
            return location
        # Kein Geocoding-Ergebnis: keine Orts-Ordner erzeugen. Ein Fallback auf
        # rohe GPS-Koordinaten würde Ordner wie "gps/52.745078_9.616632" anlegen.
        logger.debug(
            f"Reverse-Geocoding lieferte kein Ergebnis für {path}; Datei wird ohne "
            f"Land/Stadt-Struktur abgelegt"
        )
        return None
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
                # Erzeuge einen aussagekräftigen Namen mit Geo-Kürzel
                timestamp = _get_file_timestamp(path)
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


def get_file_creation_date(path: Path) -> str:
    """Gibt das Erstellungsdatum der Datei im Format YYYY-MM-DD zurück.

    Verwendet `st_ctime` wenn verfügbar, sonst fällt auf das aktuelle Datum zurück.
    Diese Funktion ist öffentlich, damit andere Module (z. B. Router)
    konsistent das Datum aus den Metadaten beziehen können.

    Args:
            path: Pfad zur Datei.

    Returns:
            Datum als ISO-String `YYYY-MM-DD`.
    """
    try:
        ctime = path.stat().st_ctime
        return datetime.fromtimestamp(ctime).date().isoformat()
    except Exception as e:
        logger.debug(f"Konnte Erstelldatum nicht lesen für {path}: {e}")
        return datetime.now().date().isoformat()


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
    """Versucht, GPS-Koordinaten aus dem EXIF-Block eines Bildes zu lesen.

    Verwendet Pillow falls vorhanden; bei fehlendem Paket wird None zurückgegeben
    und eine Warnung geloggt.
    """
    try:
        from PIL import Image
        from PIL.ExifTags import GPSTAGS, TAGS
    except Exception:
        logger.debug("Pillow nicht verfügbar; GPS-Extraktion übersprungen")
        return None

    try:
        img = Image.open(path)
        exif = img._getexif()
        if not exif:
            return None

        gps_info = {}
        for tag, value in exif.items():
            decoded = TAGS.get(tag, tag)
            if decoded == "GPSInfo":
                for t in value:
                    sub_decoded = GPSTAGS.get(t, t)
                    gps_info[sub_decoded] = value[t]

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

        return {"lat": lat, "lon": lon}

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
