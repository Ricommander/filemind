from pathlib import Path

import pytest

from filemind.classification.classifier import classify_file, detect_text_in_image, guess_mime_type
from filemind.core.models import FileType


def test_guess_mime_type_returns_expected_type(tmp_path: Path) -> None:
    path = tmp_path / "photo.jpg"
    path.write_bytes(b"dummy")

    mime_type = guess_mime_type(path)

    assert mime_type == "image/jpeg"


def test_classify_real_image(tmp_path: Path) -> None:
    path = tmp_path / "photo.jpg"
    path.write_bytes(b"dummy")

    result = classify_file(path)

    assert result.file_type == FileType.REAL_IMAGE
    assert result.file_info.path == path
    assert result.confidence >= 0.5


def test_classify_document_image(tmp_path: Path) -> None:
    path = tmp_path / "scan.tiff"
    path.write_bytes(b"dummy")

    result = classify_file(path)

    assert result.file_type == FileType.DOCUMENT_IMAGE
    assert result.confidence == pytest.approx(0.75)


def test_classify_text_document(tmp_path: Path) -> None:
    path = tmp_path / "report.pdf"
    path.write_bytes(b"dummy")

    result = classify_file(path)

    assert result.file_type == FileType.TEXT_DOCUMENT
    assert result.confidence == pytest.approx(0.98)


def test_classify_odt_as_text_document(tmp_path: Path) -> None:
    path = tmp_path / "document.odt"
    path.write_bytes(b"dummy")

    result = classify_file(path)

    assert result.file_type == FileType.TEXT_DOCUMENT
    assert result.confidence == pytest.approx(0.98)


def test_classify_archive(tmp_path: Path) -> None:
    path = tmp_path / "archive.zip"
    path.write_bytes(b"dummy")

    result = classify_file(path)

    assert result.file_type == FileType.ARCHIVES
    assert result.confidence == pytest.approx(0.98)


def test_classify_file_uses_config_extensions(
    monkeypatch: "pytest.MonkeyPatch", tmp_path: Path
) -> None:
    path = tmp_path / "scan.TIFF"
    path.write_bytes(b"dummy")

    monkeypatch.setattr(
        "filemind.config.get_section",
        lambda section, default=None: {
            "image_extensions": [".TIFF"],
            "text_document_extensions": [".pdf"],
            "video_extensions": [".mp4"],
            "audio_extensions": [".mp3"],
            "archive_extensions": [".zip"],
        },
    )

    monkeypatch.setattr(
        "filemind.classification.classifier._extract_text_from_image",
        lambda path: "Invoice number 123 with several words",
    )

    result = classify_file(path)

    assert result.file_type == FileType.DOCUMENT_IMAGE
    assert result.confidence == pytest.approx(0.75)


def test_detect_text_in_image_uses_ocr(monkeypatch: "pytest.MonkeyPatch", tmp_path: Path) -> None:
    scan = tmp_path / "invoice_scan.jpg"
    scan.write_bytes(b"dummy")

    def fake_extract_text_from_image(path: Path) -> str:
        assert path == scan
        return "Invoice number 123"

    monkeypatch.setattr(
        "filemind.classification.classifier._extract_text_from_image", fake_extract_text_from_image
    )

    score = detect_text_in_image(scan)

    assert score == pytest.approx(30.0)


def test_detect_text_in_image_stub_behaviour(tmp_path: Path) -> None:
    scan = tmp_path / "invoice_scan.jpg"
    scan.write_bytes(b"dummy")

    score = detect_text_in_image(scan)

    assert score >= 20.0
    assert score <= 100.0


_TEXT_HEAVY = "Dies ist ein langer Brief mit sehr viel Text und vielen einzelnen Woertern darin"


def test_text_heavy_low_color_is_document(
    monkeypatch: "pytest.MonkeyPatch", tmp_path: Path
) -> None:
    img = tmp_path / "brief.jpg"
    img.write_bytes(b"dummy")

    # Schwellen explizit pinnen, damit der Test nicht von der jeweils geladenen
    # config.yaml abhängt (_TEXT_HEAVY hat 14 Wörter).
    monkeypatch.setattr(
        "filemind.classification.classifier._get_document_image_thresholds",
        lambda: (8, 18.0),
    )
    monkeypatch.setattr(
        "filemind.classification.classifier._extract_text_from_image",
        lambda path: _TEXT_HEAVY,
    )
    # Graustufen-artig -> niedriger Farbwert
    monkeypatch.setattr(
        "filemind.classification.classifier._compute_colorfulness",
        lambda path: 3.0,
    )

    result = classify_file(img)

    assert result.file_type == FileType.DOCUMENT_IMAGE
    assert result.confidence == pytest.approx(0.75)


def test_text_heavy_but_colorful_is_real_image(
    monkeypatch: "pytest.MonkeyPatch", tmp_path: Path
) -> None:
    img = tmp_path / "einladung.jpg"
    img.write_bytes(b"dummy")

    # Schwellen explizit pinnen, damit "viel Text" unabhängig von der geladenen
    # config.yaml über der Wortgrenze liegt und allein die Farbe entscheidet.
    monkeypatch.setattr(
        "filemind.classification.classifier._get_document_image_thresholds",
        lambda: (8, 18.0),
    )
    monkeypatch.setattr(
        "filemind.classification.classifier._extract_text_from_image",
        lambda path: _TEXT_HEAVY,
    )
    # Bunte Einladungskarte -> hoher Farbwert
    monkeypatch.setattr(
        "filemind.classification.classifier._compute_colorfulness",
        lambda path: 55.0,
    )

    result = classify_file(img)

    assert result.file_type == FileType.REAL_IMAGE
    assert result.confidence == pytest.approx(0.9)


def test_few_words_image_is_real_image(monkeypatch: "pytest.MonkeyPatch", tmp_path: Path) -> None:
    img = tmp_path / "strassenschild.jpg"
    img.write_bytes(b"dummy")

    # Nur wenige Wörter (z. B. ein Schild im Foto) -> kein Dokument
    monkeypatch.setattr(
        "filemind.classification.classifier._extract_text_from_image",
        lambda path: "Hauptstrasse 12",
    )

    result = classify_file(img)

    assert result.file_type == FileType.REAL_IMAGE
    assert result.confidence == pytest.approx(0.9)
