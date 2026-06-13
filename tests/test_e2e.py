"""End-to-End-Softwaretest für filemind.

Dieser Test verarbeitet die echten Beispieldateien aus ``tests/input``
über die komplette Pipeline (Klassifizierung -> Deduplizierung ->
AI-Naming -> Storage) und prüft das Ergebnis im Zielverzeichnis.

Externe Abhängigkeiten (Ollama, OCR, Reverse-Geocoding) werden an der
Netzwerk-Grenze gemockt, damit der Test offline und deterministisch läuft.
"""

from __future__ import annotations

import logging
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import pytest

import filemind.storage.hash_store as hash_store_module
from filemind.config import reload_config, set_config_file
from filemind.routing.router import route_file

REPO_INPUT_DIR = Path(__file__).parent / "input"

EXPECTED_DOCUMENTS = {"23948OIKSJD.txt", "29084904SDSAD.odt", "29084904SDSAD.pdf"}
EXPECTED_IMAGE = "20260527_114047.jpg"
EXPECTED_AUDIO = "aud20200326wa0011.aac"

# Das Foto trägt im EXIF-Block DateTimeOriginal = 2026:05:27. Der Dateiname muss
# dieses Erstelldatum des Bildes verwenden - nicht das (Kopier-)Datum im Dateisystem.
EXPECTED_IMAGE_DATE = "2026-05-27"
EXPECTED_IMAGE_YEAR = EXPECTED_IMAGE_DATE.split("-")[0]


@pytest.fixture(autouse=True)
def _no_ocr(monkeypatch: pytest.MonkeyPatch):
    """Verhindert echtes OCR (EasyOCR/torch) im Test."""
    monkeypatch.setattr(
        "filemind.classification.classifier._extract_text_from_image",
        lambda path: "",
    )


@pytest.fixture(autouse=True)
def _no_geocoding(monkeypatch: pytest.MonkeyPatch):
    """Mockt Reverse-Geocoding an der Netzwerk-Grenze (Nominatim)."""
    monkeypatch.setattr(
        "filemind.integrations.metadata_extractor._reverse_geocode",
        lambda lat, lon: {"country": "Germany", "city": "Hodenhagen"},
    )


@pytest.fixture(autouse=True)
def _fake_ollama(monkeypatch: pytest.MonkeyPatch):
    """Mockt das Ollama-Modell: Beschreibung + Namensvorschlag."""

    def fake_generate(
        prompt: str,
        images: Optional[List[str]] = None,
        num_predict: int = 128,
        temperature: float = 0.2,
    ) -> str:
        if images:
            return "A brown dog playing in a park."
        return "dog_playing_park"

    monkeypatch.setattr(
        "filemind.integrations.ai_naming._ollama_generate",
        fake_generate,
    )


@pytest.fixture
def e2e_env(tmp_path: Path):
    """Baut eine isolierte Umgebung: Input-, Media-, Docs-Ordner + Konfiguration."""
    input_dir = tmp_path / "input"
    media_dir = tmp_path / "media"
    docs_dir = tmp_path / "documents"
    input_dir.mkdir()

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
logging:
  log_dir: "{(tmp_path / 'logs').as_posix()}"
  level: "DEBUG"
  console_enabled: false

daemon:
  input_directories:
    - path: "{input_dir.as_posix()}"
      action: "move"
  poll_interval: 1

storage:
  base_media_path: "{media_dir.as_posix()}"
  base_documents_path: "{docs_dir.as_posix()}"
  max_files_per_folder: 3000
  hash_db_path: "{(tmp_path / 'hash_store.db').as_posix()}"

ai:
  enabled: true
  provider: "ollama"
  model: "llava:7b"
  url: "http://localhost:11434"
  timeout: 5
""",
        encoding="utf-8",
    )
    set_config_file(config_path)
    reload_config()

    # Frischen Hash-Store für diesen Test erzwingen
    old_store = hash_store_module._global_hash_store
    hash_store_module._global_hash_store = None

    yield {"input": input_dir, "media": media_dir, "docs": docs_dir}

    if hash_store_module._global_hash_store is not None:
        hash_store_module._global_hash_store.close()
    hash_store_module._global_hash_store = old_store


def _copy_test_files(input_dir: Path) -> List[Path]:
    copied = []
    for source in sorted(REPO_INPUT_DIR.iterdir()):
        if source.is_file():
            target = input_dir / source.name
            shutil.copy2(source, target)
            copied.append(target)
    return copied


def _all_files_recursive(root: Path) -> List[Path]:
    if not root.exists():
        return []
    return [p for p in root.rglob("*") if p.is_file()]


def _assert_only_valid_location_folders(media_dir: Path) -> None:
    """Stellt sicher, dass unterhalb des Media-Ordners nur Jahresordner und
    echte Orts-Namen (Land, Stadt) als Ordner vorkommen - niemals "gps" oder
    rohe GPS-Koordinaten wie "52.745078_9.616632"."""
    year_pattern = re.compile(r"^\d{4}(_\d+)?$")
    coordinate_pattern = re.compile(r"-?\d+\.\d+")
    for folder in (p for p in media_dir.rglob("*") if p.is_dir()):
        name = folder.name
        assert name.lower() != "gps", f"Ungültiger Ordner 'gps' gefunden: {folder}"
        assert not coordinate_pattern.search(name), f"Ordner mit GPS-Koordinaten gefunden: {folder}"
        assert year_pattern.match(name) or re.match(
            r"^[^\d]+$", name
        ), f"Ordnername ist weder Jahr noch Land/Stadt: {folder}"


def test_full_pipeline_processes_all_input_files(e2e_env: dict) -> None:
    input_dir = e2e_env["input"]
    media_dir = e2e_env["media"]
    docs_dir = e2e_env["docs"]

    files = _copy_test_files(input_dir)
    assert len(files) >= 5, "tests/input sollte mindestens 5 Beispieldateien enthalten"

    for file_path in files:
        assert route_file(file_path, action="move") is True, f"Routing fehlgeschlagen: {file_path}"

    # Alle Dateien wurden aus dem Input-Ordner verschoben
    assert _all_files_recursive(input_dir) == []

    # Dokumente liegen flach im Dokumenten-Ziel
    doc_names = {p.name for p in _all_files_recursive(docs_dir)}
    assert doc_names == EXPECTED_DOCUMENTS

    today = datetime.now().date().isoformat()
    audio_year = today.split("-")[0]

    # Foto: AI-Name + GPS-basierte Country/City-Struktur. Das Datum stammt aus
    # dem EXIF-Aufnahmedatum (Erstelldatum des Bildes), nicht aus dem Dateisystem.
    image_targets = [p for p in _all_files_recursive(media_dir) if p.suffix == ".jpg"]
    assert len(image_targets) == 1
    image_target = image_targets[0]
    # Der Stamm wird in PascalCase geschrieben ("dog_playing_park" -> "Dog_Playing_Park")
    assert image_target.name == f"{EXPECTED_IMAGE_DATE}_Dog_Playing_Park.jpg"
    assert image_target.parent == media_dir / EXPECTED_IMAGE_YEAR / "Germany" / "Hodenhagen"
    _assert_only_valid_location_folders(media_dir)

    # Audio: Metadaten-basierter Name direkt im Jahresordner. Ohne EXIF greift
    # für das Datum der Datei-Zeitstempel (hier das Kopierdatum = heute). Auch
    # der Metadaten-Stamm ist PascalCase ("audio_..." -> "Audio...").
    audio_targets = [p for p in _all_files_recursive(media_dir) if p.suffix == ".aac"]
    assert len(audio_targets) == 1
    audio_target = audio_targets[0]
    assert audio_target.name.startswith(f"{today}_Audio")
    assert audio_target.parent == media_dir / audio_year

    # Inhalts-Garantie: Die Pipeline benennt nur um/verschiebt - die Dateien
    # selbst (insb. das Foto trotz AI-Downscaling) bleiben bit-identisch
    assert image_target.read_bytes() == (REPO_INPUT_DIR / EXPECTED_IMAGE).read_bytes()
    assert audio_target.read_bytes() == (REPO_INPUT_DIR / EXPECTED_AUDIO).read_bytes()


def test_photo_without_geocoding_creates_no_gps_coordinate_folders(
    e2e_env: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: Schlägt Reverse-Geocoding fehl (z. B. offline), darf kein
    Ordner "gps/<Koordinaten>" entstehen - das Foto muss direkt im
    Jahresordner landen und alle Ordnernamen müssen gültig bleiben."""
    monkeypatch.setattr(
        "filemind.integrations.metadata_extractor._reverse_geocode",
        lambda lat, lon: None,
    )

    input_dir = e2e_env["input"]
    media_dir = e2e_env["media"]

    source = REPO_INPUT_DIR / EXPECTED_IMAGE
    target = input_dir / source.name
    shutil.copy2(source, target)

    assert route_file(target, action="move") is True

    image_targets = [p for p in _all_files_recursive(media_dir) if p.suffix == ".jpg"]
    assert len(image_targets) == 1

    # Ohne Land/Stadt: direkt im Jahresordner (Jahr aus dem EXIF-Aufnahmedatum),
    # keine gps-/Koordinaten-Ordner
    assert image_targets[0].parent == media_dir / EXPECTED_IMAGE_YEAR
    _assert_only_valid_location_folders(media_dir)


def test_full_pipeline_detects_duplicates_on_second_run(
    e2e_env: dict, caplog: pytest.LogCaptureFixture
) -> None:
    input_dir = e2e_env["input"]
    media_dir = e2e_env["media"]
    docs_dir = e2e_env["docs"]

    # Erster Durchlauf
    for file_path in _copy_test_files(input_dir):
        assert route_file(file_path, action="move") is True

    first_run_targets = sorted(
        p.relative_to(media_dir) for p in _all_files_recursive(media_dir)
    ) + sorted(p.relative_to(docs_dir) for p in _all_files_recursive(docs_dir))

    # Zweiter Durchlauf mit identischen Dateien -> alles Duplikate
    for file_path in _copy_test_files(input_dir):
        assert route_file(file_path, action="move") is False, f"Duplikat nicht erkannt: {file_path}"

    second_run_targets = sorted(
        p.relative_to(media_dir) for p in _all_files_recursive(media_dir)
    ) + sorted(p.relative_to(docs_dir) for p in _all_files_recursive(docs_dir))

    # Zielstruktur unverändert, keine Duplikate angelegt
    assert second_run_targets == first_run_targets

    # Dritter Durchlauf (weiterer Poll-Zyklus des Daemons): bereits gemeldete
    # Duplikate werden still übersprungen und nicht erneut geloggt
    caplog.clear()
    with caplog.at_level(logging.INFO):
        for file_path in _copy_test_files(input_dir):
            assert route_file(file_path, action="move") is False

    repeated_mentions = [r for r in caplog.records if "Duplikat" in r.getMessage()]
    assert repeated_mentions == []
