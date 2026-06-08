"""KI-basierte Umbenennung für filemind.

Dieses Modul bietet eine intelligente Abstraktionsschicht für dateibasierte
Namensgebung mittels KI/LLM. Aktuell ist eine Dummy-Implementierung vorhanden,
die später gegen echte LLM-Engines (OpenAI, Hugging Face, lokal gehostete
Modelle, etc.) ausgetauscht werden kann.

Funktionalität:
- generate_smart_name(path: Path) -> str: Generiert einen intelligenten
  Dateinamen basierend auf Dateityp und Pfad.
- Robuste Fehlerbehandlung und Logging.
- Vorbereitet für KI-Modell-Integration.
"""

from __future__ import annotations

import logging
from pathlib import Path
from datetime import date
from typing import Optional
import os
import re
import requests

from filemind.classification.classifier import classify_file
from filemind.core.models import FileType
from filemind.logging_utils.logger import get_logger

logger = logging.getLogger(__name__)
ollama_logger = get_logger("filemind.integrations.ai_naming.ollama")


def generate_smart_name(path: Path) -> str:
	"""Generiert einen intelligenten Dateinamen mittels KI.

	Diese Funktion ist aktuell eine Dummy-Implementierung und gibt
	einen generierten Namen basierend auf Dateiendung und Pfad zurück.
	Sie kann später gegen echte LLM-Engines ausgetauscht werden.

	Args:
		path: Pfad zur Datei, für die ein intelligenter Name generiert
			werden soll.

	Returns:
		Ein vorgeschlagener neuer Dateiname (ohne Pfad, nur Basename).

	Raises:
		ValueError: Falls die Datei nicht existiert.
		RuntimeError: Falls die Namensgebung fehlschlägt.

	Examples:
		>>> from pathlib import Path
		>>> name = generate_smart_name(Path("scan_001.pdf"))
		>>> print(name)
		document_scan.pdf

		>>> name = generate_smart_name(Path("IMG_2024.jpg"))
		>>> print(name)
		photo_2024.jpg
	"""
	logger.debug(f"Smart Naming gestartet für: {path}")

	# Validierung: Datei existiert?
	if not path.exists():
		error_msg = f"Datei nicht gefunden: {path}"
		logger.error(error_msg)
		raise ValueError(error_msg)

	# Nutze die offizielle Klassifizierung von classifier
	try:
		classification = classify_file(path)
	except FileNotFoundError as e:
		logger.error(f"Klassifizierung fehlgeschlagen: {e}")
		raise ValueError(str(e))

	# Extrahiere Informationen
	original_name = path.stem  # Name ohne Endung
	file_extension = path.suffix.lower()
	file_type = classification.file_type

	logger.info(
		f"Generiere Namen für: {path.name} "
		f"(Typ: {file_type.value}, Endung: {file_extension})"
	)

	# If this is a real photo and OCR yields some text, try Ollama first
	# Special handling for real images: OCR first, then AI description.
	if file_type == FileType.REAL_IMAGE:
		try:
			from filemind.integrations.ocr import ocr_to_text

			ocr_text = ""
			try:
				ocr_text = ocr_to_text(path) or ""
			except Exception as e:
				ollama_logger.debug(f"OCR failed: {e}")

			# Priority 1: OCR text
			if ocr_text.strip():
				stem = _clean_to_stem(ocr_text)
				if stem:
					return stem

			# Priority 2: AI description
			ai_desc = _describe_with_ollama(path, ocr_text)
			if ai_desc and ai_desc.strip():
				stem = _clean_to_stem(ai_desc)
				if stem:
					return stem

		except Exception as e:
			ollama_logger.debug(f"OCR/AI attempt failed: {e}")

	# Fallback for non-images or when nothing was found: return dummy name (includes extension)
	return _generate_dummy_name(original_name, file_type)

	logger.info(f"Neuer Name generiert: {smart_name}")
	logger.debug(f"Original: {path.name} -> Neu: {smart_name}")

	return smart_name


def _generate_dummy_name(
	original_name: str, file_type: FileType
) -> str:
	"""Generiert einen Dummy-Namen für eine Datei.

	Diese Implementierung ist ein Platzhalter und wird später durch
	echte KI-Logik ersetzt.

	Args:
		original_name: Ursprünglicher Dateiname (ohne Endung).
		file_type: Erkannter `FileType` Enum-Wert.
		extension: Dateiendung (z. B. ".pdf").

	Returns:
		Neuer generierter Dateiname.
	"""
	# Präfix basierend auf Dateityp
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

	# Verwende einen Teil des Originalnamens oder einen einfachen Index
	name_part = original_name[:20].replace(" ", "_") if original_name else "unnamed"

	# Generiere neuen Namen
	new_name = f"{prefix}_{name_part}"

	return new_name


def _generate_name_with_ollama(ocr_text: str, original_name: str) -> Optional[str]:
	"""Fragt eine lokale Ollama-Instanz nach einem kurzen, geeigneten
	Dateinamens-Stamm (ohne Endung) basierend auf OCR-Text.

	Liefert `None` bei Fehlern oder wenn kein sinnvoller Vorschlag vorliegt.
	"""
	model = os.environ.get("OLLAMA_MODEL", "llama2")
	url = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")

	prompt = (
		"You are a concise filename generator.\n"
		"Given the following OCR-extracted text from an image, produce a single short filename stem (no extension)."
		" Return only the filename text. Requirements: use only lowercase letters, numbers and underscores; max 30 characters; at most 3 words; prefer descriptive nouns over dates or punctuation.\n\n"
		f"OCR_TEXT: {ocr_text}\n\n"
		"Provide only the filename stem without quotes or explanation."
	)

	try:
		resp = requests.post(url, json={"model": model, "prompt": prompt, "max_tokens": 32, "temperature": 0.2}, timeout=10)
		resp.raise_for_status()

		# Try to extract text from JSON if possible
		try:
			data = resp.json()
		except Exception:
			data = None

		candidate = None
		if isinstance(data, dict):
			# Common Ollama shapes: {"choices": [{"text": "..."}]}
			if "choices" in data and isinstance(data["choices"], list) and data["choices"]:
				candidate = data["choices"][0].get("text") or data["choices"][0].get("output")
			elif "output" in data and isinstance(data["output"], str):
				candidate = data["output"]
		if not candidate:
			candidate = resp.text

		if not candidate:
			return None

		# Clean candidate: keep only a-z0-9 and underscores, replace spaces with underscores
		candidate = candidate.strip().splitlines()[0]
		candidate = candidate.lower()
		candidate = candidate.replace(" ", "_")
		candidate = re.sub(r"[^a-z0-9_]+", "", candidate)
		candidate = candidate.strip("_")
		if not candidate:
			return None
		# Truncate to 30 chars
		return candidate[:30]

	except Exception as e:
		ollama_logger.debug(f"Ollama call failed: {e}")
		return None


def _describe_with_ollama(path: Path, ocr_text: str) -> Optional[str]:
	"""Fragt Ollama nach einer kurzen textuellen Beschreibung des Bildes.

	Übergibt OCR-Text und Dateiname als Kontext. Liefert `None` bei Fehlern.
	"""
	model = os.environ.get("OLLAMA_MODEL", "llama2")
	url = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")

	prompt = (
		"You are an image describer.\n"
		"Given the OCR-extracted text (may be empty) and the filename, produce a brief (1-2 phrase) textual description of what the image likely contains."
		" Keep it concise and factual.\n\n"
		f"FILENAME: {path.name}\n"
		f"OCR_TEXT: {ocr_text}\n\n"
		"Provide only the description text without quotes or extra commentary."
	)

	try:
		resp = requests.post(url, json={"model": model, "prompt": prompt, "max_tokens": 64, "temperature": 0.3}, timeout=10)
		resp.raise_for_status()
		try:
			data = resp.json()
		except Exception:
			data = None

		candidate = None
		if isinstance(data, dict):
			if "choices" in data and isinstance(data["choices"], list) and data["choices"]:
				candidate = data["choices"][0].get("text") or data["choices"][0].get("output")
			elif "output" in data and isinstance(data["output"], str):
				candidate = data["output"]
		if not candidate:
			candidate = resp.text

		if not candidate:
			return None

		# Use first line of response
		candidate = candidate.strip().splitlines()[0]
		return candidate.strip()

	except Exception as e:
		ollama_logger.debug(f"Ollama describe call failed: {e}")
		return None


def _clean_to_stem(text: str) -> str:
	"""Bereinigt einen Text zu einem Dateinamens-Stamm: lowercase, underscores, allowed chars."""
	if not text:
		return ""
	s = text.lower()
	s = s.replace("\n", " ")
	s = s.replace("\t", " ")
	s = re.sub(r"\s+", " ", s).strip()
	s = s.replace(" ", "_")
	s = re.sub(r"[^a-z0-9_]+", "", s)
	s = s.strip("_")
	return s[:30]


def _generate_stem_with_ollama(ocr_text: str, ai_desc: str, original_name: str) -> Optional[str]:
	"""Fragt Ollama nach einem kurzen Dateinamens-Stamm, erhält OCR- und AI-Beschreibung.

	Die Systemanweisung sagt Ollama, OCR zu bevorzugen. Liefert `None` bei Fehlschlag.
	"""
	model = os.environ.get("OLLAMA_MODEL", "llama2")
	url = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")

	prompt = (
		"You are a concise filename generator.\n"
		"You will be given two inputs: OCR_TEXT (may be empty) and AI_DESC (an AI-provided brief description).\n"
		"When creating the filename stem, PREFER content from OCR_TEXT if present, but you may augment or shorten it using AI_DESC.\n"
		"Requirements: return only the filename stem (no extension), use only lowercase letters, numbers and underscores, max 30 characters, at most 3 words. Do not include dates — we will add the date prefix.\n\n"
		f"OCR_TEXT: {ocr_text}\n"
		f"AI_DESC: {ai_desc}\n"
		f"FILENAME_HINT: {original_name}\n\n"
		"Provide only the filename stem without quotes or explanation."
	)

	try:
		resp = requests.post(url, json={"model": model, "prompt": prompt, "max_tokens": 32, "temperature": 0.2}, timeout=10)
		resp.raise_for_status()
		try:
			data = resp.json()
		except Exception:
			data = None

		candidate = None
		if isinstance(data, dict):
			if "choices" in data and isinstance(data["choices"], list) and data["choices"]:
				candidate = data["choices"][0].get("text") or data["choices"][0].get("output")
			elif "output" in data and isinstance(data["output"], str):
				candidate = data["output"]
		if not candidate:
			candidate = resp.text

		if not candidate:
			return None

		candidate = candidate.strip().splitlines()[0]
		candidate = candidate.lower()
		candidate = candidate.replace(" ", "_")
		candidate = re.sub(r"[^a-z0-9_]+", "", candidate)
		candidate = candidate.strip("_")
		if not candidate:
			return None
		return candidate[:30]

	except Exception as e:
		ollama_logger.debug(f"Ollama name-choice call failed: {e}")
		return None


# Zukünftige Implementierungen können hier eingefügt werden:
#
# OpenAI API Integration:
# - _generate_name_openai(path: Path, model: str = "gpt-4") -> str
#
# Hugging Face Integration:
# - _generate_name_huggingface(path: Path, model_id: str) -> str
#
# Lokal gehostete Modelle:
# - _generate_name_local_llm(path: Path, model_path: str) -> str
#
# Vision-LLMs für Bilder:
# - _generate_name_vision_llm(path: Path, model: str) -> str
#
# Diese können dann in `generate_smart_name()` basierend auf Konfiguration
# (config.yaml) ausgewählt und aufgerufen werden.


def _validate_naming_config() -> bool:
	"""Validiert, ob die KI-Konfiguration korrekt ist.

	Returns:
		True, falls die Konfiguration gültig ist.

	Raises:
		RuntimeError: Falls die Konfiguration ungültig oder unvollständig ist.
	"""
	# Zukünftige Implementierung: Prüfe config.yaml auf LLM-Parameter
	# - API-Schlüssel vorhanden?
	# - Modell konfiguriert?
	# - Endpoints erreichbar?
	logger.debug("Validiere AI-Konfiguration...")
	return True
