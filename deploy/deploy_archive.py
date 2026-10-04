from __future__ import annotations

import os
import re
import sys
import tarfile
from pathlib import Path, PurePosixPath
from typing import List

MAX_ARCHIVE_MEMBERS = 10_000
MAX_EXPANDED_BYTES = 512 * 1024 * 1024
REQUIRED_FILES = {"main.py", "requirements.txt", "config.yaml", "filemind/__init__.py"}
_ALLOWED_ROOT_FILES = {"main.py", "requirements.txt", "config.yaml"}
_SAFE_PATH = re.compile(r"^[A-Za-z0-9_./-]+$")


class DeployArchiveError(ValueError):
    """Raised when a deployment package is malformed or unsafe."""


def _validate_members(members: List[tarfile.TarInfo]) -> List[tarfile.TarInfo]:
    if not members or len(members) > MAX_ARCHIVE_MEMBERS:
        raise DeployArchiveError("invalid archive member count")

    seen = set()
    found_files = set()
    expanded_bytes = 0
    for member in members:
        name = member.name.rstrip("/")
        path = PurePosixPath(name)
        if (
            not name
            or name.startswith("./")
            or path.is_absolute()
            or ".." in path.parts
            or "\\" in name
            or not _SAFE_PATH.fullmatch(name)
        ):
            raise DeployArchiveError(f"unsafe archive path: {member.name!r}")
        if name in seen:
            raise DeployArchiveError(f"duplicate archive member: {name}")
        seen.add(name)

        if member.isdir():
            if name != "filemind" and not name.startswith("filemind/"):
                raise DeployArchiveError(f"unexpected directory: {name}")
            continue
        if not member.isfile():
            raise DeployArchiveError(f"links and special files are not allowed: {name}")

        allowed = name in _ALLOWED_ROOT_FILES or (
            name.startswith("filemind/") and name.endswith(".py")
        )
        if not allowed:
            raise DeployArchiveError(f"unexpected package file: {name}")
        expanded_bytes += member.size
        if expanded_bytes > MAX_EXPANDED_BYTES:
            raise DeployArchiveError("expanded archive exceeds 512 MiB")
        found_files.add(name)

    missing = REQUIRED_FILES - found_files
    if missing:
        raise DeployArchiveError(
            f"required package files are missing: {', '.join(sorted(missing))}"
        )
    return members


def inspect_archive(archive_path: Path) -> List[str]:
    """Validate an archive and return its member names without extracting it."""
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            members = _validate_members(archive.getmembers())
    except (OSError, tarfile.TarError) as error:
        raise DeployArchiveError(f"could not read deployment archive: {error}") from error
    return [member.name for member in members]


def extract_verified_archive(archive_path: Path, destination: Path) -> None:
    """Validate and safely extract regular package files to an empty directory."""
    destination.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink() or any(destination.iterdir()):
        raise DeployArchiveError("extraction destination must be an empty real directory")
    destination_root = destination.resolve()

    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            members = _validate_members(archive.getmembers())
            for member in members:
                name = member.name.rstrip("/")
                target = destination.joinpath(*PurePosixPath(name).parts)
                resolved_target = target.resolve()
                if os.path.commonpath((destination_root, resolved_target)) != str(
                    destination_root
                ):
                    raise DeployArchiveError(f"archive path escapes destination: {name}")

                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    target.chmod(0o750)
                    continue

                target.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    raise DeployArchiveError(f"could not read package file: {name}")
                with source, target.open("xb") as output:
                    while block := source.read(1024 * 1024):
                        output.write(block)
                target.chmod(0o640)
    except (OSError, tarfile.TarError) as error:
        raise DeployArchiveError(f"could not extract deployment archive: {error}") from error


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: deploy_archive.py ARCHIVE DESTINATION", file=sys.stderr)
        return 2
    try:
        extract_verified_archive(Path(sys.argv[1]), Path(sys.argv[2]))
    except DeployArchiveError as error:
        print(f"filemind deploy archive: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())