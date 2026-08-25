"""Edge-Case- und Robustheitstests für filemind.

Ergänzt die bestehenden Modultests um Grenzfälle, die beim Review der
Test-Suite als Lücken identifiziert wurden: unbekannte/fehlende Endungen,
leere und fehlende Dateien, kaputte/ungültige Konfiguration sowie
Router-Verhalten für bisher ungetestete Dateitypen und Aktionen.

Ein Teil der Tests nutzt echte, öffentlich freigegebene Beispieldateien aus
``tests/fixtures/real_world`` (Fotos und Textdateien der Library of Congress
"Free to Use and Reuse Sets", gemeinfrei) anstelle synthetischer Dummy-Bytes,
um realistischere Klassifizierungs- und Hashing-Pfade abzudecken.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from filemind.classification.classifier import classify_file
from filemind.config import get_section, load_config, reload_config, set_config_file
from filemind.core.models import FileType
from filemind.routing import router
from filemind.routing.router import route_file
from filemind.storage.hash_store import HashStore

REAL_WORLD_DIR = Path(__file__).parent / "fixtures" / "real_world"
REAL_PHOTO_1 = REAL_WORLD_DIR / "real_photo_01.jpg"
REAL_PHOTO_2 = REAL_WORLD_DIR / "real_photo_02.jpg"
REAL_README = REAL_WORLD_DIR / "real_readme.txt"
MISMATCHED_PDF = REAL_WORLD_DIR / "mismatched_extension.pdf"  # Inhalt ist Markdown-Text
MISMATCHED_JSON = REAL_WORLD_DIR / "mismatched_extension.json"  # Inhalt ist ein JPEG


# ---------------------------------------------------------------------------
# Classifier: unbekannte/fehlende Endungen, leere Dateien, echte Fotos
# ---------------------------------------------------------------------------


def test_classify_unknown_extension_is_other(tmp_path: Path) -> None:
    path = tmp_path / "notes.xyz"
    path.write_bytes(b"some content")

    result = classify_file(path)

    assert result.file_type == FileType.OTHER
    assert result.confidence == pytest.approx(0.5)


def test_classify_file_without_extension_is_other(tmp_path: Path) -> None:
    path = tmp_path / "README"
    path.write_bytes(b"no extension here")

    result = classify_file(path)

    assert result.file_type == FileType.OTHER


def test_classify_uppercase_extension_is_recognized(tmp_path: Path) -> None:
    path = tmp_path / "REPORT.PDF"
    path.write_bytes(b"dummy")

    result = classify_file(path)

    assert result.file_type == FileType.TEXT_DOCUMENT


def test_classify_missing_file_raises_file_not_found(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.jpg"

    with pytest.raises(FileNotFoundError):
        classify_file(missing)


def test_classify_zero_byte_document_uses_extension(tmp_path: Path) -> None:
    path = tmp_path / "empty.pdf"
    path.write_bytes(b"")

    result = classify_file(path)

    assert result.file_type == FileType.TEXT_DOCUMENT
    assert result.file_info.size == 0


def test_classify_zero_byte_image_falls_back_to_real_image(tmp_path: Path) -> None:
    """Ein 0-Byte-Bild kann nicht per OCR/Farbanalyse untersucht werden und
    muss trotzdem robust (ohne Absturz) klassifiziert werden."""
    path = tmp_path / "empty.jpg"
    path.write_bytes(b"")

    result = classify_file(path)

    assert result.file_type == FileType.REAL_IMAGE


def test_classify_real_photo_without_exif_is_real_image() -> None:
    """Echtes, EXIF-loses Archivfoto (Library of Congress) muss als Foto
    erkannt werden, ohne dass OCR/Farbanalyse abstürzt."""
    assert REAL_PHOTO_1.exists(), "Real-world Fixture fehlt"

    result = classify_file(REAL_PHOTO_1)

    assert result.file_type == FileType.REAL_IMAGE
    assert result.confidence >= 0.5


def test_classify_real_text_document(tmp_path: Path) -> None:
    """Echte README-Textdatei statt Dummy-Bytes: mehrere Absätze, Markdown-Listen."""
    assert REAL_README.exists(), "Real-world Fixture fehlt"

    result = classify_file(REAL_README)

    assert result.file_type == FileType.TEXT_DOCUMENT
    assert result.confidence == pytest.approx(0.98)


def test_classify_mismatched_extension_trusts_extension_over_content(tmp_path: Path) -> None:
    """Dokumentiertes Verhalten: Die Klassifizierung prüft nur die Endung, nicht
    den tatsächlichen Dateiinhalt. Eine als ``.pdf`` benannte Datei, die in
    Wahrheit reiner Markdown-Text ist, wird dennoch als TEXT_DOCUMENT erkannt."""
    assert MISMATCHED_PDF.exists(), "Real-world Fixture fehlt"

    result = classify_file(MISMATCHED_PDF)

    assert result.file_type == FileType.TEXT_DOCUMENT
    assert result.confidence == pytest.approx(0.98)


def test_classify_mismatched_extension_unknown_is_other(tmp_path: Path) -> None:
    """Eine als ``.json`` benannte Datei, die in Wahrheit ein JPEG-Foto ist,
    landet als OTHER, weil ``.json`` in keiner konfigurierten Endungsliste ist."""
    assert MISMATCHED_JSON.exists(), "Real-world Fixture fehlt"

    result = classify_file(MISMATCHED_JSON)

    assert result.file_type == FileType.OTHER


# ---------------------------------------------------------------------------
# HashStore: leere Dateien, fehlende Dateien, echte (nicht-)Duplikate
# ---------------------------------------------------------------------------


def test_compute_sha256_of_empty_file(tmp_path: Path) -> None:
    import hashlib

    path = tmp_path / "empty.bin"
    path.write_bytes(b"")

    store = HashStore(db_path=tmp_path / "hash_store.db")
    try:
        assert store.compute_sha256(path) == hashlib.sha256(b"").hexdigest()
    finally:
        store.close()


def test_compute_sha256_missing_file_raises_value_error(tmp_path: Path) -> None:
    store = HashStore(db_path=tmp_path / "hash_store.db")
    try:
        with pytest.raises(ValueError):
            store.compute_sha256(tmp_path / "missing.jpg")
    finally:
        store.close()


def test_distinct_real_photos_are_not_duplicates(tmp_path: Path) -> None:
    store = HashStore(db_path=tmp_path / "hash_store.db")
    try:
        store.register_file(REAL_PHOTO_1)
        assert store.is_duplicate(REAL_PHOTO_2) is False
    finally:
        store.close()


def test_identical_content_under_different_name_is_duplicate(tmp_path: Path) -> None:
    """Kopiert man eine echte Datei unter neuem Namen, muss sie trotzdem als
    inhaltliches Duplikat erkannt werden (Hash ist namensunabhängig)."""
    copy_path = tmp_path / "renamed_copy.jpg"
    copy_path.write_bytes(REAL_PHOTO_1.read_bytes())

    store = HashStore(db_path=tmp_path / "hash_store.db")
    try:
        store.register_file(REAL_PHOTO_1)
        assert store.is_duplicate(copy_path) is True
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Config: fehlende Datei, ungültiges YAML, Reload
# ---------------------------------------------------------------------------


def test_load_config_missing_file_returns_empty_defaults(tmp_path: Path) -> None:
    set_config_file(tmp_path / "does_not_exist.yaml")

    config = load_config()

    assert config == {}
    assert get_section("storage", default={"fallback": True}) == {"fallback": True}


def test_load_config_invalid_yaml_falls_back_to_empty(tmp_path: Path) -> None:
    bad_config = tmp_path / "broken.yaml"
    bad_config.write_text("language: [unclosed list\nstorage: {", encoding="utf-8")

    set_config_file(bad_config)

    config = load_config()

    assert config == {}


def test_reload_config_picks_up_changed_file(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("language: de\n", encoding="utf-8")
    set_config_file(config_path)
    load_config()

    assert load_config().get("language") == "de"

    config_path.write_text("language: en\n", encoding="utf-8")
    reload_config()

    assert load_config().get("language") == "en"


# ---------------------------------------------------------------------------
# Router: unbekannte Typen, fehlende Dateien, copy-Aktion, Duplikat-Kurzschluss
# ---------------------------------------------------------------------------


def test_route_file_missing_path_returns_false(tmp_path: Path) -> None:
    missing = tmp_path / "ghost.jpg"

    assert route_file(missing) is False


def test_route_file_other_type_dispatches_to_storage(
    monkeypatch: "pytest.MonkeyPatch", tmp_path: Path
) -> None:
    unknown_file = tmp_path / "mystery.xyz"
    unknown_file.write_bytes(b"unknown content")

    called = {"storage_file_type": None}

    def fake_dedup(file_info):
        return False

    def fake_storage(file_info, file_type, action="move", ai_name=None):
        called["storage_file_type"] = file_type
        return True

    monkeypatch.setattr(router, "_handle_deduplication", fake_dedup)
    monkeypatch.setattr(router, "_handle_storage", fake_storage)

    result = route_file(unknown_file)

    assert result is True
    assert called["storage_file_type"] == FileType.OTHER


def test_route_file_skips_already_known_duplicate_without_reclassifying(
    monkeypatch: "pytest.MonkeyPatch", tmp_path: Path
) -> None:
    path = tmp_path / "known_dup.jpg"
    path.write_bytes(b"duplicate content")

    router._remember_duplicate(path)

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("classify_file sollte fuer bekannte Duplikate nicht aufgerufen werden")

    monkeypatch.setattr(router, "classify_file", fail_if_called)

    assert route_file(path) is False


def test_handle_storage_copy_action_preserves_source_file(
    monkeypatch: "pytest.MonkeyPatch", tmp_path: Path
) -> None:
    source_file = tmp_path / "document.txt"
    source_file.write_bytes(b"keep me around")

    docs_dir = tmp_path / "docs"

    monkeypatch.setattr(
        router,
        "get_config",
        lambda: {
            "storage": {
                "base_media_path": str(tmp_path / "media"),
                "base_documents_path": str(docs_dir),
                "max_files_per_folder": 3000,
            }
        },
    )
    monkeypatch.setattr("filemind.storage.hash_store.compute_sha256", lambda path: "dummyhash")
    monkeypatch.setattr(
        "filemind.storage.hash_store.is_hash_in_directory", lambda file_hash, path: False
    )
    monkeypatch.setattr("filemind.storage.hash_store.register_file", lambda path: None)

    class FileInfo:
        pass

    file_info = FileInfo()
    file_info.path = source_file

    result = router._handle_storage(file_info, FileType.TEXT_DOCUMENT, action="copy")

    assert result is True
    assert source_file.exists(), "Bei action='copy' muss die Quelldatei erhalten bleiben"
    assert any(docs_dir.iterdir())
