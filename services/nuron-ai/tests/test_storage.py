import hashlib
import os
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

import fsspec
import pytest

from nuron_ai.storage import CleanupError, CorruptedWriteError, ObjectStorage, from_uri

# -- fast logic tests, against an in-memory filesystem -----------------------


def _staging_files(storage: ObjectStorage) -> list[str]:
    staging = f"{storage.root}/.staging"
    if not storage.fs.exists(staging):
        return []
    return storage.fs.find(staging)


@pytest.fixture
def memory_storage() -> ObjectStorage:
    fs = fsspec.filesystem("memory")
    return ObjectStorage(fs=fs, root=f"/nuron-test/{uuid.uuid4().hex}")


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


def test_get_raises_when_stored_bytes_are_corrupted(memory_storage: ObjectStorage) -> None:
    data = b"get corruption probe"
    key = memory_storage.put(data)
    memory_storage.fs.pipe_file(f"{memory_storage.root}/{key}", b"corrupted garbage")

    with pytest.raises(CorruptedWriteError):
        memory_storage.get(key)

    memory_storage.fs.pipe_file(f"{memory_storage.root}/{key}", data)
    assert memory_storage.get(key) == data


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


def test_failed_put_does_not_delete_another_writers_published_object(
    memory_storage: ObjectStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = b"survivor"
    key = memory_storage.put(data)
    published = f"{memory_storage.root}/{key}"
    original_exists = memory_storage.fs.exists
    original_open = memory_storage.fs.open

    def exists_hiding_published(path: str) -> bool:
        if path == published:
            return False
        return original_exists(path)

    def fail_on_write(path: str, mode: str = "rb", **kwargs: object) -> object:
        if "w" in mode:
            raise OSError("write failed")
        return original_open(path, mode, **kwargs)

    monkeypatch.setattr(memory_storage.fs, "exists", exists_hiding_published)
    monkeypatch.setattr(memory_storage.fs, "open", fail_on_write)

    with pytest.raises(OSError, match="write failed"):
        memory_storage.put(data)

    monkeypatch.setattr(memory_storage.fs, "exists", original_exists)
    monkeypatch.setattr(memory_storage.fs, "open", original_open)
    assert memory_storage.get(key) == data
    assert _staging_files(memory_storage) == []


def test_failed_staging_write_leaves_no_staging_object(
    memory_storage: ObjectStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = b"no leftovers"
    original_open = memory_storage.fs.open

    def fail_on_write(path: str, mode: str = "rb", **kwargs: object) -> object:
        if "w" in mode:
            handle = original_open(path, mode, **kwargs)

            class Boom:
                def write(self, _data: bytes) -> int:
                    handle.write(b"partial")
                    raise OSError("staging blip")

                def __enter__(self) -> object:
                    return self

                def __exit__(self, *_args: object) -> None:
                    handle.close()

            return Boom()
        return original_open(path, mode, **kwargs)

    monkeypatch.setattr(memory_storage.fs, "open", fail_on_write)
    with pytest.raises(OSError, match="staging blip"):
        memory_storage.put(data)

    monkeypatch.setattr(memory_storage.fs, "open", original_open)
    assert _staging_files(memory_storage) == []
    digest = hashlib.sha256(data).hexdigest()
    published = f"{memory_storage.root}/{digest[:2]}/{digest}"
    assert not memory_storage.fs.exists(published)


def test_concurrent_puts_of_same_bytes_keep_published_object(
    memory_storage: ObjectStorage,
) -> None:
    data = os.urandom(256)
    digest = hashlib.sha256(data).hexdigest()
    expected_key = f"{digest[:2]}/{digest}"

    with ThreadPoolExecutor(max_workers=8) as pool:
        keys = list(pool.map(lambda _: memory_storage.put(data), range(8)))

    assert keys == [expected_key] * 8
    assert memory_storage.get(expected_key) == data
    assert _staging_files(memory_storage) == []


def test_failed_ack_of_owned_create_leaves_object_so_retry_recovers(
    memory_storage: ObjectStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = b"owned create"
    digest = hashlib.sha256(data).hexdigest()
    published = f"{memory_storage.root}/{digest[:2]}/{digest}"
    original_open = memory_storage.fs.open

    def corrupt_published_read(path: str, mode: str = "rb", **kwargs: object) -> object:
        handle = original_open(path, mode, **kwargs)
        if path == published and "r" in mode and "w" not in mode:

            class Bad:
                def read(self) -> bytes:
                    return b"garbage"

                def __enter__(self) -> object:
                    return self

                def __exit__(self, *_args: object) -> None:
                    handle.close()

            return Bad()
        return handle

    monkeypatch.setattr(memory_storage.fs, "open", corrupt_published_read)
    with pytest.raises(CorruptedWriteError):
        memory_storage.put(data)

    monkeypatch.setattr(memory_storage.fs, "open", original_open)
    assert memory_storage.fs.exists(published)
    assert _staging_files(memory_storage) == []
    key = memory_storage.put(data)
    assert memory_storage.get(key) == data


def test_failed_exclusive_create_leaves_no_object_so_retry_recovers(
    memory_storage: ObjectStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = b"partial publish"
    digest = hashlib.sha256(data).hexdigest()
    published = f"{memory_storage.root}/{digest[:2]}/{digest}"
    original_pipe = memory_storage.fs.pipe_file

    def boom_on_create(
        path: str, value: bytes, mode: str = "overwrite", **kwargs: object
    ) -> object:
        if path == published and mode == "create":
            raise OSError("network blip")
        return original_pipe(path, value, mode=mode, **kwargs)

    monkeypatch.setattr(memory_storage.fs, "pipe_file", boom_on_create)
    with pytest.raises(OSError, match="network blip"):
        memory_storage.put(data)

    monkeypatch.setattr(memory_storage.fs, "pipe_file", original_pipe)
    assert not memory_storage.fs.exists(published)
    key = memory_storage.put(data)
    assert memory_storage.get(key) == data


def test_failed_concurrent_publish_does_not_remove_winner(
    memory_storage: ObjectStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = b"winner stays"
    digest = hashlib.sha256(data).hexdigest()
    key = f"{digest[:2]}/{digest}"
    published = f"{memory_storage.root}/{key}"
    original_exists = memory_storage.fs.exists
    original_pipe = memory_storage.fs.pipe_file
    hidden = {"used": False}

    memory_storage.put(data)

    def exists_hide_first_published(path: str) -> bool:
        if path == published and not hidden["used"]:
            hidden["used"] = True
            return False
        return original_exists(path)

    def fail_second_create(
        path: str, value: bytes, mode: str = "overwrite", **kwargs: object
    ) -> object:
        if path == published and mode == "create":
            raise OSError("loser publish failed")
        return original_pipe(path, value, mode=mode, **kwargs)

    monkeypatch.setattr(memory_storage.fs, "exists", exists_hide_first_published)
    monkeypatch.setattr(memory_storage.fs, "pipe_file", fail_second_create)
    with pytest.raises(OSError, match="loser publish failed"):
        memory_storage.put(data)

    monkeypatch.setattr(memory_storage.fs, "exists", original_exists)
    monkeypatch.setattr(memory_storage.fs, "pipe_file", original_pipe)
    assert memory_storage.get(key) == data
    assert _staging_files(memory_storage) == []


def test_cleanup_retries_rm_then_propagates_write_error(
    memory_storage: ObjectStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = b"retry cleanup"
    original_open = memory_storage.fs.open
    original_rm = memory_storage.fs.rm
    rm_calls = {"n": 0}

    def fail_on_write(path: str, mode: str = "rb", **kwargs: object) -> object:
        if "w" in mode:
            handle = original_open(path, mode, **kwargs)

            class Boom:
                def write(self, _data: bytes) -> int:
                    handle.write(b"partial")
                    raise OSError("staging blip")

                def __enter__(self) -> object:
                    return self

                def __exit__(self, *_args: object) -> None:
                    handle.close()

            return Boom()
        return original_open(path, mode, **kwargs)

    def flaky_rm(path: str, *args: object, **kwargs: object) -> object:
        rm_calls["n"] += 1
        if rm_calls["n"] < 3:
            raise OSError("rm blip")
        return original_rm(path, *args, **kwargs)

    monkeypatch.setattr(memory_storage.fs, "open", fail_on_write)
    monkeypatch.setattr(memory_storage.fs, "rm", flaky_rm)
    with pytest.raises(OSError, match="staging blip"):
        memory_storage.put(data)

    monkeypatch.setattr(memory_storage.fs, "open", original_open)
    monkeypatch.setattr(memory_storage.fs, "rm", original_rm)
    assert _staging_files(memory_storage) == []
    key = memory_storage.put(data)
    assert memory_storage.get(key) == data


def test_cleanup_exhausted_raises_cleanup_error_from_rm_failure(
    memory_storage: ObjectStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = b"cleanup exhausted"
    original_open = memory_storage.fs.open

    def fail_on_write(path: str, mode: str = "rb", **kwargs: object) -> object:
        if "w" in mode:
            handle = original_open(path, mode, **kwargs)

            class Boom:
                def write(self, _data: bytes) -> int:
                    handle.write(b"partial")
                    raise OSError("staging blip")

                def __enter__(self) -> object:
                    return self

                def __exit__(self, *_args: object) -> None:
                    handle.close()

            return Boom()
        return original_open(path, mode, **kwargs)

    def fail_rm(*_args: object, **_kwargs: object) -> None:
        raise OSError("rm failed")

    monkeypatch.setattr(memory_storage.fs, "open", fail_on_write)
    monkeypatch.setattr(memory_storage.fs, "rm", fail_rm)
    with pytest.raises(CleanupError) as caught:
        memory_storage.put(data)

    assert isinstance(caught.value.__cause__, OSError)
    assert "rm failed" in str(caught.value.__cause__)
    assert "rm failed" in str(caught.value)
    assert isinstance(caught.value.__context__, OSError)
    assert "staging blip" in str(caught.value.__context__)


def test_successful_publish_cleanup_failure_raises_cleanup_error(
    memory_storage: ObjectStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = b"published"
    digest = hashlib.sha256(data).hexdigest()
    key = f"{digest[:2]}/{digest}"
    original_rm = memory_storage.fs.rm

    def fail_staging_rm(path: str, *args: object, **kwargs: object) -> object:
        if ".staging" in path:
            raise OSError("staging rm failed")
        return original_rm(path, *args, **kwargs)

    monkeypatch.setattr(memory_storage.fs, "rm", fail_staging_rm)
    with pytest.raises(CleanupError) as caught:
        memory_storage.put(data)

    assert isinstance(caught.value.__cause__, OSError)
    assert "staging rm failed" in str(caught.value.__cause__)
    assert "staging rm failed" in str(caught.value)
    monkeypatch.setattr(memory_storage.fs, "rm", original_rm)
    assert memory_storage.get(key) == data


def test_failed_ack_does_not_delete_key_a_peer_already_read(
    memory_storage: ObjectStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = b"shared object"
    digest = hashlib.sha256(data).hexdigest()
    key = f"{digest[:2]}/{digest}"
    published = f"{memory_storage.root}/{key}"
    original_open = memory_storage.fs.open
    original_pipe = memory_storage.fs.pipe_file
    published_create_done = {"value": False}

    def pipe_then_peer_ack(
        path: str, value: bytes, mode: str = "overwrite", **kwargs: object
    ) -> object:
        result = original_pipe(path, value, mode=mode, **kwargs)
        if path == published and mode == "create":
            assert memory_storage.get(key) == data
            published_create_done["value"] = True
        return result

    def corrupt_ack_read(path: str, mode: str = "rb", **kwargs: object) -> object:
        handle = original_open(path, mode, **kwargs)
        if (
            published_create_done["value"]
            and path == published
            and "r" in mode
            and "w" not in mode
        ):

            class Bad:
                def read(self) -> bytes:
                    return b"garbage"

                def __enter__(self) -> object:
                    return self

                def __exit__(self, *_args: object) -> None:
                    handle.close()

            return Bad()
        return handle

    monkeypatch.setattr(memory_storage.fs, "pipe_file", pipe_then_peer_ack)
    monkeypatch.setattr(memory_storage.fs, "open", corrupt_ack_read)
    with pytest.raises(CorruptedWriteError):
        memory_storage.put(data)

    monkeypatch.setattr(memory_storage.fs, "pipe_file", original_pipe)
    monkeypatch.setattr(memory_storage.fs, "open", original_open)
    assert memory_storage.get(key) == data
    assert _staging_files(memory_storage) == []



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
