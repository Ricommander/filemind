"""Binäre Deduplizierung für filemind.

Dieses Modul verwaltet SHA-256 Hashes von Dateien zur Erkennung von Duplikaten.
Der Hash-Index wird persistent in einer SQLite-Datenbank gespeichert.

Funktionalität:
- compute_sha256(path: Path) -> str: Berechnet SHA-256 Hash einer Datei.
- is_duplicate(path: Path) -> bool: Prüft, ob eine Datei bereits existiert.
- register_file(path: Path) -> None: Registriert eine Datei im Hash-Index.
- is_registered_unchanged(path: Path) -> bool: Prüft ohne Hashing, ob eine Datei
  bereits mit unveränderter Größe registriert ist.
- Robustheit gegen Abstürze durch SQLite-Transaktionen.
- Umfassendes Logging.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
from pathlib import Path
from typing import Dict, List, Optional

from filemind.logging_utils.logger import get_logger

logger = get_logger(__name__)

# Globale Konstanten
DEFAULT_DB_PATH = Path.cwd() / ".filemind" / "hash_store.db"
BUFFER_SIZE = 1024 * 1024  # 1 MB Buffer für Datei-Hashing
DUPLICATE_CACHE_SIZE = 10000  # Maximale Größe des In-Memory Cache


class HashStore:
    """Verwaltet den SHA-256 Hash-Index für Duplikat-Erkennung.

    Diese Klasse verwaltet eine SQLite-Datenbank zur Speicherung von Datei-Hashes.
    Sie ist Thread-sicher und robust gegen Abstürze.

    Attributes:
            db_path: Pfad zur SQLite-Datenbank.
            _db_connection: Datenbankverbindung (Thread-lokal).
            _cache: In-Memory Cache für Hashes zur Performance-Optimierung.
    """

    def __init__(self, db_path: Optional[Path] = None):
        """Initialisiert den Hash-Store.

        Args:
                db_path: Pfad zur SQLite-Datenbank. Nutzt DEFAULT_DB_PATH falls nicht angegeben.
        """
        self.db_path = db_path or DEFAULT_DB_PATH
        self._local = threading.local()
        self._cache: Dict[str, str] = {}  # Hash-Cache für Performance
        self._cache_lock = threading.Lock()

        logger.debug(f"HashStore initialisiert: {self.db_path}")

        # Stelle sicher, dass das Verzeichnis existiert
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Initialisiere die Datenbank
        self._init_database()

    def _get_connection(self) -> sqlite3.Connection:
        """Besorgt eine Thread-lokale Datenbankverbindung.

        Returns:
                SQLite3 Datenbankverbindung.
        """
        if not hasattr(self._local, "connection") or self._local.connection is None:
            try:
                self._local.connection = sqlite3.connect(
                    str(self.db_path),
                    check_same_thread=False,
                    timeout=10.0,  # 10 Sekunden Timeout für Locks
                )
                # Ermögliche Write-Ahead Logging für Robustheit
                self._local.connection.execute("PRAGMA journal_mode=WAL")
                logger.debug(f"Datenbankverbindung hergestellt: {self.db_path}")
            except Exception as e:
                error_msg = f"Fehler beim Verbinden zur Datenbank {self.db_path}: {e}"
                logger.error(error_msg)
                raise RuntimeError(error_msg) from e

        return self._local.connection

    def _init_database(self) -> None:
        """Initialisiert die Datenbank-Tabelle falls nötig."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # Erstelle Tabelle falls nicht vorhanden
            cursor.execute("""
				CREATE TABLE IF NOT EXISTS file_hashes (
					id INTEGER PRIMARY KEY AUTOINCREMENT,
					file_path TEXT UNIQUE NOT NULL,
					file_hash TEXT NOT NULL,
					file_size INTEGER NOT NULL,
					created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
					last_checked TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
				)
				""")

            # Erstelle Index für schnelle Lookups nach Hash
            cursor.execute("""
				CREATE INDEX IF NOT EXISTS idx_file_hash ON file_hashes(file_hash)
				""")

            conn.commit()
            logger.debug("Datenbank-Tabelle initialisiert")

        except Exception as e:
            error_msg = f"Fehler bei der Datenbank-Initialisierung: {e}"
            logger.error(error_msg)
            raise RuntimeError(error_msg) from e

    def compute_sha256(self, path: Path) -> str:
        """Berechnet den SHA-256 Hash einer Datei.

        Args:
                path: Pfad zur zu hashenden Datei.

        Returns:
                Hexadezimaler SHA-256 Hash-String.

        Raises:
                ValueError: Falls die Datei nicht existiert.
                RuntimeError: Falls das Hashing fehlschlägt.

        Examples:
                >>> from pathlib import Path
                >>> hash_value = compute_sha256(Path("document.pdf"))
                >>> print(hash_value)
                a1b2c3d4e5f6...
        """
        logger.debug(f"Berechne SHA-256 Hash für: {path}")

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
            sha256_hash = hashlib.sha256()

            # Lese Datei in Chunks
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(BUFFER_SIZE), b""):
                    sha256_hash.update(chunk)

            hash_value = sha256_hash.hexdigest()
            file_size = path.stat().st_size

            logger.debug(
                f"Hash berechnet für {path.name}: {hash_value[:16]}... "
                f"(Größe: {file_size} bytes)"
            )

            return hash_value

        except Exception as e:
            error_msg = f"Fehler beim Hashing von {path}: {e}"
            logger.error(error_msg)
            raise RuntimeError(error_msg) from e

    def is_duplicate(self, path: Path) -> bool:
        """Prüft, ob eine Datei bereits im Hash-Index existiert.

        Args:
                path: Pfad zur zu prüfenden Datei.

        Returns:
                True, falls ein Hash dieser Datei im Index existiert.

        Raises:
                ValueError: Falls die Datei nicht existiert.
                RuntimeError: Falls die Datenbank-Abfrage fehlschlägt.

        Examples:
                >>> from pathlib import Path
                >>> if is_duplicate(Path("photo.jpg")):
                ...     print("Duplikat gefunden!")
        """
        logger.debug(f"Prüfe auf Duplikat: {path}")

        # Berechne Hash
        file_hash = self.compute_sha256(path)

        # Prüfe Cache erst
        with self._cache_lock:
            if file_hash in self._cache:
                logger.debug(f"Duplikat im Cache gefunden: {file_hash[:16]}...")
                return True

        # Prüfe Datenbank
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute(
                "SELECT COUNT(*) FROM file_hashes WHERE file_hash = ?",
                (file_hash,),
            )

            result = cursor.fetchone()
            is_dup = result[0] > 0 if result else False

            if is_dup:
                # Nur DEBUG: die fachliche Meldung übernimmt der Router (einmalig)
                logger.debug(f"Duplikat erkannt: {path.name} (Hash: {file_hash[:16]}...)")
                # Aktualisiere Cache
                with self._cache_lock:
                    if len(self._cache) >= DUPLICATE_CACHE_SIZE:
                        self._cache.clear()
                    self._cache[file_hash] = str(path)

            logger.debug(f"Duplikat-Check für {path.name}: {is_dup}")
            return is_dup

        except Exception as e:
            error_msg = f"Fehler beim Duplikat-Check für {path}: {e}"
            logger.error(error_msg)
            raise RuntimeError(error_msg) from e

    def register_file(self, path: Path) -> None:
        """Registriert eine Datei im Hash-Index.

        Args:
                path: Pfad zur zu registrierenden Datei.

        Raises:
                ValueError: Falls die Datei nicht existiert.
                RuntimeError: Falls die Registrierung fehlschlägt.

        Examples:
                >>> from pathlib import Path
                >>> register_file(Path("document.pdf"))
        """
        logger.debug(f"Registriere Datei: {path}")

        # Berechne Hash
        file_hash = self.compute_sha256(path)
        file_size = path.stat().st_size

        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # Versuche Einfügung
            cursor.execute(
                """
				INSERT OR REPLACE INTO file_hashes
				(file_path, file_hash, file_size, last_checked)
				VALUES (?, ?, ?, CURRENT_TIMESTAMP)
				""",
                (str(path), file_hash, file_size),
            )

            conn.commit()

            # Aktualisiere Cache
            with self._cache_lock:
                if len(self._cache) >= DUPLICATE_CACHE_SIZE:
                    self._cache.clear()
                self._cache[file_hash] = str(path)

            # Nur DEBUG: bei der Startup-Registrierung des Zielbestands würde
            # INFO das Log mit einer Zeile pro Datei fluten
            logger.debug(
                f"Datei registriert: {path.name} (Hash: {file_hash[:16]}..., "
                f"Größe: {file_size} bytes)"
            )

        except Exception as e:
            error_msg = f"Fehler beim Registrieren von {path}: {e}"
            logger.error(error_msg)
            raise RuntimeError(error_msg) from e

    def is_registered_unchanged(self, path: Path) -> bool:
        """Prüft ohne Hashing, ob eine Datei bereits unverändert registriert ist.

        Vergleicht nur Pfad und Dateigröße gegen den Index. Das ist um
        Größenordnungen schneller als ``register_file`` (kein Lesen des
        Dateiinhalts) und erlaubt es, beim Daemon-Start die erneute
        Hash-Berechnung für bereits bekannte Ziel-Dateien zu überspringen.

        Einschränkung: Eine inhaltlich geänderte Datei mit identischer Größe
        wird nicht erkannt. Für den von filemind selbst verwalteten
        Zielbestand ist das akzeptabel.

        Args:
                path: Dateipfad.

        Returns:
                True, falls der Pfad mit unveränderter Größe registriert ist.
        """
        try:
            file_size = path.stat().st_size

            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute(
                "SELECT file_size FROM file_hashes WHERE file_path = ?",
                (str(path),),
            )

            result = cursor.fetchone()
            return result is not None and result[0] == file_size

        except Exception as e:
            logger.debug(f"Fehler bei is_registered_unchanged für {path}: {e}")
            return False

    def get_hash_by_path(self, path: Path) -> Optional[str]:
        """Ruft den Hash einer registrierten Datei ab.

        Args:
                path: Dateipfad.

        Returns:
                Der gespeicherte Hash oder None, falls die Datei nicht registriert ist.
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute(
                "SELECT file_hash FROM file_hashes WHERE file_path = ?",
                (str(path),),
            )

            result = cursor.fetchone()
            return result[0] if result else None

        except Exception as e:
            logger.warning(f"Fehler beim Abrufen des Hash für {path}: {e}")
            return None

    def get_all_hashes(self) -> Dict[str, str]:
        """Ruft alle registrierten Hashes ab.

        Returns:
                Wörterbuch mit Dateipfaden als Schlüssel und Hashes als Werte.
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute("SELECT file_path, file_hash FROM file_hashes")

            result = {row[0]: row[1] for row in cursor.fetchall()}
            logger.debug(f"Abgerufen {len(result)} registrierte Hashes")

            return result

        except Exception as e:
            logger.warning(f"Fehler beim Abrufen aller Hashes: {e}")
            return {}

    def delete_hash(self, path: Path) -> bool:
        """Löscht einen Hash-Eintrag aus dem Index.

        Args:
                path: Dateipfad.

        Returns:
                True, falls der Eintrag gelöscht wurde.
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute("DELETE FROM file_hashes WHERE file_path = ?", (str(path),))

            conn.commit()

            if cursor.rowcount > 0:
                logger.info(f"Hash-Eintrag gelöscht: {path}")
                return True
            else:
                logger.debug(f"Kein Hash-Eintrag gefunden für: {path}")
                return False

        except Exception as e:
            logger.warning(f"Fehler beim Löschen des Hash für {path}: {e}")
            return False

    def get_statistics(self) -> Dict[str, int]:
        """Ruft Statistiken über den Hash-Index ab.

        Returns:
                Wörterbuch mit Statistiken.
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(DISTINCT file_hash) FROM file_hashes")
            unique_hashes = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM file_hashes")
            total_files = cursor.fetchone()[0]

            cursor.execute("SELECT SUM(file_size) FROM file_hashes")
            total_size = cursor.fetchone()[0] or 0

            stats = {
                "unique_hashes": unique_hashes,
                "total_files": total_files,
                "total_size_bytes": total_size,
                "duplicate_count": max(0, total_files - unique_hashes),
            }

            logger.debug(f"Hash-Index Statistiken: {stats}")

            return stats

        except Exception as e:
            logger.warning(f"Fehler beim Abrufen der Statistiken: {e}")
            return {}

    def close(self) -> None:
        """Schließt die Datenbankverbindung."""
        if hasattr(self._local, "connection") and self._local.connection:
            try:
                self._local.connection.close()
                logger.debug("Datenbankverbindung geschlossen")
            except Exception as e:
                logger.warning(f"Fehler beim Schließen der Datenbankverbindung: {e}")

    def __del__(self):
        """Cleanup beim Löschen des Objekts."""
        self.close()

    def find_paths_by_hash(self, file_hash: str) -> List[str]:
        """Gibt alle registrierten Dateipfade für einen gegebenen Hash zurück.

        Args:
                file_hash: SHA-256 Hash-String.

        Returns:
                Liste von Dateipfaden (als Strings). Leere Liste falls keiner gefunden.
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute(
                "SELECT file_path FROM file_hashes WHERE file_hash = ?",
                (file_hash,),
            )
            rows = [row[0] for row in cursor.fetchall()]
            return rows

        except Exception as e:
            logger.warning(f"Fehler beim Abrufen von Pfaden für Hash {file_hash}: {e}")
            return []

    def is_hash_in_directory(self, file_hash: str, directory: Path) -> bool:
        """Prüft, ob ein gegebener Hash bereits unter einem bestimmten Verzeichnis registriert ist.

        Args:
                file_hash: SHA-256 Hash-String.
                directory: Basisverzeichnis, in dem gesucht werden soll.

        Returns:
                True, falls mindestens ein registrierter Pfad mit dem Verzeichnispräfix existiert.
        """
        try:
            candidates = self.find_paths_by_hash(file_hash)
            if not candidates:
                return False

            dir_norm = os.path.normcase(str(Path(directory)))
            for p in candidates:
                try:
                    p_norm = os.path.normcase(str(Path(p)))
                    if p_norm == dir_norm or p_norm.startswith(dir_norm + os.sep):
                        return True
                except Exception:
                    continue
            return False

        except Exception as e:
            logger.warning(f"Fehler bei is_hash_in_directory: {e}")
            return False


# Globale Instanz
_global_hash_store: Optional[HashStore] = None


def _get_configured_db_path() -> Path:
    """Ermittelt den DB-Pfad aus der Konfiguration (``storage.hash_db_path``)."""
    try:
        from filemind.config import get_section

        storage_cfg = get_section("storage", {}) or {}
        configured = storage_cfg.get("hash_db_path")
        if configured:
            return Path(configured)
    except Exception as e:
        logger.debug(f"Konnte hash_db_path nicht aus Konfiguration lesen: {e}")
    return DEFAULT_DB_PATH


def get_hash_store(db_path: Optional[Path] = None) -> HashStore:
    """Besorgt die globale HashStore-Instanz (Singleton).

    Args:
            db_path: Pfad zur SQLite-Datenbank (nur beim ersten Aufruf relevant).
                    Falls nicht angegeben, wird ``storage.hash_db_path`` aus der
                    Konfiguration verwendet (Fallback: ``DEFAULT_DB_PATH``).

    Returns:
            Die globale HashStore-Instanz.
    """
    global _global_hash_store

    if _global_hash_store is None:
        _global_hash_store = HashStore(db_path or _get_configured_db_path())

    return _global_hash_store


# Convenience-Funktionen (verwenden die globale Instanz)
def compute_sha256(path: Path) -> str:
    """Berechnet den SHA-256 Hash einer Datei.

    Convenience-Funktion, die die globale HashStore-Instanz nutzt.

    Args:
            path: Dateipfad.

    Returns:
            Hexadezimaler SHA-256 Hash.
    """
    return get_hash_store().compute_sha256(path)


def is_duplicate(path: Path) -> bool:
    """Prüft, ob eine Datei bereits im Hash-Index existiert.

    Convenience-Funktion, die die globale HashStore-Instanz nutzt.

    Args:
            path: Dateipfad.

    Returns:
            True, falls ein Duplikat existiert.
    """
    return get_hash_store().is_duplicate(path)


def register_file(path: Path) -> None:
    """Registriert eine Datei im Hash-Index.

    Convenience-Funktion, die die globale HashStore-Instanz nutzt.

    Args:
            path: Dateipfad.
    """
    get_hash_store().register_file(path)


def is_registered_unchanged(path: Path) -> bool:
    """Prüft ohne Hashing, ob eine Datei bereits unverändert registriert ist.

    Convenience-Funktion, die die globale HashStore-Instanz nutzt.

    Args:
            path: Dateipfad.

    Returns:
            True, falls der Pfad mit unveränderter Größe registriert ist.
    """
    return get_hash_store().is_registered_unchanged(path)


def find_paths_by_hash(file_hash: str) -> List[str]:
    """Convenience: Alle Pfade zu einem Hash abrufen."""
    return get_hash_store().find_paths_by_hash(file_hash)


def is_hash_in_directory(file_hash: str, directory: Path) -> bool:
    """Convenience: Prüft ob Hash bereits unter `directory` registriert ist."""
    return get_hash_store().is_hash_in_directory(file_hash, directory)


def reset_hash_store(db_path: Optional[Path] = None) -> None:
    """Resets the global hash store by closing and removing the DB file.

    If `db_path` is provided, that path is used; otherwise the default DB
    path is used. After removal the global singleton is cleared so the next
    call to `get_hash_store()` will recreate the database file and tables.
    """
    global _global_hash_store

    # Determine path
    target_path = Path(db_path) if db_path is not None else _get_configured_db_path()

    # Close existing connection if present
    try:
        if _global_hash_store is not None:
            try:
                _global_hash_store.close()
            except Exception:
                pass
            _global_hash_store = None

        # Remove DB file if exists
        if target_path.exists():
            try:
                target_path.unlink()
            except Exception:
                # Best-effort: ignore deletion errors
                pass
    except Exception:
        # Swallow errors to avoid preventing daemon start; caller may log
        return
