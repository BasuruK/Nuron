import hashlib
import os
import uuid
from collections.abc import Iterator

import fsspec
import pytest

from nuron_ai.storage import CorruptedWriteError, ObjectStorage, from_uri

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


def test_put_retries_after_failed_write(
    memory_storage: ObjectStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = b"retry me"
    original_open = memory_storage.fs.open
    failed = {"once": False}

    def flaky_open(path: str, mode: str = "rb", **kwargs: object) -> object:
        handle = original_open(path, mode, **kwargs)
        if "w" in mode and not failed["once"]:
            failed["once"] = True

            class Boom:
                def write(self, _data: bytes) -> int:
                    handle.write(b"partial")
                    raise OSError("network blip")

                def __enter__(self) -> object:
                    return self

                def __exit__(self, *_args: object) -> None:
                    handle.close()

            return Boom()
        return handle

    monkeypatch.setattr(memory_storage.fs, "open", flaky_open)
    with pytest.raises(OSError, match="network blip"):
        memory_storage.put(data)

    monkeypatch.setattr(memory_storage.fs, "open", original_open)
    key = memory_storage.put(data)
    assert memory_storage.get(key) == data


# -- round-trip test against the compose RustFS (issue #18 definition of done) ----


@pytest.fixture(scope="module")
def rustfs_storage() -> Iterator[ObjectStorage]:
    if not os.environ.get("RUSTFS_INTEGRATION_TESTS"):
        pytest.skip("RUSTFS_INTEGRATION_TESTS not set -- skipping RustFS integration tests")

    # Per-process prefix under pytest-integration so overlapping pytest runs
    # cannot wipe each other's objects. Teardown deletes only this root.
    storage = from_uri(
        f"{os.environ['RUSTFS_URI']}/pytest-integration/{uuid.uuid4().hex}",
        endpoint_url=os.environ["RUSTFS_ENDPOINT_URL"],
        key=os.environ["RUSTFS_ACCESS_KEY"],
        secret=os.environ["RUSTFS_SECRET_KEY"],
    )
    try:
        yield storage
    finally:
        if storage.fs.exists(storage.root):
            storage.fs.rm(storage.root, recursive=True)


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
