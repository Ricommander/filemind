from pathlib import Path

from filemind.core.models import FileType
from filemind.routing import router


def test_route_file_text_document_calls_paperless_and_storage(monkeypatch: "pytest.MonkeyPatch", tmp_path: Path) -> None:
    document_path = tmp_path / "report.pdf"
    document_path.write_bytes(b"document content")

    called = {"dedup": False, "paperless": False, "storage": False}

    def fake_dedup(file_info):
        called["dedup"] = True
        return False

    def fake_paperless(file_info, file_type):
        assert file_type == FileType.TEXT_DOCUMENT
        called["paperless"] = True
        return True

    def fake_storage(file_info, file_type):
        assert file_type == FileType.TEXT_DOCUMENT
        called["storage"] = True
        return True

    monkeypatch.setattr(router, "_handle_deduplication", fake_dedup)
    monkeypatch.setattr(router, "_handle_paperless_upload", fake_paperless)
    monkeypatch.setattr(router, "_handle_storage", fake_storage)

    result = router.route_file(document_path)

    assert result is True
    assert called["dedup"]
    assert called["paperless"]
    assert called["storage"]


def test_route_file_real_image_calls_ai_and_storage(monkeypatch: "pytest.MonkeyPatch", tmp_path: Path) -> None:
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
