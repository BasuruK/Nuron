import hashlib
import os

import fsspec
import pytest

from nuron_ai.storage import CorruptedWriteError, ObjectStorage, from_env

# -- fast logic tests, against an in-memory filesystem -----------------------


@pytest.fixture
def memory_storage() -> ObjectStorage:
    fs = fsspec.filesystem("memory")
    return ObjectStorage(fs=fs, root="/nuron-test")


def test_put_returns_key_matching_content_hash(memory_storage: ObjectStorage) -> None:
    data = b"hello nuron"
    digest = hashlib.sha256(data).hexdigest()

    key = memory_storage.put(data)

    assert key == f"{digest[:2]}/{digest}"


def test_put_then_get_round_trips_identical_bytes(memory_storage: ObjectStorage) -> None:
    data = b"round trip me"

    key = memory_storage.put(data)

    assert memory_storage.get(key) == data


def test_second_put_of_existing_key_does_not_rewrite(
    memory_storage: ObjectStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = b"write me once"
    memory_storage.put(data)

    def fail_on_write(path: str, mode: str = "rb", **kwargs: object) -> object:
        if "w" in mode:
            raise AssertionError("put() rewrote an already-stored key")
        return original_open(path, mode, **kwargs)

    original_open = memory_storage.fs.open
    monkeypatch.setattr(memory_storage.fs, "open", fail_on_write)

    memory_storage.put(data)  # must not raise


def test_put_raises_when_stored_bytes_are_corrupted(memory_storage: ObjectStorage) -> None:
    data = b"trustworthy bytes"
    key = memory_storage.put(data)
    # Simulate corruption at rest (bit rot, a bad prior write) by writing directly,
    # bypassing put().
    memory_storage.fs.pipe_file(f"{memory_storage.root}/{key}", b"corrupted garbage")

    with pytest.raises(CorruptedWriteError):
        memory_storage.put(data)


# -- round-trip test against the compose RustFS (issue #18 definition of done) ----


@pytest.fixture(scope="module")
def rustfs_storage() -> ObjectStorage:
    try:
        return from_env()
    except KeyError as exc:
        pytest.skip(f"RustFS not configured: missing env var {exc}")
    except Exception as exc:  # RustFS not reachable (compose stack not running)
        pytest.skip(f"RustFS unreachable: {exc}")


def test_rustfs_round_trip_returns_identical_bytes_with_hash_matching_key(
    rustfs_storage: ObjectStorage,
) -> None:
    data = os.urandom(256)
    digest = hashlib.sha256(data).hexdigest()

    key = rustfs_storage.put(data)

    assert key == f"{digest[:2]}/{digest}"
    assert rustfs_storage.get(key) == data


def test_rustfs_detects_corrupted_write_rather_than_acking(
    rustfs_storage: ObjectStorage,
) -> None:
    data = os.urandom(256)
    key = rustfs_storage.put(data)
    rustfs_storage.fs.pipe_file(f"{rustfs_storage.root}/{key}", b"corrupted garbage")

    with pytest.raises(CorruptedWriteError):
        rustfs_storage.put(data)
