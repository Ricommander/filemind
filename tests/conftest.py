"""Gemeinsame Test-Fixtures für filemind."""

from __future__ import annotations

import pytest

import filemind.config as config_module


@pytest.fixture(autouse=True)
def _isolate_config():
    """Stellt den globalen Konfigurationszustand nach jedem Test wieder her."""
    old_file = config_module._config_file
    old_cache = config_module._config_cache
    yield
    config_module._config_file = old_file
    config_module._config_cache = old_cache
