import hashlib
from pathlib import Path

from filemind.storage.hash_store import HashStore


def test_compute_sha256_matches_python_hash(tmp_path: Path) -> None:
    content = b"hello world"
    file_path = tmp_path / "hello.txt"
    file_path.write_bytes(content)

    store = HashStore(db_path=tmp_path / "hash_store.db")
    hash_value = store.compute_sha256(file_path)
    expected = hashlib.sha256(content).hexdigest()

    assert hash_value == expected
    store.close()


def test_register_and_detect_duplicate(tmp_path: Path) -> None:
    content = b"duplicate content"
    file_path = tmp_path / "sample.txt"
    file_path.write_bytes(content)
    db_path = tmp_path / "hash_store.db"

    store = HashStore(db_path=db_path)

    assert not store.is_duplicate(file_path)
    store.register_file(file_path)
    assert store.is_duplicate(file_path)

    stats = store.get_statistics()
    assert stats["unique_hashes"] == 1
    assert stats["total_files"] == 1
    assert stats["duplicate_count"] == 0

    assert store.get_hash_by_path(file_path) == hashlib.sha256(content).hexdigest()
    store.close()


def test_delete_hash_removes_entry(tmp_path: Path) -> None:
    content = b"to delete"
    file_path = tmp_path / "delete.txt"
    file_path.write_bytes(content)
    db_path = tmp_path / "hash_store.db"

    store = HashStore(db_path=db_path)
    store.register_file(file_path)

    assert store.delete_hash(file_path)
    assert store.get_hash_by_path(file_path) is None
    store.close()
