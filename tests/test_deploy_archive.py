from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from deploy.deploy_archive import (
    DeployArchiveError,
    extract_verified_archive,
    inspect_archive,
)


def _write_archive(path: Path, members: list[tuple[str, bytes]]) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for name, content in members:
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))


def _required_members() -> list[tuple[str, bytes]]:
    return [
        ("main.py", b"main"),
        ("requirements.txt", b"requests==2.32.0\n"),
        ("config.yaml", b"logging: {}\n"),
        ("filemind/__init__.py", b"__version__ = '0.4.0'\n"),
        ("filemind/core.py", b"value = 1\n"),
    ]


def test_valid_deployment_archive_extracts_only_application_files(tmp_path: Path) -> None:
    archive = tmp_path / "release.tar.gz"
    destination = tmp_path / "staging"
    _write_archive(archive, _required_members())

    names = inspect_archive(archive)
    extract_verified_archive(archive, destination)

    assert "filemind/core.py" in names
    assert (destination / "main.py").read_bytes() == b"main"
    assert (destination / "filemind/core.py").read_bytes() == b"value = 1\n"
    assert not (destination / ".filemind").exists()


def test_archive_rejects_path_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "traversal.tar.gz"
    _write_archive(archive, _required_members() + [("../outside.txt", b"bad")])

    with pytest.raises(DeployArchiveError, match="unsafe archive path"):
        inspect_archive(archive)


def test_archive_rejects_symlinks(tmp_path: Path) -> None:
    archive = tmp_path / "link.tar.gz"
    with tarfile.open(archive, "w:gz") as package:
        for name, content in _required_members():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            package.addfile(member, io.BytesIO(content))
        link = tarfile.TarInfo("filemind/link.py")
        link.type = tarfile.SYMTYPE
        link.linkname = "../../outside.py"
        package.addfile(link)

    with pytest.raises(DeployArchiveError, match="links and special files"):
        inspect_archive(archive)


def test_archive_rejects_unexpected_files_and_duplicate_entries(tmp_path: Path) -> None:
    unexpected = tmp_path / "unexpected.tar.gz"
    _write_archive(unexpected, _required_members() + [("README.md", b"not deployed")])
    with pytest.raises(DeployArchiveError, match="unexpected package file"):
        inspect_archive(unexpected)

    duplicate = tmp_path / "duplicate.tar.gz"
    members = _required_members()
    _write_archive(duplicate, members + [("main.py", b"second")])
    with pytest.raises(DeployArchiveError, match="duplicate archive member"):
        inspect_archive(duplicate)


def test_archive_requires_runtime_entry_files(tmp_path: Path) -> None:
    archive = tmp_path / "missing.tar.gz"
    _write_archive(
        archive,
        [(name, content) for name, content in _required_members() if name != "config.yaml"],
    )

    with pytest.raises(DeployArchiveError, match="required package files are missing"):
        inspect_archive(archive)