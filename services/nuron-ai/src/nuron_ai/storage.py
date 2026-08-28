"""RustFS object storage: content-addressed originals via fsspec/s3fs (ADR-0005).

Backend is a URI (RUSTFS_URI/RUSTFS_ENDPOINT_URL), so MinIO/S3 are drop-in if RustFS's
Beta status bites -- see docs/tracer-bullet-01.md's Object storage row.
"""

import os
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
        """Writes data under its content-hash key if absent; verifies by read-back before returning."""
        digest = content_hash(data)
        key = object_key(digest)
        path = f"{self.root}/{key}"
        if not self.fs.exists(path):
            with self.fs.open(path, "wb") as handle:
                handle.write(data)
        stored = self.get(key)
        if content_hash(stored) != digest:
            raise CorruptedWriteError(f"read-back hash mismatch for key {key!r}")
        return key

    def get(self, key: str) -> bytes:
        """Reads back the bytes stored at key."""
        with self.fs.open(f"{self.root}/{key}", "rb") as handle:
            return handle.read()


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
