from pathlib import Path

import pytest

from filemind.storage import structure_manager
from filemind.storage.structure_manager import (
    get_base_folders,
    get_folder_stats,
    get_target_subfolder,
)


def test_get_target_subfolder_creates_first_folder(tmp_path: Path) -> None:
    base_folder = tmp_path / "Pictures"

    target = get_target_subfolder(base_folder, 2026)

    assert target.exists()
    assert target.name == "2026_01"
    assert target.parent == base_folder


def test_get_target_subfolder_rolls_over_when_folder_full(
    tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
) -> None:
    monkeypatch.setattr(structure_manager, "MAX_FILES_PER_SUBFOLDER", 3)

    base_folder = tmp_path / "Pictures"
    first_target = get_target_subfolder(base_folder, 2026)
    for index in range(3):
        file_path = first_target / f"file_{index}.txt"
        file_path.write_text("content")

    second_target = get_target_subfolder(base_folder, 2026)

    assert second_target.exists()
    assert second_target.name == "2026_02"
    assert second_target != first_target


def test_get_base_folders_creates_all_folders(tmp_path: Path) -> None:
    folders = get_base_folders(tmp_path)

    assert set(folders.keys()) == structure_manager.BASE_FOLDERS
    for folder in folders.values():
        assert folder.exists()


def test_get_folder_stats_returns_counts(tmp_path: Path) -> None:
    pictures = tmp_path / "Pictures"
    target = get_target_subfolder(pictures, 2026)
    for index in range(2):
        (target / f"doc_{index}.txt").write_text("content")

    stats = get_folder_stats(pictures)

    assert stats == {"2026_01": 2}
