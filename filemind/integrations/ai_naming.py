"""KI-basierte Namensgebung für filemind.

Dieses Modul generiert intelligente Dateinamen mittels eines lokal
gehosteten Ollama-Vision-Modells (Default: ``llava:7b``).

Ablauf für echte Fotos (REAL_IMAGE), wie in der README beschrieben:
1. Das Bild wird an das Vision-Modell geschickt, das eine kurze
   Beschreibung des Bildinhalts liefert.
2. Aus dieser Beschreibung wird in einem zweiten Schritt ein kurzer,
   dateinamen-tauglicher Stamm (ohne Endung) generiert.

Für alle anderen Dateitypen (und als Fallback, wenn Ollama nicht
erreichbar ist) wird ein deterministischer Name aus Dateityp und
Originalnamen erzeugt.

Konfiguration über ``config.yaml`` (Sektion ``ai``)::

    ai:
      enabled: true
      provider: "ollama"
      model: "llava:7b"
      url: "http://localhost:11434"
      timeout: 300
      max_image_size: 1024
      num_ctx: 8192

Die Sprache der generierten Namen wird über den Top-Level-Schlüssel
``language`` ("de" oder "en") gesteuert.

Die Umgebungsvariablen ``OLLAMA_MODEL`` und ``OLLAMA_URL`` übersteuern
die Konfigurationswerte.
"""

from __future__ import annotations

import base64
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from filemind.classification.classifier import classify_file
from filemind.config import get_language, get_section
from filemind.core.models import FileType
from filemind.logging_utils.logger import get_logger

logger = logging.getLogger(__name__)
ollama_logger = get_logger("filemind.integrations.ai_naming.ollama")

DEFAULT_MODEL = "llava:7b"
DEFAULT_URL = "http://localhost:11434"
DEFAULT_TIMEOUT = 300
DEFAULT_MAX_IMAGE_SIZE = 1024  # Pixel (längste Kante) für die Übertragung an das Vision-Modell
# Kontextfenster (Tokens) für Ollama-Requests. Ohne explizites num_ctx gilt der
# Ollama-Server-Default (4096, ältere Versionen 2048) — zu wenig für Vision-Modelle
# mit dynamischer Auflösung, deren Bild-Token den Prompt sonst sprengen/abschneiden.
DEFAULT_NUM_CTX = 8192
# Token-Budgets für die Generierung. Thinking-Modelle (z. B. qwen3-vl) verbrauchen
# erst mehrere hundert Tokens für die Denkphase, bevor die eigentliche Antwort
# beginnt - zu kleine Budgets enden mit done_reason "length" und leerer Antwort.
# Nicht-denkende Modelle (z. B. llava) stoppen ohnehin früher am Stop-Token.
DESCRIBE_NUM_PREDICT = 1024
STEM_NUM_PREDICT = 1024
MAX_STEM_LENGTH = 60

# Anzeigename der Zielsprache für die Prompts (die Instruktionen selbst
# bleiben englisch, da die Modelle diese am zuverlässigsten befolgen).
_LANGUAGE_NAMES = {"en": "English", "de": "German"}

_DESCRIBE_PROMPT_TEMPLATE = (
    "Describe the main subject of this photo in one short {language_name} sentence. "
    "Mention the most important objects, people, animals or scenery. "
    "Reply with the {language_name} description only, no preamble."
)

_NAME_PROMPT_TEMPLATE = (
    "You are a filename generator.\n"
    "Based on the following image description, produce a single short filename stem "
    "(no extension).\n"
    "Rules: use {language_name} words; use only lowercase letters, numbers and underscores; "
    "at most 4 words; max 40 characters; no dates; no quotes; no explanation.\n\n"
    "DESCRIPTION: {description}\n\n"
    "Reply with the filename stem only."
)

# Sprachspezifische Präfixe für deterministische Fallback-Namen
_DUMMY_PREFIXES = {
    "en": {
        FileType.REAL_IMAGE: "photo",
        FileType.DOCUMENT_IMAGE: "scan",
        FileType.TEXT_DOCUMENT: "doc",
        FileType.VIDEO: "video",
        FileType.AUDIO: "audio",
        FileType.ARCHIVES: "archive",
        FileType.OTHER: "file",
    },
    "de": {
        FileType.REAL_IMAGE: "foto",
        FileType.DOCUMENT_IMAGE: "scan",
        FileType.TEXT_DOCUMENT: "dokument",
        FileType.VIDEO: "video",
        FileType.AUDIO: "audio",
        FileType.ARCHIVES: "archiv",
        FileType.OTHER: "datei",
    },
}

# Transliteration statt Löschung, damit deutsche Wörter lesbar bleiben
# ("gewürz" -> "gewuerz" statt "gewrz")
_TRANSLITERATIONS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def _language_name() -> str:
    """Gibt den Prompt-Anzeigenamen der konfigurierten Zielsprache zurück."""
    return _LANGUAGE_NAMES.get(get_language(), _LANGUAGE_NAMES["en"])


def _load_ai_config() -> Dict[str, Any]:
    """Lädt die ``ai``-Sektion der Konfiguration mit Defaults und Env-Overrides."""
    cfg = get_section("ai", {}) or {}

    model = os.environ.get("OLLAMA_MODEL") or cfg.get("model", DEFAULT_MODEL)
    url = os.environ.get("OLLAMA_URL") or cfg.get("url", DEFAULT_URL)

    try:
        timeout = float(cfg.get("timeout", DEFAULT_TIMEOUT))
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT

    try:
        max_image_size = int(cfg.get("max_image_size", DEFAULT_MAX_IMAGE_SIZE))
        if max_image_size <= 0:
            max_image_size = DEFAULT_MAX_IMAGE_SIZE
    except (TypeError, ValueError):
        max_image_size = DEFAULT_MAX_IMAGE_SIZE

    try:
        num_ctx = int(cfg.get("num_ctx", DEFAULT_NUM_CTX))
        if num_ctx <= 0:
            num_ctx = DEFAULT_NUM_CTX
    except (TypeError, ValueError):
        num_ctx = DEFAULT_NUM_CTX

    return {
        "model": model,
        "url": url,
        "timeout": timeout,
        "max_image_size": max_image_size,
        "num_ctx": num_ctx,
    }


def _generate_endpoint(url: str) -> str:
    """Normalisiert die Ollama-URL auf den ``/api/generate``-Endpoint."""
    url = url.rstrip("/")
    if url.endswith("/api/generate"):
        return url
    return f"{url}/api/generate"


def _ollama_generate(
    prompt: str,
    images: Optional[List[str]] = None,
    num_predict: int = 128,
    temperature: float = 0.2,
) -> Optional[str]:
    """Schickt einen Generate-Request an Ollama und liefert den Antworttext.

    Args:
        prompt: Der Text-Prompt.
        images: Optionale Liste base64-kodierter Bilder (Vision-Modelle).
        num_predict: Maximale Anzahl generierter Tokens.
        temperature: Sampling-Temperatur.

    Returns:
        Der Antworttext oder ``None`` bei Fehlern.
    """
    cfg = _load_ai_config()
    endpoint = _generate_endpoint(cfg["url"])

    payload: Dict[str, Any] = {
        "model": cfg["model"],
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": num_predict,
            "num_ctx": cfg["num_ctx"],
        },
    }
    if images:
        payload["images"] = images

    try:
        resp = requests.post(endpoint, json=payload, timeout=cfg["timeout"])
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        ollama_logger.warning(f"Ollama-Request fehlgeschlagen ({endpoint}): {e}")
        return None

    if not isinstance(data, dict):
        ollama_logger.warning(f"Unerwartete Ollama-Antwort: {data!r}")
        return None

    response_text = data.get("response")
    if not isinstance(response_text, str) or not response_text.strip():
        thinking = data.get("thinking")
        if isinstance(thinking, str) and thinking.strip():
            ollama_logger.warning(
                f"Ollama lieferte keine Antwort: Thinking-Modell {cfg['model']!r} hat "
                f"das Token-Budget (num_predict={num_predict}) komplett für die "
                f"Denkphase verbraucht (done_reason={data.get('done_reason')!r})"
            )
        else:
            ollama_logger.warning("Ollama lieferte keine Antwort")
        return None

    return response_text.strip()


def _encode_image_base64(path: Path) -> Optional[str]:
    """Kodiert eine Bilddatei base64 für die Ollama-API.

    Das Bild wird vorher auf ``ai.max_image_size`` Pixel (längste Kante)
    verkleinert und als JPEG re-kodiert. Das ist entscheidend für
    Vision-Modelle mit dynamischer Auflösung (z. B. qwen3-vl): Ein
    12-MP-Originalfoto erzeugt dort zehntausende Bild-Token, deren
    Verarbeitung auf schwachen CPUs viele Minuten dauert und in
    Timeouts läuft. Für eine kurze Bildbeschreibung reicht ~1 MP.

    Die Originaldatei wird dabei niemals verändert: Die verkleinerte
    Kopie existiert ausschließlich im Arbeitsspeicher (``BytesIO``) und
    wird nach dem Request verworfen — es wird keine temporäre Datei
    auf die Platte geschrieben.

    Kann Pillow das Format nicht lesen, werden die Originalbytes
    unverändert gesendet (Fallback).
    """
    max_size = _load_ai_config()["max_image_size"]

    try:
        from io import BytesIO

        from PIL import Image, ImageOps

        with Image.open(path) as img:
            # EXIF-Rotation anwenden, damit das Modell das Bild richtig herum sieht
            img = ImageOps.exif_transpose(img)
            original_size = img.size
            if max(img.size) > max_size:
                img.thumbnail((max_size, max_size))
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            buffer = BytesIO()
            img.save(buffer, format="JPEG", quality=85)

        logger.debug(
            f"Bild für AI-Naming verkleinert: {path.name} "
            f"{original_size} -> {img.size}, {buffer.tell()} bytes"
        )
        return base64.b64encode(buffer.getvalue()).decode("ascii")

    except Exception as e:
        logger.debug(f"Bild-Verkleinerung nicht möglich ({e}), sende Original: {path.name}")

    try:
        return base64.b64encode(path.read_bytes()).decode("ascii")
    except Exception as e:
        logger.warning(f"Konnte Bild nicht lesen für AI-Naming: {path}: {e}")
        return None


def describe_image(path: Path) -> Optional[str]:
    """Lässt das Vision-Modell den Bildinhalt kurz beschreiben.

    Args:
        path: Pfad zur Bilddatei.

    Returns:
        Eine kurze Beschreibung oder ``None`` bei Fehlern.
    """
    encoded = _encode_image_base64(path)
    if not encoded:
        return None

    prompt = _DESCRIBE_PROMPT_TEMPLATE.format(language_name=_language_name())
    description = _ollama_generate(prompt, images=[encoded], num_predict=DESCRIBE_NUM_PREDICT)
    if description:
        ollama_logger.info(f"Bildbeschreibung für {path.name}: {description}")
    return description


def _stem_from_description(description: str) -> Optional[str]:
    """Generiert aus einer Bildbeschreibung einen Dateinamens-Stamm."""
    prompt = _NAME_PROMPT_TEMPLATE.format(description=description, language_name=_language_name())
    candidate = _ollama_generate(prompt, num_predict=STEM_NUM_PREDICT)
    if not candidate:
        return None

    stem = _clean_to_stem(candidate.splitlines()[0])
    return stem or None


def truncate_stem(stem: str, limit: int = MAX_STEM_LENGTH) -> str:
    """Kürzt einen Dateinamens-Stamm auf ``limit`` Zeichen.

    Geschnitten wird an einer Wortgrenze (Unterstrich), damit keine
    abgehackten Wortreste wie "auf_e" entstehen. Nur wenn innerhalb des
    Limits keine Wortgrenze existiert, wird hart geschnitten.
    """
    if len(stem) <= limit:
        return stem
    cut = stem[:limit]
    if "_" in cut:
        cut = cut.rsplit("_", 1)[0]
    return cut.rstrip("_")


def to_pascal_case(text: str, limit: int = MAX_STEM_LENGTH) -> str:
    """Wandelt einen Dateinamens-Stamm in PascalCase mit Unterstrichen um.

    Wörter werden an Trennzeichen (Unterstrich, Bindestrich, Leerzeichen) sowie
    sonstigen Nicht-Alphanumerischen Zeichen erkannt, jeweils mit großem
    Anfangsbuchstaben versehen und durch Unterstriche getrennt::

        "dog_playing_park" -> "Dog_Playing_Park"

    Das Ergebnis wird an einer Wortgrenze auf ``limit`` Zeichen begrenzt; ein
    einzelnes überlanges Wort wird hart geschnitten. Die Funktion ist idempotent:
    Bereits so formatierte Eingaben bleiben unverändert.
    """
    words = [word[:1].upper() + word[1:] for word in re.findall(r"[A-Za-z0-9]+", text or "")]

    result = ""
    for word in words:
        if not result:
            # Erstes Wort: notfalls hart auf das Limit schneiden, falls es allein
            # schon zu lang ist.
            result = word[:limit]
            continue
        # +1 für den verbindenden Unterstrich.
        if len(result) + 1 + len(word) > limit:
            break
        result = f"{result}_{word}"

    return result


def _clean_to_stem(text: str) -> str:
    """Bereinigt einen Text zu einem Dateinamens-Stamm.

    Umlaute und ß werden transliteriert (ä -> ae, ß -> ss). Erlaubt sind
    Kleinbuchstaben, Ziffern und Unterstriche; das Ergebnis wird an einer
    Wortgrenze auf ``MAX_STEM_LENGTH`` Zeichen gekürzt.
    """
    if not text:
        return ""
    s = text.lower()
    s = s.translate(_TRANSLITERATIONS)
    s = re.sub(r"\s+", "_", s.strip())
    s = re.sub(r"[^a-z0-9_]+", "", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return truncate_stem(s)


def _generate_dummy_name(original_name: str, file_type: FileType) -> str:
    """Erzeugt einen deterministischen Fallback-Namen ohne KI.

    Das Präfix richtet sich nach Dateityp und konfigurierter Zielsprache
    (z. B. ``photo`` vs. ``foto``).

    Args:
        original_name: Ursprünglicher Dateiname (ohne Endung).
        file_type: Erkannter ``FileType``.

    Returns:
        Neuer Dateinamens-Stamm (ohne Endung).
    """
    prefix_map = _DUMMY_PREFIXES.get(get_language(), _DUMMY_PREFIXES["en"])
    prefix = prefix_map.get(file_type, prefix_map[FileType.OTHER])
    name_part = original_name[:20].replace(" ", "_") if original_name else "unnamed"

    return f"{prefix}_{name_part}"


def generate_smart_name(path: Path) -> str:
    """Generiert einen intelligenten Dateinamens-Stamm für eine Datei.

    Für echte Fotos wird das konfigurierte Ollama-Vision-Modell genutzt:
    Erst wird das Bild beschrieben, dann aus der Beschreibung ein kurzer
    Name abgeleitet. Für alle anderen Typen (oder wenn Ollama nicht
    erreichbar ist) wird ein deterministischer Fallback-Name erzeugt.

    Args:
        path: Pfad zur Datei.

    Returns:
        Vorgeschlagener Dateinamens-Stamm (ohne Pfad, ohne Endung).

    Raises:
        ValueError: Falls die Datei nicht existiert.
    """
    logger.debug(f"Smart Naming gestartet für: {path}")

    if not path.exists():
        error_msg = f"Datei nicht gefunden: {path}"
        logger.error(error_msg)
        raise ValueError(error_msg)

    try:
        classification = classify_file(path)
    except FileNotFoundError as e:
        logger.error(f"Klassifizierung fehlgeschlagen: {e}")
        raise ValueError(str(e)) from e

    file_type = classification.file_type
    logger.info(f"Generiere Namen für: {path.name} (Typ: {file_type.value})")

    if file_type == FileType.REAL_IMAGE:
        try:
            description = describe_image(path)
            if description:
                stem = _stem_from_description(description)
                if stem:
                    return stem
                # Degradierter Fallback: Stamm direkt aus der Beschreibung
                stem = _clean_to_stem(description)
                if stem:
                    return stem
        except Exception as e:
            ollama_logger.warning(f"AI-Naming fehlgeschlagen für {path.name}: {e}")

    return _generate_dummy_name(path.stem, file_type)
