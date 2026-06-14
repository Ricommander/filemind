import datetime
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from filemind.config import reload_config, set_config_file
from filemind.integrations.metadata_extractor import (
    _extract_image_capture_datetime,
    _reverse_geocode,
    get_country_city_from_file,
    get_file_creation_date,
)

# Beispielbild mit gesetztem EXIF-Aufnahmedatum (DateTimeOriginal 2026:05:27).
SAMPLE_IMAGE = Path(__file__).parent / "input" / "20260527_114047.jpg"
SAMPLE_IMAGE_EXIF_DATE = "2026-05-27"


def test_image_creation_date_uses_exif_not_filesystem(tmp_path):
    """Regression: Das EXIF-Aufnahmedatum hat Vorrang vor den Datei-Zeitstempeln.
    Ein abweichend gesetzter mtime darf den Dateinamen nicht verfälschen.
    """
    pytest.importorskip("PIL")

    img = tmp_path / "kopie.jpg"
    shutil.copy2(SAMPLE_IMAGE, img)

    # Datei-Zeitstempel bewusst auf ein ganz anderes Datum legen: Da ein gültiges
    # Aufnahmedatum vorliegt, wird der mtime ignoriert.
    far_off = datetime.datetime(2030, 1, 1, 12, 0, 0).timestamp()
    os.utime(img, (far_off, far_off))

    assert get_file_creation_date(img) == SAMPLE_IMAGE_EXIF_DATE


def test_extract_image_capture_datetime_reads_exif(tmp_path):
    """`_extract_image_capture_datetime` liefert den EXIF-Aufnahmezeitpunkt."""
    pytest.importorskip("PIL")

    img = tmp_path / "kopie.jpg"
    shutil.copy2(SAMPLE_IMAGE, img)

    captured = _extract_image_capture_datetime(img)
    assert captured is not None
    assert captured.date().isoformat() == SAMPLE_IMAGE_EXIF_DATE


def test_extract_image_capture_datetime_none_for_non_image(tmp_path):
    """Ohne EXIF-Bild gibt es keinen Aufnahmezeitpunkt -> None (Fallback greift)."""
    p = tmp_path / "notiz.txt"
    p.write_text("kein bild")

    assert _extract_image_capture_datetime(p) is None


def test_get_file_creation_date_from_stat(monkeypatch, tmp_path):
    """Ohne EXIF (Datei ist kein Bild) und ohne ``st_mtime`` im Mock bleiben nur
    das Erstelldatum (``st_ctime``, 2021) und das aktuelle Datum gültig - das
    ältere (ctime) gewinnt."""
    p = tmp_path / "sample.txt"
    p.write_text("hello")

    # Use a fixed timestamp (2021-01-01 00:00:00 local)
    ts = 1609459200

    def fake_stat(self):
        return SimpleNamespace(st_ctime=ts)

    monkeypatch.setattr("pathlib.Path.stat", fake_stat)

    expected = datetime.datetime.fromtimestamp(ts).date().isoformat()
    assert get_file_creation_date(p) == expected


def test_get_file_creation_date_prefers_capture_date(monkeypatch, tmp_path):
    """Neue Logik: Ein gültiges EXIF-Aufnahmedatum hat Vorrang und wird verwendet -
    selbst wenn andere Datumsquellen (mtime/Erstelldatum) älter sind. Das schützt
    vor künstlich alten Datei-Zeitstempeln."""
    p = tmp_path / "bild.jpg"
    p.write_bytes(b"fake")

    capture = datetime.datetime(2023, 7, 1, 12, 0, 0)  # Aufnahme
    modified = datetime.datetime(2018, 2, 2, 9, 0, 0)  # älter, aber irrelevant
    created = datetime.datetime(2020, 5, 5, 8, 0, 0)

    monkeypatch.setattr(
        "filemind.integrations.metadata_extractor._extract_image_capture_datetime",
        lambda path: capture,
    )
    monkeypatch.setattr(
        "filemind.integrations.metadata_extractor._get_file_modification_datetime",
        lambda path: modified,
    )
    monkeypatch.setattr(
        "filemind.integrations.metadata_extractor._get_file_creation_datetime",
        lambda path: created,
    )

    # Aufnahmedatum gewinnt, obwohl mtime (2018) älter ist.
    assert get_file_creation_date(p) == capture.date().isoformat()


def test_get_file_creation_date_oldest_when_no_capture(monkeypatch, tmp_path):
    """Ohne EXIF-Aufnahmedatum wird das älteste der übrigen gültigen Daten
    (Änderungs-/Erstelldatum, aktuelles Datum) gewählt."""
    p = tmp_path / "ohne_exif.bin"
    p.write_bytes(b"x")

    modified = datetime.datetime(2018, 2, 2, 9, 0, 0)  # am ältesten
    created = datetime.datetime(2020, 5, 5, 8, 0, 0)

    monkeypatch.setattr(
        "filemind.integrations.metadata_extractor._extract_image_capture_datetime",
        lambda path: None,
    )
    monkeypatch.setattr(
        "filemind.integrations.metadata_extractor._get_file_modification_datetime",
        lambda path: modified,
    )
    monkeypatch.setattr(
        "filemind.integrations.metadata_extractor._get_file_creation_datetime",
        lambda path: created,
    )

    # Kein Aufnahmedatum -> ältestes der übrigen (modified, 2018) gewinnt.
    assert get_file_creation_date(p) == modified.date().isoformat()


def test_get_file_creation_date_ignores_missing_sources(monkeypatch, tmp_path):
    """Fehlende Quellen (None) werden ignoriert; vom verbleibenden Rest gilt das
    älteste gültige Datum."""
    p = tmp_path / "datei.bin"
    p.write_bytes(b"x")

    created = datetime.datetime(2017, 9, 9, 10, 0, 0)

    monkeypatch.setattr(
        "filemind.integrations.metadata_extractor._extract_image_capture_datetime",
        lambda path: None,
    )
    monkeypatch.setattr(
        "filemind.integrations.metadata_extractor._get_file_modification_datetime",
        lambda path: None,
    )
    monkeypatch.setattr(
        "filemind.integrations.metadata_extractor._get_file_creation_datetime",
        lambda path: created,
    )

    # Nur Erstelldatum (2017) und aktuelles Datum sind gültig -> 2017 ist älter.
    assert get_file_creation_date(p) == created.date().isoformat()


def test_get_file_creation_date_creation_prefers_birthtime(monkeypatch, tmp_path):
    """Das Erstelldatum bevorzugt ``st_birthtime`` gegenüber ``st_ctime``. Im Mock
    fehlt ``st_mtime``; birthtime (2019) ist zugleich das älteste gültige Datum."""
    p = tmp_path / "ohne_exif.txt"
    p.write_text("kein bild")

    birthtime = datetime.datetime(2019, 3, 3, 8, 0, 0).timestamp()
    ctime = datetime.datetime(2021, 4, 4, 9, 0, 0).timestamp()

    def fake_stat(self):
        return SimpleNamespace(st_birthtime=birthtime, st_ctime=ctime)

    monkeypatch.setattr("pathlib.Path.stat", fake_stat)

    expected = datetime.datetime.fromtimestamp(birthtime).date().isoformat()
    assert get_file_creation_date(p) == expected


def test_get_file_creation_date_fallback(monkeypatch, tmp_path):
    p = tmp_path / "sample2.txt"
    p.write_text("world")

    def fake_stat_raise(self):
        raise OSError("stat failed")

    monkeypatch.setattr("pathlib.Path.stat", fake_stat_raise)

    expected = datetime.date.today().isoformat()
    assert get_file_creation_date(p) == expected


def _set_language(tmp_path, language: str) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(f'language: "{language}"\n', encoding="utf-8")
    set_config_file(config_path)
    reload_config()


@pytest.mark.parametrize(
    ("language", "country"),
    [("de", "Deutschland"), ("en", "Germany")],
)
def test_reverse_geocode_uses_configured_language(monkeypatch, tmp_path, language, country):
    _set_language(tmp_path, language)

    captured = {}

    class FakeLocation:
        raw = {"address": {"country": country, "city": "Hodenhagen"}}

    class FakeNominatim:
        def __init__(self, user_agent):
            captured["user_agent"] = user_agent

        def reverse(self, coords, language, timeout):
            captured["language"] = language
            return FakeLocation()

    monkeypatch.setattr("geopy.geocoders.Nominatim", FakeNominatim)

    result = _reverse_geocode(52.76, 9.59)

    assert captured["language"] == language
    assert result == {"country": country, "city": "Hodenhagen"}


def test_get_country_city_returns_real_names(monkeypatch, tmp_path):
    p = tmp_path / "foto.jpg"
    p.write_bytes(b"fake image")

    monkeypatch.setattr(
        "filemind.integrations.metadata_extractor._extract_gps_from_image",
        lambda path: {"lat": 52.745078, "lon": 9.616632},
    )
    monkeypatch.setattr(
        "filemind.integrations.metadata_extractor._reverse_geocode",
        lambda lat, lon: {"country": "Deutschland", "city": "Hodenhagen"},
    )

    assert get_country_city_from_file(p) == {"country": "Deutschland", "city": "Hodenhagen"}


def test_get_country_city_returns_none_when_geocoding_fails(monkeypatch, tmp_path):
    """Regression: Schlägt Reverse-Geocoding fehl, darf es keinen
    gps/<Koordinaten>-Fallback geben - es muss None zurückkommen, damit die
    Datei direkt im Jahresordner landet."""
    p = tmp_path / "foto.jpg"
    p.write_bytes(b"fake image")

    monkeypatch.setattr(
        "filemind.integrations.metadata_extractor._extract_gps_from_image",
        lambda path: {"lat": 52.745078, "lon": 9.616632},
    )
    monkeypatch.setattr(
        "filemind.integrations.metadata_extractor._reverse_geocode",
        lambda lat, lon: None,
    )

    assert get_country_city_from_file(p) is None
