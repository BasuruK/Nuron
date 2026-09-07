"""Watcher and Landing Zone: scheduled scan of the watched directory (NU-005).

Recursive, mtime-gated scan; dedupe is by content hash, never filename. Lands each new file
as a bare row at state 'landed' -- format extraction and header parsing are NU-006's job
(docs/tracer-bullet-01.md "Pipeline states").
"""

import logging
import os
import time
from collections.abc import Iterator
from pathlib import Path

import psycopg

from nuron_ai import db
from nuron_ai.core import content_hash
from nuron_ai.storage import ObjectStorage, from_env as storage_from_env

logger = logging.getLogger(__name__)

_SUPPORTED_EXTENSIONS = {".md", ".txt", ".docx", ".pdf"}
_TEXT_EXTENSIONS = {".md", ".txt"}
_MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024
_RETRY_DELAY_SECONDS = 60.0


def iter_landable(root: Path, stability_window_seconds: float) -> Iterator[tuple[Path, bytes]]:
    """Yields (path, bytes) for each stable, readable, supported file under root.

    Unreadable, oversized, zero-byte, and non-UTF-8 text files are logged and skipped.
    """
    now = time.time()

    for path in sorted(root.rglob("*")):
        try:
            is_file = path.is_file()
        except OSError as err:
            logger.warning("skipping unreadable file %s: %s", path, err)
            continue

        if not is_file or path.suffix.lower() not in _SUPPORTED_EXTENSIONS:
            continue

        try:
            stat = path.stat()
        except OSError as err:
            logger.warning("skipping unreadable file %s: %s", path, err)
            continue

        if stat.st_size > _MAX_FILE_SIZE_BYTES:
            logger.warning("skipping oversized file %s: %d bytes", path, stat.st_size)
            continue

        if now - stat.st_mtime < stability_window_seconds:
            continue  # not yet stable; a candidate for a later scan

        try:
            data = path.read_bytes()
        except OSError as err:
            logger.warning("skipping unreadable file %s: %s", path, err)
            continue

        try:
            restat = path.stat()
        except OSError as err:
            logger.warning("skipping unreadable file %s: %s", path, err)
            continue

        # Skip if the file was replaced or rewritten between the first stat and this one.
        identity_changed = restat.st_dev != stat.st_dev or restat.st_ino != stat.st_ino
        size_changed = restat.st_size != stat.st_size
        mtime_changed = restat.st_mtime_ns != stat.st_mtime_ns
        if identity_changed or size_changed or mtime_changed:
            continue

        if len(data) == 0:
            logger.warning("skipping zero-byte file %s", path)
            continue

        if path.suffix.lower() in _TEXT_EXTENSIONS:
            try:
                data.decode("utf-8")
            except UnicodeDecodeError as err:
                logger.warning("skipping non-UTF-8 file %s: %s", path, err)
                continue

        yield path, data


def scan(
    root: Path,
    storage: ObjectStorage,
    conn: psycopg.Connection,
    stability_window_seconds: float,
) -> None:
    """Lands every stable candidate under root: RustFS write, then a conflict-safe insert.

    Object write happens before the Landing Zone row (docs/adr/0005-content-hash-identity.md):
    a crash between the two leaves an unreferenced object and no row, which the next scan or
    upload of the same bytes silently repairs.
    """
    for path, data in iter_landable(root, stability_window_seconds):
        storage.put(data)
        conn.execute(
            """
            INSERT INTO nuron_ai.documents (content_hash, entry_point, original_filename)
            VALUES (%s, 'watched_directory', %s)
            ON CONFLICT (content_hash) DO NOTHING
            """,
            (content_hash(data), str(path.relative_to(root))),
        )
        conn.commit()


def main() -> None:
    """Runs scheduled scans forever, retrying scan failures after a bounded delay."""
    logging.basicConfig(level=logging.INFO)
    root = Path(os.environ["WATCHED_DIRECTORY"])
    interval_seconds = float(os.environ["SCAN_INTERVAL_HOURS"]) * 3600
    stability_window_seconds = float(os.environ["MTIME_STABILITY_WINDOW_SECONDS"])
    storage = storage_from_env()

    while True:
        try:
            with db.from_env() as conn:
                scan(root, storage, conn, stability_window_seconds)
        except Exception:
            logger.exception("watcher scan failed; retrying after %.0f seconds", _RETRY_DELAY_SECONDS)
            time.sleep(_RETRY_DELAY_SECONDS)
            continue
        time.sleep(interval_seconds)


if __name__ == "__main__":
    main()
