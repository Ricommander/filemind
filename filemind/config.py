from __future__ import annotations

import yaml
from pathlib import Path
from typing import Any, Dict, Optional, Union

DEFAULT_CONFIG_FILE = Path.cwd() / "config.yaml"

_config_file: Path = DEFAULT_CONFIG_FILE
_config_cache: Optional[Dict[str, Any]] = None


def set_config_file(path: Union[str, Path]) -> None:
    """Setzt den Pfad zur Konfigurationsdatei und leert den Cache."""
    global _config_file, _config_cache

    config_path = Path(path).expanduser()
    if not config_path.is_absolute():
        config_path = config_path.resolve()

    _config_file = config_path
    _config_cache = None


def get_config_file() -> Path:
    """Gibt den aktuell konfigurierten Config-Pfad zurück."""
    return _config_file


def load_config(config_file: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
    """Lädt die Konfiguration aus YAML und cached sie für nachfolgende Zugriffe."""
    global _config_cache

    if config_file is None:
        if _config_cache is not None:
            return _config_cache
        config_path = _config_file
    else:
        config_path = Path(config_file).expanduser()
        if not config_path.is_absolute():
            config_path = config_path.resolve()

    if not config_path.exists():
        _config_cache = {} if config_file is None else {}
        return _config_cache

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
    except Exception:
        _config_cache = {} if config_file is None else {}
        return _config_cache

    if config_file is None:
        _config_cache = loaded

    return loaded


def reload_config() -> Dict[str, Any]:
    """Lädt die Konfiguration neu, unabhängig vom Cache."""
    global _config_cache

    _config_cache = None
    return load_config()


def get_config() -> Dict[str, Any]:
    """Gibt die aktuell geladene Konfiguration zurück."""
    if _config_cache is None:
        load_config()
    return _config_cache or {}


def get_section(section: str, default: Any = None) -> Any:
    """Gibt einen Konfigurationsabschnitt zurück."""
    if default is None:
        default = {}
    config = get_config()
    return config.get(section, default)
