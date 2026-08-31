"""RustFS object storage: content-addressed originals via fsspec/s3fs (ADR-0005).

Backend is a URI (RUSTFS_URI/RUSTFS_ENDPOINT_URL), so MinIO/S3 are drop-in if RustFS's
Beta status bites -- see docs/tracer-bullet-01.md's Object storage row.
"""

import os
import time
from dataclasses import dataclass

import fsspec
from fsspec.spec import AbstractFileSystem

from nuron_ai.core import content_hash, object_key

_REMOVE_ATTEMPTS = 3
_REMOVE_RETRY_DELAY_S = 0.05


class CorruptedWriteError(RuntimeError):
    """Raised when a stored object's read-back hash does not match what was put."""


class CleanupError(RuntimeError):
    """Raised when put cannot remove objects this call created."""


@dataclass(frozen=True)
class ObjectStorage:
    """Content-addressed put/get over an fsspec filesystem rooted at one bucket."""

    fs: AbstractFileSystem
    root: str

    def put(self, data: bytes) -> str:
        """Publishes bytes at the content-hash key if absent; read-back hashes before ack."""
        digest = content_hash(data)
        key = object_key(digest)
        path = f"{self.root}/{key}"
        if self.fs.exists(path):
            try:
                return self._ack(key)
            except CorruptedWriteError:
                try:
                    self._remove(path)
                except Exception as remove_err:
                    raise CleanupError(
                        f"failed to remove objects for key {key!r}: {remove_err}"
                    ) from remove_err
                # Poisoned key: fall through and republish.

        try:
            try:
                # If-None-Match exclusive create. Delete only on ack hash-mismatch;
                # a transient ack error must not yank a peer's key.
                self.fs.pipe_file(path, data, mode="create")
            except FileExistsError:
                pass
            return self._ack(key)
        except CorruptedWriteError:
            try:
                self._remove(path)
            except Exception as remove_err:
                cleanup_err = remove_err
            else:
                raise
            raise CleanupError(
                f"failed to remove objects for key {key!r}: {cleanup_err}"
            ) from cleanup_err

    def get(self, key: str) -> bytes:
        """Reads bytes at key; raises if they don't hash to the digest encoded in key."""
        with self.fs.open(f"{self.root}/{key}", "rb") as handle:
            data = handle.read()
        digest = key.rsplit("/", 1)[-1]
        if content_hash(data) != digest:
            raise CorruptedWriteError(f"read-back hash mismatch for key {key!r}")
        return data

    def _ack(self, key: str) -> str:
        """Returns key once get() has read back and integrity-checked the object."""
        self.get(key)
        return key

    def _remove(self, path: str) -> None:
        """Deletes path if present; retries with a bounded delay, then raises the last error."""
        last: Exception | None = None
        for attempt in range(_REMOVE_ATTEMPTS):
            try:
                if self.fs.exists(path):
                    self.fs.rm(path, recursive=True)
                return
            except Exception as err:
                last = err
                if attempt + 1 < _REMOVE_ATTEMPTS:
                    time.sleep(_REMOVE_RETRY_DELAY_S)
        assert last is not None
        raise last


def from_uri(uri: str, **storage_options: object) -> ObjectStorage:
    """Resolves an fsspec filesystem + bucket root from a URI and ensures the bucket exists."""
    fs, root = fsspec.core.url_to_fs(uri, **storage_options)
    fs.makedirs(root, exist_ok=True)
    return ObjectStorage(fs=fs, root=root)


def from_env() -> ObjectStorage:
    """Builds ObjectStorage from RUSTFS_URI/RUSTFS_ENDPOINT_URL/RUSTFS_ACCESS_KEY/RUSTFS_SECRET_KEY."""
    return from_uri(
        os.environ["RUSTFS_URI"],
        endpoint_url=os.environ["RUSTFS_ENDPOINT_URL"],
        key=os.environ["RUSTFS_ACCESS_KEY"],
        secret=os.environ["RUSTFS_SECRET_KEY"],
    )
