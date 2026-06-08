"""Paperless NGX API Client für filemind.

Dieses Modul bietet eine Schnittstelle zum Hochladen von Dokumenten
in ein Paperless NGX System. Konfiguration wird aus config.yaml gelesen.

Funktionalität:
- upload_document(path, title, tags, correspondent, doc_type) -> dict:
  Lädt ein Dokument in Paperless NGX hoch.
- Robuste Fehlerbehandlung mit automatischen Retries.
- Umfassendes Logging.
- HTTP-Fehler-Handling und aussagekräftige Meldungen.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass

from filemind.config import get_section

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from filemind.logging_utils.logger import get_logger

logger = get_logger(__name__)

# Konstanten
DEFAULT_TIMEOUT = 30  # Sekunden
MAX_RETRIES = 3
RETRY_BACKOFF_FACTOR = 0.5


@dataclass
class PaperlessConfig:
	"""Konfiguration für Paperless NGX.

	Attributes:
		base_url: Basis-URL des Paperless NGX Systems (z. B. http://localhost:8000).
		api_token: API-Token für Authentifizierung.
		timeout: Request-Timeout in Sekunden.
	"""

	base_url: str
	api_token: str
	timeout: int = DEFAULT_TIMEOUT

	def validate(self) -> None:
		"""Validiert die Konfiguration.

		Raises:
			ValueError: Falls erforderliche Felder fehlen oder ungültig sind.
		"""
		if not self.base_url:
			raise ValueError("Paperless base_url nicht konfiguriert")
		if not self.api_token:
			raise ValueError("Paperless api_token nicht konfiguriert")

		# Entferne trailing slash falls vorhanden
		self.base_url = self.base_url.rstrip("/")


class PaperlessClient:
	"""Client für Paperless NGX API.

	Diese Klasse verwaltet die Verbindung zu Paperless NGX und ermöglicht
	das Hochladen von Dokumenten. Sie ist robust gegen Netzwerkfehler
	und implementiert automatische Retries.

	Attributes:
		config: PaperlessConfig-Instanz mit Authentifizierung.
		session: requests.Session mit automatischen Retries.
	"""

	def __init__(self, config: Optional[PaperlessConfig] = None):
		"""Initialisiert den Paperless Client.

		Args:
			config: PaperlessConfig-Instanz. Falls None, wird aus config.yaml gelesen.

		Raises:
			ValueError: Falls Konfiguration ungültig ist.
			RuntimeError: Falls Konfigurationsdatei nicht lesbar ist.
		"""
		self.config = config or self._load_config()
		self.config.validate()

		logger.info(f"Paperless Client initialisiert: {self.config.base_url}")

		# Erstelle Session mit Retry-Mechanismus
		self.session = self._create_session()

	@staticmethod
	def _load_config() -> PaperlessConfig:
		"""Lädt Paperless-Konfiguration aus der zentralen filemind-Konfiguration."""
		logger.debug("Lade Paperless-Konfiguration")

		# Versuche zuerst aus Environment-Variablen
		base_url = os.getenv("PAPERLESS_URL")
		api_token = os.getenv("PAPERLESS_TOKEN")

		if base_url and api_token:
			logger.debug("Paperless-Konfiguration aus Environment-Variablen geladen")
			return PaperlessConfig(base_url=base_url, api_token=api_token)

		paperless_config = get_section("paperless", {})

		base_url = paperless_config.get("url")
		api_token = paperless_config.get("token")
		timeout = int(paperless_config.get("timeout", DEFAULT_TIMEOUT))

		if not base_url or not api_token:
			error_msg = (
				"Paperless-Konfiguration unvollständig in config.yaml. "
				"Erforderlich: paperless.url und paperless.token"
			)
			logger.error(error_msg)
			raise ValueError(error_msg)

		logger.debug("Paperless-Konfiguration aus filemind-Konfiguration geladen")
		return PaperlessConfig(base_url=base_url, api_token=api_token, timeout=timeout)

	def _create_session(self) -> requests.Session:
		"""Erstellt eine requests.Session mit Retry-Mechanismus.

		Returns:
			Konfigurierte requests.Session.
		"""
		session = requests.Session()

		# Retry-Strategie
		retry_strategy = Retry(
			total=MAX_RETRIES,
			status_forcelist=[429, 500, 502, 503, 504],
			allowed_methods=["HEAD", "GET", "OPTIONS", "POST"],
			backoff_factor=RETRY_BACKOFF_FACTOR,
		)

		# Mount adapter mit Retry-Strategie
		adapter = HTTPAdapter(max_retries=retry_strategy)
		session.mount("http://", adapter)
		session.mount("https://", adapter)

		# Setze Standard-Header
		session.headers.update(
			{
				"Authorization": f"Token {self.config.api_token}",
				"User-Agent": "filemind-paperless-client/1.0",
			}
		)

		logger.debug("requests.Session mit Retry-Mechanismus erstellt")
		return session

	def upload_document(
		self,
		path: Path,
		title: str,
		tags: Optional[List[str]] = None,
		correspondent: Optional[str] = None,
		doc_type: Optional[str] = None,
	) -> Dict[str, Any]:
		"""Lädt ein Dokument in Paperless NGX hoch.

		Args:
			path: Dateipfad zum hochzuladenden Dokument.
			title: Titel des Dokuments.
			tags: Liste von Tags (optional).
			correspondent: Korrespondent-Name (optional).
			doc_type: Dokumenttyp (optional).

		Returns:
			Response-Wörterbuch mit Upload-Ergebnis (enthält u.a. document_id).

		Raises:
			ValueError: Falls die Datei nicht existiert.
			RuntimeError: Falls der Upload fehlschlägt.

		Examples:
			>>> from pathlib import Path
			>>> client = PaperlessClient()
			>>> result = client.upload_document(
			...     Path("invoice.pdf"),
			...     title="Invoice 2026-05",
			...     tags=["finance", "2026"],
			...     correspondent="Supplier Inc."
			... )
			>>> print(result["document"])
			12345
		"""
		logger.debug(
			f"Starte Upload: {path.name} (Titel: {title}, "
			f"Tags: {tags}, Correspondent: {correspondent}, Type: {doc_type})"
		)

		# Validierung
		if not path.exists():
			error_msg = f"Datei nicht gefunden: {path}"
			logger.error(error_msg)
			raise ValueError(error_msg)

		if not path.is_file():
			error_msg = f"Pfad ist keine Datei: {path}"
			logger.error(error_msg)
			raise ValueError(error_msg)

		try:
			# Vorbereitung der Upload-Parameter
			files = {"document": open(path, "rb")}
			data = {
				"title": title,
			}

			# Optionale Parameter
			if tags:
				data["tags"] = ",".join(str(tag) for tag in tags)
			if correspondent:
				data["correspondent"] = correspondent
			if doc_type:
				data["document_type"] = doc_type

			# Upload-Endpoint
			url = f"{self.config.base_url}/api/documents/post_document/"

			logger.info(
				f"Hochlade Dokument: {path.name} zu {self.config.base_url} "
				f"(Titel: {title})"
			)

			# POST-Request mit Timeout und Retry-Mechanismus
			response = self.session.post(
				url,
				files=files,
				data=data,
				timeout=self.config.timeout,
			)

			# Schließe File-Handle
			files["document"].close()

			# Fehlerbehandlung
			if response.status_code == 401:
				error_msg = "Authentifizierung fehlgeschlagen (401). Überprüfe API-Token."
				logger.error(error_msg)
				raise RuntimeError(error_msg)

			elif response.status_code == 403:
				error_msg = "Zugriff verweigert (403). Überprüfe Berechtigungen."
				logger.error(error_msg)
				raise RuntimeError(error_msg)

			elif response.status_code == 404:
				error_msg = "API-Endpoint nicht gefunden (404). Überprüfe Base-URL."
				logger.error(error_msg)
				raise RuntimeError(error_msg)

			elif response.status_code >= 400:
				error_msg = (
					f"HTTP {response.status_code} Fehler: {response.text[:200]}"
				)
				logger.error(error_msg)
				raise RuntimeError(error_msg)

			# Erfolgreiche Antwort
			result = response.json()

			logger.info(
				f"Dokument erfolgreich hochgeladen: {path.name} "
				f"(Document-ID: {result.get('document', 'N/A')})"
			)

			return result

		except ValueError:
			raise  # Re-raise ValueError

		except RuntimeError:
			raise  # Re-raise RuntimeError

		except requests.exceptions.Timeout:
			error_msg = f"Request-Timeout beim Upload von {path.name}"
			logger.error(error_msg)
			raise RuntimeError(error_msg) from None

		except requests.exceptions.ConnectionError as e:
			error_msg = (
				f"Verbindungsfehler zu {self.config.base_url}: {e}. "
				"Ist der Paperless NGX Server erreichbar?"
			)
			logger.error(error_msg)
			raise RuntimeError(error_msg) from e

		except requests.exceptions.RequestException as e:
			error_msg = f"Request-Fehler beim Upload: {e}"
			logger.error(error_msg)
			raise RuntimeError(error_msg) from e

		except Exception as e:
			error_msg = f"Unerwarteter Fehler beim Upload von {path.name}: {e}"
			logger.error(error_msg)
			raise RuntimeError(error_msg) from e

	def get_document(self, document_id: int) -> Dict[str, Any]:
		"""Ruft Metadaten eines Dokuments ab.

		Args:
			document_id: ID des Dokuments.

		Returns:
			Wörterbuch mit Dokument-Metadaten.

		Raises:
			RuntimeError: Falls die Abfrage fehlschlägt.
		"""
		logger.debug(f"Rufe Dokument ab: {document_id}")

		try:
			url = f"{self.config.base_url}/api/documents/{document_id}/"
			response = self.session.get(url, timeout=self.config.timeout)

			if response.status_code >= 400:
				error_msg = f"Fehler beim Abrufen von Dokument {document_id}: {response.text}"
				logger.error(error_msg)
				raise RuntimeError(error_msg)

			return response.json()

		except requests.exceptions.RequestException as e:
			error_msg = f"Request-Fehler beim Abrufen von Dokument {document_id}: {e}"
			logger.error(error_msg)
			raise RuntimeError(error_msg) from e

	def get_tags(self) -> List[Dict[str, Any]]:
		"""Ruft die Liste aller verfügbaren Tags ab.

		Returns:
			Liste von Tag-Wörterbüchern.

		Raises:
			RuntimeError: Falls die Abfrage fehlschlägt.
		"""
		logger.debug("Rufe Tags ab")

		try:
			url = f"{self.config.base_url}/api/tags/"
			response = self.session.get(url, timeout=self.config.timeout)

			if response.status_code >= 400:
				error_msg = f"Fehler beim Abrufen von Tags: {response.text}"
				logger.error(error_msg)
				raise RuntimeError(error_msg)

			result = response.json()
			tags = result.get("results", [])

			logger.debug(f"Abgerufen {len(tags)} Tags")
			return tags

		except requests.exceptions.RequestException as e:
			error_msg = f"Request-Fehler beim Abrufen von Tags: {e}"
			logger.error(error_msg)
			raise RuntimeError(error_msg) from e

	def health_check(self) -> bool:
		"""Prüft die Verbindung zu Paperless NGX.

		Returns:
			True, falls die Verbindung erfolgreich ist.
		"""
		logger.debug(f"Führe Health-Check durch: {self.config.base_url}")

		try:
			url = f"{self.config.base_url}/api/"
			response = self.session.get(url, timeout=self.config.timeout)

			if response.status_code == 200:
				logger.info(f"Health-Check erfolgreich: {self.config.base_url}")
				return True
			else:
				logger.warning(f"Health-Check fehlgeschlagen: HTTP {response.status_code}")
				return False

		except Exception as e:
			logger.warning(f"Health-Check Fehler: {e}")
			return False

	def close(self) -> None:
		"""Schließt die Session."""
		try:
			self.session.close()
			logger.debug("Paperless-Session geschlossen")
		except Exception as e:
			logger.warning(f"Fehler beim Schließen der Session: {e}")

	def __del__(self):
		"""Cleanup beim Löschen des Objekts."""
		self.close()


# Globale Instanz
_global_client: Optional[PaperlessClient] = None


def get_client(config: Optional[PaperlessConfig] = None) -> PaperlessClient:
	"""Besorgt die globale PaperlessClient-Instanz (Singleton).

	Args:
		config: Optional custom PaperlessConfig (nur beim ersten Aufruf relevant).

	Returns:
		Die globale PaperlessClient-Instanz.

	Raises:
		ValueError: Falls Konfiguration ungültig ist.
		RuntimeError: Falls Client nicht initialisiert werden kann.
	"""
	global _global_client

	if _global_client is None:
		_global_client = PaperlessClient(config)

	return _global_client


# Convenience-Funktionen
def upload_document(
	path: Path,
	title: str,
	tags: Optional[List[str]] = None,
	correspondent: Optional[str] = None,
	doc_type: Optional[str] = None,
) -> Dict[str, Any]:
	"""Lädt ein Dokument in Paperless NGX hoch.

	Convenience-Funktion, die die globale PaperlessClient-Instanz nutzt.

	Args:
		path: Dateipfad.
		title: Dokument-Titel.
		tags: Liste von Tags (optional).
		correspondent: Korrespondent (optional).
		doc_type: Dokumenttyp (optional).

	Returns:
		Upload-Ergebnis als Wörterbuch.

	Raises:
		ValueError: Falls Datei nicht existiert.
		RuntimeError: Falls Upload fehlschlägt.
	"""
	return get_client().upload_document(path, title, tags, correspondent, doc_type) 