import logging
from pathlib import Path

import pytest

from filemind.core.models import FileType
from filemind.routing import router


def test_route_file_text_document_calls_storage(
    monkeypatch: "pytest.MonkeyPatch", tmp_path: Path
) -> None:
    document_path = tmp_path / "report.pdf"
    document_path.write_bytes(b"document content")

    called = {"dedup": False, "storage": False}

    def fake_dedup(file_info):
        called["dedup"] = True
        return False

    def fake_storage(file_info, file_type):
        assert file_type == FileType.TEXT_DOCUMENT
        called["storage"] = True
        return True

    monkeypatch.setattr(router, "_handle_deduplication", fake_dedup)
    monkeypatch.setattr(router, "_handle_storage", fake_storage)

    result = router.route_file(document_path)

    assert result is True
    assert called["dedup"]
    assert called["storage"]


def test_route_file_real_image_calls_ai_and_storage(
    monkeypatch: "pytest.MonkeyPatch", tmp_path: Path
) -> None:
    image_path = tmp_path / "photo.jpg"
    image_path.write_bytes(b"image content")

    called = {"dedup": False, "ai": False, "storage": False}

    def fake_dedup(file_info):
        called["dedup"] = True
        return False

    def fake_ai_naming(file_info, file_type):
        assert file_type == FileType.REAL_IMAGE
        called["ai"] = True
        return "photo_renamed.jpg"

    def fake_storage(file_info, file_type):
        assert file_type == FileType.REAL_IMAGE
        called["storage"] = True
        return True

    monkeypatch.setattr(router, "_handle_deduplication", fake_dedup)
    monkeypatch.setattr(router, "_handle_ai_naming", fake_ai_naming)
    monkeypatch.setattr(router, "_handle_storage", fake_storage)

    result = router.route_file(image_path)

    assert result is True
    assert called["dedup"]
    assert called["ai"]
    assert called["storage"]


def test_handle_storage_uses_configured_max_files_per_folder(
    monkeypatch: "pytest.MonkeyPatch", tmp_path: Path
) -> None:
    source_file = tmp_path / "sample.mp3"
    source_file.write_bytes(b"music content")

    base_media = tmp_path / "media"
    existing_year = base_media / "2026"
    existing_year.mkdir(parents=True)
    (existing_year / "existing.mp3").write_bytes(b"existing")

    monkeypatch.setattr(
        router,
        "get_config",
        lambda: {
            "storage": {
                "base_media_path": str(base_media),
                "base_documents_path": str(tmp_path / "docs"),
                "max_files_per_folder": 1,
            }
        },
    )

    monkeypatch.setattr("filemind.storage.hash_store.compute_sha256", lambda path: "dummyhash")
    monkeypatch.setattr(
        "filemind.storage.hash_store.is_hash_in_directory", lambda file_hash, path: False
    )
    monkeypatch.setattr("filemind.storage.hash_store.register_file", lambda path: None)
    monkeypatch.setattr(
        "filemind.integrations.metadata_extractor.get_file_creation_date", lambda path: "2026-05-01"
    )
    monkeypatch.setattr(
        "filemind.integrations.metadata_extractor.get_country_city_from_file", lambda path: None
    )
    monkeypatch.setattr(
        "filemind.integrations.metadata_extractor.extract_metadata_name", lambda path: "sample"
    )

    class FileInfo:
        pass

    file_info = FileInfo()
    file_info.path = source_file

    result = router._handle_storage(file_info, router.FileType.AUDIO)

    assert result is True
    assert (base_media / "2026_1").exists()
    assert any((base_media / "2026_1").iterdir())


def test_handle_storage_ignores_subfolder_files_for_year_capacity(
    monkeypatch: "pytest.MonkeyPatch", tmp_path: Path
) -> None:
    """Das Limit zählt nur direkt im Ordner liegende Dateien: Dateien in
    Land/Stadt-Unterordnern dürfen den Jahresordner nicht "voll" machen."""
    source_file = tmp_path / "sample.mp3"
    source_file.write_bytes(b"music content")

    base_media = tmp_path / "media"
    city_dir = base_media / "2026" / "Deutschland" / "Hodenhagen"
    city_dir.mkdir(parents=True)
    (city_dir / "foto.jpg").write_bytes(b"existing photo")

    monkeypatch.setattr(
        router,
        "get_config",
        lambda: {
            "storage": {
                "base_media_path": str(base_media),
                "base_documents_path": str(tmp_path / "docs"),
                "max_files_per_folder": 1,
            }
        },
    )

    monkeypatch.setattr("filemind.storage.hash_store.compute_sha256", lambda path: "dummyhash")
    monkeypatch.setattr(
        "filemind.storage.hash_store.is_hash_in_directory", lambda file_hash, path: False
    )
    monkeypatch.setattr("filemind.storage.hash_store.register_file", lambda path: None)
    monkeypatch.setattr(
        "filemind.integrations.metadata_extractor.get_file_creation_date", lambda path: "2026-05-01"
    )
    monkeypatch.setattr(
        "filemind.integrations.metadata_extractor.get_country_city_from_file", lambda path: None
    )
    monkeypatch.setattr(
        "filemind.integrations.metadata_extractor.extract_metadata_name", lambda path: "sample"
    )

    class FileInfo:
        pass

    file_info = FileInfo()
    file_info.path = source_file

    result = router._handle_storage(file_info, router.FileType.AUDIO)

    assert result is True
    # Jahresordner hat 0 direkte Dateien -> kein Überlauf nach 2026_1
    assert not (base_media / "2026_1").exists()
    # Der Stamm wird in PascalCase geschrieben ("sample" -> "Sample")
    assert (base_media / "2026" / "2026-05-01_Sample.mp3").exists()


def test_handle_storage_full_city_folder_rolls_over_to_next_year_folder(
    monkeypatch: "pytest.MonkeyPatch", tmp_path: Path
) -> None:
    """Das Limit gilt für jeden Zielordner: Ist der Stadt-Ordner voll, weicht
    die Ablage auf den nächsten Jahres-Suffix-Ordner aus (2026_1/...)."""
    source_file = tmp_path / "photo.jpg"
    source_file.write_bytes(b"photo content")

    base_media = tmp_path / "media"
    city_dir = base_media / "2026" / "Deutschland" / "Hodenhagen"
    city_dir.mkdir(parents=True)
    (city_dir / "existing.jpg").write_bytes(b"existing photo")

    monkeypatch.setattr(
        router,
        "get_config",
        lambda: {
            "storage": {
                "base_media_path": str(base_media),
                "base_documents_path": str(tmp_path / "docs"),
                "max_files_per_folder": 1,
            }
        },
    )

    monkeypatch.setattr("filemind.storage.hash_store.compute_sha256", lambda path: "dummyhash")
    monkeypatch.setattr(
        "filemind.storage.hash_store.is_hash_in_directory", lambda file_hash, path: False
    )
    monkeypatch.setattr("filemind.storage.hash_store.register_file", lambda path: None)
    monkeypatch.setattr(
        "filemind.integrations.metadata_extractor.get_file_creation_date", lambda path: "2026-05-01"
    )
    monkeypatch.setattr(
        "filemind.integrations.metadata_extractor.get_country_city_from_file",
        lambda path: {"country": "Deutschland", "city": "Hodenhagen"},
    )

    class FileInfo:
        pass

    file_info = FileInfo()
    file_info.path = source_file

    result = router._handle_storage(
        file_info, router.FileType.REAL_IMAGE, action="move", ai_name="nashorn_wiese"
    )

    assert result is True
    # Der Stamm wird in PascalCase geschrieben ("nashorn_wiese" -> "Nashorn_Wiese")
    target = base_media / "2026_1" / "Deutschland" / "Hodenhagen" / "2026-05-01_Nashorn_Wiese.jpg"
    assert target.exists()
    # Der volle Stadt-Ordner wurde nicht weiter befüllt
    assert [p.name for p in city_dir.iterdir()] == ["existing.jpg"]


def test_handle_storage_moves_text_document(
    monkeypatch: "pytest.MonkeyPatch", tmp_path: Path
) -> None:
    source_file = tmp_path / "document.odt"
    source_file.write_bytes(b"doc content")

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

    result = router._handle_storage(file_info, router.FileType.TEXT_DOCUMENT)

    assert result is True
    assert not source_file.exists()
    assert any(docs_dir.iterdir())


def test_handle_ai_naming_renames_real_image_when_enabled(
    monkeypatch: "pytest.MonkeyPatch", tmp_path: Path
) -> None:
    image_path = tmp_path / "photo.jpg"
    image_path.write_bytes(b"image content")

    monkeypatch.setattr(router, "get_config", lambda: {"ai": {"enabled": True}})
    monkeypatch.setattr(
        "filemind.integrations.ai_naming.generate_smart_name", lambda path: "photo_renamed.jpg"
    )

    class FileInfo:
        pass

    file_info = FileInfo()
    file_info.path = image_path

    result = router._handle_ai_naming(file_info, router.FileType.REAL_IMAGE)

    # New behavior: AI naming returns a suggested stem and does NOT rename the source file
    assert result == "photo_renamed"
    assert image_path.exists()


def test_handle_ai_naming_does_not_truncate_mid_word(
    monkeypatch: "pytest.MonkeyPatch", tmp_path: Path
) -> None:
    """Regression: Vorschläge über 30 Zeichen wurden mitten im Wort abgeschnitten,
    obwohl der Prompt bis 40 Zeichen erlaubt und keine Pfadgrenze das erfordert."""
    image_path = tmp_path / "photo.jpg"
    image_path.write_bytes(b"image content")

    suggestion = "zwei_rhinozerosse_grasen_gruene_wiese"  # 37 Zeichen

    monkeypatch.setattr(router, "get_config", lambda: {"ai": {"enabled": True}})
    monkeypatch.setattr(
        "filemind.integrations.ai_naming.generate_smart_name", lambda path: suggestion
    )

    class FileInfo:
        pass

    file_info = FileInfo()
    file_info.path = image_path

    result = router._handle_ai_naming(file_info, router.FileType.REAL_IMAGE)

    assert result == suggestion


def test_handle_deduplication_only_checks_duplicate_without_registering(
    monkeypatch: "pytest.MonkeyPatch", tmp_path: Path
) -> None:
    file_path = tmp_path / "sample.txt"
    file_path.write_bytes(b"sample")

    called = {"registered": False}

    def fake_is_duplicate(path):
        assert path == file_path
        return False

    def fake_register_file(path):
        called["registered"] = True

    monkeypatch.setattr("filemind.storage.hash_store.is_duplicate", fake_is_duplicate)
    monkeypatch.setattr("filemind.storage.hash_store.register_file", fake_register_file)

    class FileInfo:
        pass

    file_info = FileInfo()
    file_info.path = file_path

    result = router._handle_deduplication(file_info)

    assert result is False
    assert not called["registered"]


def test_handle_deduplication_detects_duplicate(
    monkeypatch: "pytest.MonkeyPatch", tmp_path: Path
) -> None:
    file_path = tmp_path / "duplicate.txt"
    file_path.write_bytes(b"duplicate")

    called = {"registered": False}

    def fake_is_duplicate(path):
        assert path == file_path
        return True

    def fake_register_file(path):
        called["registered"] = True

    monkeypatch.setattr("filemind.storage.hash_store.is_duplicate", fake_is_duplicate)
    monkeypatch.setattr("filemind.storage.hash_store.register_file", fake_register_file)

    class FileInfo:
        pass

    file_info = FileInfo()
    file_info.path = file_path

    result = router._handle_deduplication(file_info)

    assert result is True
    assert not called["registered"]


def test_route_file_logs_and_hashes_duplicate_only_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Ein liegengebliebenes Duplikat darf nur beim ersten Durchlauf gemeldet werden."""
    file_path = tmp_path / "dup.txt"
    file_path.write_bytes(b"duplicate content")

    calls = {"count": 0}

    def fake_is_duplicate(path):
        calls["count"] += 1
        return True

    monkeypatch.setattr("filemind.storage.hash_store.is_duplicate", fake_is_duplicate)

    with caplog.at_level(logging.DEBUG, logger="filemind.routing.router"):
        # Drei Poll-Durchläufe des Daemons simulieren
        assert router.route_file(file_path) is False
        assert router.route_file(file_path) is False
        assert router.route_file(file_path) is False

    duplicate_warnings = [
        r for r in caplog.records if r.levelno == logging.WARNING and "Duplikat" in r.getMessage()
    ]
    assert len(duplicate_warnings) == 1
    # Folgedurchläufe hashen nicht erneut
    assert calls["count"] == 1


def test_route_file_rechecks_duplicate_after_file_change(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Ändert sich die Datei, muss erneut geprüft und ggf. gemeldet werden."""
    file_path = tmp_path / "dup.txt"
    file_path.write_bytes(b"duplicate content")

    calls = {"count": 0}

    def fake_is_duplicate(path):
        calls["count"] += 1
        return True

    monkeypatch.setattr("filemind.storage.hash_store.is_duplicate", fake_is_duplicate)

    with caplog.at_level(logging.WARNING, logger="filemind.routing.router"):
        assert router.route_file(file_path) is False
        # Datei verändert sich (andere Größe/mtime) -> erneute Prüfung nötig
        file_path.write_bytes(b"changed content with different size")
        assert router.route_file(file_path) is False

    duplicate_warnings = [
        r for r in caplog.records if r.levelno == logging.WARNING and "Duplikat" in r.getMessage()
    ]
    assert len(duplicate_warnings) == 2
    assert calls["count"] == 2
