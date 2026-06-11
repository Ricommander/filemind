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
      timeout: 120

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
from filemind.config import get_section
from filemind.core.models import FileType
from filemind.logging_utils.logger import get_logger

logger = logging.getLogger(__name__)
ollama_logger = get_logger("filemind.integrations.ai_naming.ollama")

DEFAULT_MODEL = "llava:7b"
DEFAULT_URL = "http://localhost:11434"
DEFAULT_TIMEOUT = 120
MAX_STEM_LENGTH = 60

_DESCRIBE_PROMPT = (
    "Describe the main subject of this photo in one short English sentence. "
    "Mention the most important objects, people, animals or scenery. "
    "Reply with the description only, no preamble."
)

_NAME_PROMPT_TEMPLATE = (
    "You are a filename generator.\n"
    "Based on the following image description, produce a single short filename stem "
    "(no extension).\n"
    "Rules: use only lowercase letters, numbers and underscores; "
    "at most 4 words; max 40 characters; no dates; no quotes; no explanation.\n\n"
    "DESCRIPTION: {description}\n\n"
    "Reply with the filename stem only."
)


def _load_ai_config() -> Dict[str, Any]:
    """Lädt die ``ai``-Sektion der Konfiguration mit Defaults und Env-Overrides."""
    cfg = get_section("ai", {}) or {}

    model = os.environ.get("OLLAMA_MODEL") or cfg.get("model", DEFAULT_MODEL)
    url = os.environ.get("OLLAMA_URL") or cfg.get("url", DEFAULT_URL)

    try:
        timeout = float(cfg.get("timeout", DEFAULT_TIMEOUT))
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT

    return {"model": model, "url": url, "timeout": timeout}


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
        "options": {"temperature": temperature, "num_predict": num_predict},
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
        ollama_logger.warning("Ollama lieferte keine Antwort")
        return None

    return response_text.strip()


def _encode_image_base64(path: Path) -> Optional[str]:
    """Liest eine Bilddatei und kodiert sie base64 für die Ollama-API."""
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

    description = _ollama_generate(_DESCRIBE_PROMPT, images=[encoded], num_predict=96)
    if description:
        ollama_logger.info(f"Bildbeschreibung für {path.name}: {description}")
    return description


def _stem_from_description(description: str) -> Optional[str]:
    """Generiert aus einer Bildbeschreibung einen Dateinamens-Stamm."""
    prompt = _NAME_PROMPT_TEMPLATE.format(description=description)
    candidate = _ollama_generate(prompt, num_predict=32)
    if not candidate:
        return None

    stem = _clean_to_stem(candidate.splitlines()[0])
    return stem or None


def _clean_to_stem(text: str) -> str:
    """Bereinigt einen Text zu einem Dateinamens-Stamm.

    Erlaubt sind Kleinbuchstaben, Ziffern und Unterstriche; das Ergebnis
    wird auf ``MAX_STEM_LENGTH`` Zeichen gekürzt.
    """
    if not text:
        return ""
    s = text.lower()
    s = re.sub(r"\s+", "_", s.strip())
    s = re.sub(r"[^a-z0-9_]+", "", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:MAX_STEM_LENGTH]


def _generate_dummy_name(original_name: str, file_type: FileType) -> str:
    """Erzeugt einen deterministischen Fallback-Namen ohne KI.

    Args:
        original_name: Ursprünglicher Dateiname (ohne Endung).
        file_type: Erkannter ``FileType``.

    Returns:
        Neuer Dateinamens-Stamm (ohne Endung).
    """
    prefix_map = {
        FileType.REAL_IMAGE: "photo",
        FileType.DOCUMENT_IMAGE: "scan",
        FileType.TEXT_DOCUMENT: "doc",
        FileType.VIDEO: "video",
        FileType.AUDIO: "audio",
        FileType.ARCHIVES: "archive",
        FileType.OTHER: "file",
    }

    prefix = prefix_map.get(file_type, "file")
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
