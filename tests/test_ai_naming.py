"""Unit-Tests für die KI-basierte Namensgebung (Ollama-Integration)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import pytest

from filemind.config import reload_config, set_config_file
from filemind.integrations import ai_naming


@pytest.fixture
def ai_config(tmp_path: Path) -> Path:
    """Minimale Konfiguration mit aktivierter AI-Sektion."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
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
    return config_path


@pytest.fixture(autouse=True)
def _no_ocr(monkeypatch: pytest.MonkeyPatch):
    """Verhindert, dass der Klassifizierer echtes OCR (EasyOCR) anwirft."""
    monkeypatch.setattr(
        "filemind.classification.classifier._extract_text_from_image",
        lambda path: "",
    )


class FakeResponse:
    def __init__(self, payload: Dict[str, Any], status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> Dict[str, Any]:
        return self._payload


def test_clean_to_stem_sanitizes_text() -> None:
    assert ai_naming._clean_to_stem("A Dog playing in the Park!") == "a_dog_playing_in_the_park"
    assert ai_naming._clean_to_stem("  spaces\tand\nnewlines ") == "spaces_and_newlines"
    assert ai_naming._clean_to_stem("Ümläute & Sönderzeichen") == "mlute_snderzeichen"
    assert ai_naming._clean_to_stem("") == ""
    assert len(ai_naming._clean_to_stem("x" * 200)) == ai_naming.MAX_STEM_LENGTH


def test_generate_endpoint_normalizes_url() -> None:
    assert (
        ai_naming._generate_endpoint("http://localhost:11434")
        == "http://localhost:11434/api/generate"
    )
    assert (
        ai_naming._generate_endpoint("http://localhost:11434/")
        == "http://localhost:11434/api/generate"
    )
    assert (
        ai_naming._generate_endpoint("http://localhost:11434/api/generate")
        == "http://localhost:11434/api/generate"
    )


def test_load_ai_config_uses_config_values(ai_config: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_URL", raising=False)

    cfg = ai_naming._load_ai_config()

    assert cfg["model"] == "llava:7b"
    assert cfg["url"] == "http://localhost:11434"
    assert cfg["timeout"] == 5.0


def test_load_ai_config_env_overrides(ai_config: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_MODEL", "other-model")
    monkeypatch.setenv("OLLAMA_URL", "http://example:1234")

    cfg = ai_naming._load_ai_config()

    assert cfg["model"] == "other-model"
    assert cfg["url"] == "http://example:1234"


def test_generate_smart_name_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        ai_naming.generate_smart_name(tmp_path / "missing.jpg")


def test_generate_smart_name_image_uses_describe_then_name(
    ai_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_path = tmp_path / "photo.jpg"
    image_path.write_bytes(b"fake image data")

    calls = []

    def fake_post(url: str, json: Dict[str, Any], timeout: float) -> FakeResponse:
        calls.append(json)
        if "images" in json:
            return FakeResponse({"response": "A brown dog playing in a park."})
        return FakeResponse({"response": "dog_playing_park"})

    monkeypatch.setattr(ai_naming.requests, "post", fake_post)

    name = ai_naming.generate_smart_name(image_path)

    assert name == "dog_playing_park"
    # Erster Call: Vision (mit Bild), zweiter Call: Namensgenerierung (nur Text)
    assert len(calls) == 2
    assert "images" in calls[0]
    assert "images" not in calls[1]
    assert "A brown dog playing in a park." in calls[1]["prompt"]
    assert calls[0]["model"] == "llava:7b"
    assert calls[0]["stream"] is False


def test_generate_smart_name_falls_back_to_description_stem(
    ai_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_path = tmp_path / "photo.jpg"
    image_path.write_bytes(b"fake image data")

    def fake_post(url: str, json: Dict[str, Any], timeout: float) -> FakeResponse:
        if "images" in json:
            return FakeResponse({"response": "Sunset over the ocean"})
        # Namensgenerierung liefert leere Antwort
        return FakeResponse({"response": ""})

    monkeypatch.setattr(ai_naming.requests, "post", fake_post)

    name = ai_naming.generate_smart_name(image_path)

    assert name == "sunset_over_the_ocean"


def test_generate_smart_name_image_falls_back_when_ollama_down(
    ai_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_path = tmp_path / "IMG_2024.jpg"
    image_path.write_bytes(b"fake image data")

    def fake_post(*args: Any, **kwargs: Any) -> FakeResponse:
        raise ConnectionError("Ollama nicht erreichbar")

    monkeypatch.setattr(ai_naming.requests, "post", fake_post)

    name = ai_naming.generate_smart_name(image_path)

    assert name == "photo_IMG_2024"


def test_generate_smart_name_audio_does_not_call_ollama(
    ai_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audio_path = tmp_path / "song.mp3"
    audio_path.write_bytes(b"fake audio data")

    def fail_post(*args: Any, **kwargs: Any) -> FakeResponse:
        raise AssertionError("Ollama darf für Audio nicht aufgerufen werden")

    monkeypatch.setattr(ai_naming.requests, "post", fail_post)

    name = ai_naming.generate_smart_name(audio_path)

    assert name == "audio_song"


def test_ollama_generate_handles_invalid_response(
    ai_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        ai_naming.requests,
        "post",
        lambda *a, **kw: FakeResponse({"unexpected": "shape"}),
    )

    assert ai_naming._ollama_generate("prompt") is None


def test_ollama_generate_handles_http_error(
    ai_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        ai_naming.requests,
        "post",
        lambda *a, **kw: FakeResponse({}, status_code=500),
    )

    assert ai_naming._ollama_generate("prompt") is None


def test_describe_image_sends_base64_image(
    ai_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import base64

    image_path = tmp_path / "photo.jpg"
    content = b"raw image bytes"
    image_path.write_bytes(content)

    captured: Dict[str, Any] = {}

    def fake_post(url: str, json: Dict[str, Any], timeout: float) -> FakeResponse:
        captured.update(json)
        return FakeResponse({"response": "A cat on a sofa."})

    monkeypatch.setattr(ai_naming.requests, "post", fake_post)

    description = ai_naming.describe_image(image_path)

    assert description == "A cat on a sofa."
    assert captured["images"] == [base64.b64encode(content).decode("ascii")]


def test_describe_image_unreadable_file_returns_none(
    ai_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "missing.jpg"

    def fail_post(*args: Any, **kwargs: Any) -> Optional[FakeResponse]:
        raise AssertionError("Ollama darf ohne Bilddaten nicht aufgerufen werden")

    monkeypatch.setattr(ai_naming.requests, "post", fail_post)

    assert ai_naming.describe_image(missing) is None
