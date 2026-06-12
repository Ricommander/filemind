from pathlib import Path

import pytest

from filemind.config import (
    DEFAULT_LANGUAGE,
    get_config_file,
    get_language,
    get_section,
    reload_config,
    set_config_file,
)


def _write_config(tmp_path: Path, content: str) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(content, encoding="utf-8")
    set_config_file(config_path)
    reload_config()


def test_config_loads_sections_from_file(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("""
logging:
  level: DEBUG
  console_enabled: false

daemon:
  poll_interval: 123
""")

    set_config_file(config_path)
    reload_config()

    assert get_config_file() == config_path.resolve()
    assert get_section("logging") == {
        "level": "DEBUG",
        "console_enabled": False,
    }
    assert get_section("daemon") == {"poll_interval": 123}
    assert get_section("missing", {"default": True}) == {"default": True}


def test_reload_config_updates_cached_values(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("daemon:\n  poll_interval: 10\n")

    set_config_file(config_path)
    reload_config()
    assert get_section("daemon")["poll_interval"] == 10

    config_path.write_text("daemon:\n  poll_interval: 20\n")
    reload_config()
    assert get_section("daemon")["poll_interval"] == 20


def test_get_language_defaults_to_english(tmp_path: Path) -> None:
    _write_config(tmp_path, "daemon:\n  poll_interval: 10\n")

    assert get_language() == DEFAULT_LANGUAGE == "en"


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("de", "de"),
        ("en", "en"),
        ("DE", "de"),  # Groß-/Kleinschreibung wird normalisiert
        (" de ", "de"),  # Whitespace wird entfernt
    ],
)
def test_get_language_reads_configured_value(
    tmp_path: Path, configured: str, expected: str
) -> None:
    _write_config(tmp_path, f'language: "{configured}"\n')

    assert get_language() == expected


@pytest.mark.parametrize("invalid", ['"fr"', '"klingon"', "123", "[de, en]"])
def test_get_language_invalid_value_falls_back_to_default(tmp_path: Path, invalid: str) -> None:
    _write_config(tmp_path, f"language: {invalid}\n")

    assert get_language() == DEFAULT_LANGUAGE
