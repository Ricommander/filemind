import datetime
from types import SimpleNamespace
from pathlib import Path

import pytest

from filemind.integrations.metadata_extractor import get_file_creation_date


def test_get_file_creation_date_from_stat(monkeypatch, tmp_path):
    p = tmp_path / "sample.txt"
    p.write_text("hello")

    # Use a fixed timestamp (2021-01-01 00:00:00 local)
    ts = 1609459200

    def fake_stat(self):
        return SimpleNamespace(st_ctime=ts)

    monkeypatch.setattr("pathlib.Path.stat", fake_stat)

    expected = datetime.datetime.fromtimestamp(ts).date().isoformat()
    assert get_file_creation_date(p) == expected


def test_get_file_creation_date_fallback(monkeypatch, tmp_path):
    p = tmp_path / "sample2.txt"
    p.write_text("world")

    def fake_stat_raise(self):
        raise OSError("stat failed")

    monkeypatch.setattr("pathlib.Path.stat", fake_stat_raise)

    expected = datetime.date.today().isoformat()
    assert get_file_creation_date(p) == expected
