from pathlib import Path

from filemind.config import get_config, get_config_file, get_section, reload_config, set_config_file


def test_config_loads_sections_from_file(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
logging:
  level: DEBUG
  console_enabled: false

daemon:
  poll_interval: 123
"""
    )

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
