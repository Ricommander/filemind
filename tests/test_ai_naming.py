"""Unit-Tests für die KI-basierte Namensgebung (Ollama-Integration)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import pytest

from filemind.config import reload_config, set_config_file
from filemind.integrations import ai_naming


def _write_ai_config(tmp_path: Path, language: str = "en") -> Path:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
language: "{language}"

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


@pytest.fixture
def ai_config(tmp_path: Path) -> Path:
    """Minimale Konfiguration mit aktivierter AI-Sektion (Sprache: en)."""
    return _write_ai_config(tmp_path, language="en")


@pytest.fixture
def ai_config_de(tmp_path: Path) -> Path:
    """Minimale Konfiguration mit aktivierter AI-Sektion (Sprache: de)."""
    return _write_ai_config(tmp_path, language="de")


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
    assert ai_naming._clean_to_stem("") == ""
    assert len(ai_naming._clean_to_stem("x" * 200)) == ai_naming.MAX_STEM_LENGTH


def test_clean_to_stem_transliterates_german_characters() -> None:
    assert ai_naming._clean_to_stem("Müde Bären im Großen Wald") == "muede_baeren_im_grossen_wald"
    assert ai_naming._clean_to_stem("Gewürzregal & Öl") == "gewuerzregal_oel"


def test_truncate_stem_cuts_at_word_boundary() -> None:
    assert ai_naming.truncate_stem("kurzer_name") == "kurzer_name"

    long_stem = "zwei_rhinozerosse_grasen_auf_einer_gruenen_flaeche_mit_baeumen_im_park"
    cut = ai_naming.truncate_stem(long_stem)
    assert len(cut) <= ai_naming.MAX_STEM_LENGTH
    assert long_stem.startswith(cut)
    # Schnitt liegt an einer Wortgrenze, kein abgehackter Wortrest wie "auf_e"
    assert long_stem[len(cut)] == "_"

    # Einzelnes überlanges Wort ohne Wortgrenze: harter Schnitt am Limit
    assert ai_naming.truncate_stem("x" * 200) == "x" * ai_naming.MAX_STEM_LENGTH


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


def test_load_ai_config_uses_config_values(
    ai_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_URL", raising=False)

    cfg = ai_naming._load_ai_config()

    assert cfg["model"] == "llava:7b"
    assert cfg["url"] == "http://localhost:11434"
    assert cfg["timeout"] == 5.0
    assert cfg["num_ctx"] == ai_naming.DEFAULT_NUM_CTX


def test_load_ai_config_reads_num_ctx(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("ai:\n  num_ctx: 16384\n", encoding="utf-8")
    set_config_file(config_path)
    reload_config()

    assert ai_naming._load_ai_config()["num_ctx"] == 16384


def test_load_ai_config_invalid_num_ctx_falls_back_to_default(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text('ai:\n  num_ctx: "viele"\n', encoding="utf-8")
    set_config_file(config_path)
    reload_config()

    assert ai_naming._load_ai_config()["num_ctx"] == ai_naming.DEFAULT_NUM_CTX


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
    # num_ctx muss explizit mitgeschickt werden, sonst gilt der (zu kleine) Server-Default
    assert calls[0]["options"]["num_ctx"] == ai_naming.DEFAULT_NUM_CTX
    assert calls[1]["options"]["num_ctx"] == ai_naming.DEFAULT_NUM_CTX


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


def test_prompts_request_english_by_default(
    ai_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_path = tmp_path / "photo.jpg"
    image_path.write_bytes(b"fake image data")

    prompts = []

    def fake_post(url: str, json: Dict[str, Any], timeout: float) -> FakeResponse:
        prompts.append(json["prompt"])
        return FakeResponse({"response": "a house"})

    monkeypatch.setattr(ai_naming.requests, "post", fake_post)

    ai_naming.generate_smart_name(image_path)

    assert len(prompts) == 2
    assert "English" in prompts[0]  # Beschreibung
    assert "use English words" in prompts[1]  # Namensgenerierung
    assert "German" not in prompts[0] and "German" not in prompts[1]


def test_prompts_request_german_when_language_de(
    ai_config_de: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_path = tmp_path / "photo.jpg"
    image_path.write_bytes(b"fake image data")

    prompts = []

    def fake_post(url: str, json: Dict[str, Any], timeout: float) -> FakeResponse:
        prompts.append(json["prompt"])
        if "images" in json:
            return FakeResponse({"response": "Ein Nashorn läuft über eine Wiese."})
        return FakeResponse({"response": "nashorn_auf_wiese"})

    monkeypatch.setattr(ai_naming.requests, "post", fake_post)

    name = ai_naming.generate_smart_name(image_path)

    assert name == "nashorn_auf_wiese"
    assert len(prompts) == 2
    assert "German" in prompts[0]  # Beschreibung
    assert "use German words" in prompts[1]  # Namensgenerierung


def test_german_description_fallback_keeps_umlauts_readable(
    ai_config_de: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_path = tmp_path / "photo.jpg"
    image_path.write_bytes(b"fake image data")

    def fake_post(url: str, json: Dict[str, Any], timeout: float) -> FakeResponse:
        if "images" in json:
            return FakeResponse({"response": "Müde Bären im Wald"})
        # Namensgenerierung schlägt fehl -> Fallback auf Beschreibung
        return FakeResponse({"response": ""})

    monkeypatch.setattr(ai_naming.requests, "post", fake_post)

    assert ai_naming.generate_smart_name(image_path) == "muede_baeren_im_wald"


def test_dummy_name_uses_german_prefix_when_language_de(
    ai_config_de: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_path = tmp_path / "IMG_2024.jpg"
    image_path.write_bytes(b"fake image data")

    def fail_post(*args: Any, **kwargs: Any) -> FakeResponse:
        raise ConnectionError("Ollama nicht erreichbar")

    monkeypatch.setattr(ai_naming.requests, "post", fail_post)

    assert ai_naming.generate_smart_name(image_path) == "foto_IMG_2024"


def test_encode_image_downscales_large_photos(ai_config: Path, tmp_path: Path) -> None:
    import base64
    from io import BytesIO

    from PIL import Image

    image_path = tmp_path / "big_photo.jpg"
    Image.new("RGB", (4000, 2000), "red").save(image_path, "JPEG")

    encoded = ai_naming._encode_image_base64(image_path)

    assert encoded is not None
    sent_image = Image.open(BytesIO(base64.b64decode(encoded)))
    assert max(sent_image.size) <= ai_naming.DEFAULT_MAX_IMAGE_SIZE
    assert sent_image.size == (1024, 512)  # Seitenverhältnis bleibt erhalten


def test_encode_image_respects_configured_max_size(tmp_path: Path) -> None:
    import base64
    from io import BytesIO

    from PIL import Image

    from filemind.config import reload_config, set_config_file

    config_path = tmp_path / "config.yaml"
    config_path.write_text("ai:\n  max_image_size: 512\n", encoding="utf-8")
    set_config_file(config_path)
    reload_config()

    image_path = tmp_path / "photo.jpg"
    Image.new("RGB", (2000, 1000), "blue").save(image_path, "JPEG")

    encoded = ai_naming._encode_image_base64(image_path)

    sent_image = Image.open(BytesIO(base64.b64decode(encoded)))
    assert max(sent_image.size) <= 512


def test_encode_image_keeps_small_images_dimensions(ai_config: Path, tmp_path: Path) -> None:
    import base64
    from io import BytesIO

    from PIL import Image

    image_path = tmp_path / "small.png"
    Image.new("RGB", (300, 200), "green").save(image_path, "PNG")

    encoded = ai_naming._encode_image_base64(image_path)

    sent_image = Image.open(BytesIO(base64.b64decode(encoded)))
    assert sent_image.size == (300, 200)


def test_encode_image_falls_back_to_raw_bytes_for_unreadable_format(
    ai_config: Path, tmp_path: Path
) -> None:
    import base64

    image_path = tmp_path / "not_really_an_image.jpg"
    content = b"these are not valid image bytes"
    image_path.write_bytes(content)

    encoded = ai_naming._encode_image_base64(image_path)

    assert encoded == base64.b64encode(content).decode("ascii")


def test_encode_image_never_modifies_original_file(ai_config: Path, tmp_path: Path) -> None:
    """Das Downscaling darf nur im Speicher passieren, nie auf der Originaldatei."""
    from PIL import Image

    image_path = tmp_path / "original.jpg"
    Image.new("RGB", (3000, 2000), "red").save(image_path, "JPEG")
    original_bytes = image_path.read_bytes()

    ai_naming._encode_image_base64(image_path)

    assert image_path.read_bytes() == original_bytes
    # Keine temporären Dateien zurückgelassen
    assert sorted(p.name for p in tmp_path.iterdir()) == ["config.yaml", "original.jpg"]


def test_generate_smart_name_never_modifies_original_file(
    ai_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die komplette Namensgenerierung darf das Original nicht anfassen."""
    from PIL import Image

    image_path = tmp_path / "photo.jpg"
    Image.new("RGB", (3000, 2000), "blue").save(image_path, "JPEG")
    original_bytes = image_path.read_bytes()

    def fake_post(url: str, json: Dict[str, Any], timeout: float) -> FakeResponse:
        if "images" in json:
            return FakeResponse({"response": "A blue wall."})
        return FakeResponse({"response": "blue_wall"})

    monkeypatch.setattr(ai_naming.requests, "post", fake_post)

    assert ai_naming.generate_smart_name(image_path) == "blue_wall"
    assert image_path.read_bytes() == original_bytes
