"""RustFS object storage: content-addressed originals via fsspec/s3fs (ADR-0005).

Backend is a URI (RUSTFS_URI/RUSTFS_ENDPOINT_URL), so MinIO/S3 are drop-in if RustFS's
Beta status bites -- see docs/tracer-bullet-01.md's Object storage row.
"""

import os
import uuid
from contextlib import suppress
from dataclasses import dataclass

import fsspec
from fsspec.spec import AbstractFileSystem

from nuron_ai.core import content_hash, object_key


class CorruptedWriteError(RuntimeError):
    """Raised when a stored object's read-back hash does not match what was put."""


@dataclass(frozen=True)
class ObjectStorage:
    """Content-addressed put/get over an fsspec filesystem rooted at one bucket."""

    fs: AbstractFileSystem
    root: str

    def put(self, data: bytes) -> str:
        """Stages bytes at a request-unique key, then publishes to the hash path if absent."""
        digest = content_hash(data)
        key = object_key(digest)
        path = f"{self.root}/{key}"
        if self.fs.exists(path):
            return self._ack(key, digest)

        staging_root = f"{self.root}/.staging/{uuid.uuid4().hex}"
        staging = f"{staging_root}/{key}"
        try:
            with self.fs.open(staging, "wb") as handle:
                handle.write(data)
            with self.fs.open(staging, "rb") as handle:
                staged = handle.read()
            if content_hash(staged) != digest:
                raise CorruptedWriteError(f"read-back hash mismatch for key {key!r}")
            try:
                self.fs.pipe_file(path, staged, mode="create")
            except FileExistsError:
                pass
            return self._ack(key, digest)
        finally:
            with suppress(Exception):
                if self.fs.exists(staging_root):
                    self.fs.rm(staging_root, recursive=True)

    def get(self, key: str) -> bytes:
        """Reads back the bytes stored at key."""
        with self.fs.open(f"{self.root}/{key}", "rb") as handle:
            return handle.read()

    def _ack(self, key: str, digest: str) -> str:
        """Returns key if the published object hashes to digest; never deletes it on mismatch."""
        stored = self.get(key)
        if content_hash(stored) != digest:
            raise CorruptedWriteError(f"read-back hash mismatch for key {key!r}")
        return key


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
