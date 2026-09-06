import hashlib
import os
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import fsspec
import psycopg
import pytest

from nuron_ai import db
from nuron_ai.storage import ObjectStorage
from nuron_ai.watcher import iter_landable, scan

STABILITY_WINDOW = 30.0

# -- iter_landable: pure filesystem decisions, no Postgres/RustFS needed -----


def _age_file(path: Path, seconds_old: float) -> None:
    """Backdates a file's mtime by seconds_old so it reads as scan-stable."""
    old = time.time() - seconds_old
    os.utime(path, (old, old))


def test_iter_landable_yields_stable_supported_file(tmp_path: Path) -> None:
    target = tmp_path / "decision.md"
    target.write_text("# Subject\n")
    _age_file(target, STABILITY_WINDOW + 1)

    results = list(iter_landable(tmp_path, STABILITY_WINDOW))

    assert results == [(target, b"# Subject\n")]


def test_iter_landable_skips_file_not_yet_stable(tmp_path: Path) -> None:
    target = tmp_path / "mid-write.md"
    target.write_text("still writing")
    _age_file(target, STABILITY_WINDOW - 1)

    assert list(iter_landable(tmp_path, STABILITY_WINDOW)) == []


def test_iter_landable_ignores_unsupported_extension(tmp_path: Path) -> None:
    target = tmp_path / "notes.json"
    target.write_text("{}")
    _age_file(target, STABILITY_WINDOW + 1)

    assert list(iter_landable(tmp_path, STABILITY_WINDOW)) == []


def test_iter_landable_recurses_into_nested_directories(tmp_path: Path) -> None:
    nested = tmp_path / "nested" / "dir"
    nested.mkdir(parents=True)
    target = nested / "file.md"
    target.write_text("# Nested\n")
    _age_file(target, STABILITY_WINDOW + 1)

    results = list(iter_landable(tmp_path, STABILITY_WINDOW))

    assert results == [(target, b"# Nested\n")]


def test_iter_landable_skips_zero_byte_file(tmp_path: Path) -> None:
    target = tmp_path / "empty.md"
    target.write_bytes(b"")
    _age_file(target, STABILITY_WINDOW + 1)

    assert list(iter_landable(tmp_path, STABILITY_WINDOW)) == []


def test_iter_landable_skips_non_utf8_text_file(tmp_path: Path) -> None:
    target = tmp_path / "garbled.md"
    target.write_bytes(b"\xff\xfe not utf-8")
    _age_file(target, STABILITY_WINDOW + 1)

    assert list(iter_landable(tmp_path, STABILITY_WINDOW)) == []


def test_iter_landable_does_not_utf8_check_pdf(tmp_path: Path) -> None:
    target = tmp_path / "scan.pdf"
    data = b"%PDF-1.4\xff\xfebinary"
    target.write_bytes(data)
    _age_file(target, STABILITY_WINDOW + 1)

    assert list(iter_landable(tmp_path, STABILITY_WINDOW)) == [(target, data)]


def test_iter_landable_does_not_let_one_bad_file_block_the_rest(tmp_path: Path) -> None:
    good_before = tmp_path / "a-good.md"
    bad = tmp_path / "b-bad.md"
    good_after = tmp_path / "c-good.md"
    good_before.write_text("# Good before\n")
    bad.write_bytes(b"\xff\xfe not utf-8")
    good_after.write_text("# Good after\n")
    for path in (good_before, bad, good_after):
        _age_file(path, STABILITY_WINDOW + 1)

    results = list(iter_landable(tmp_path, STABILITY_WINDOW))

    assert results == [
        (good_before, b"# Good before\n"),
        (good_after, b"# Good after\n"),
    ]


def test_iter_landable_skips_file_mutated_during_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "mutating.md"
    target.write_text("original")
    _age_file(target, STABILITY_WINDOW + 1)
    original_read = Path.read_bytes

    def read_then_mutate(self: Path) -> bytes:
        data = original_read(self)
        if self == target:
            self.write_text("torn write")
        return data

    monkeypatch.setattr(Path, "read_bytes", read_then_mutate)

    assert list(iter_landable(tmp_path, STABILITY_WINDOW)) == []


def test_iter_landable_skips_replaced_file_during_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "replaced.md"
    target.write_text("original")
    _age_file(target, STABILITY_WINDOW + 1)
    original_read = Path.read_bytes

    def read_then_replace(self: Path) -> bytes:
        data = original_read(self)
        if self == target:
            self.unlink()
            self.write_text("original")
            _age_file(self, STABILITY_WINDOW + 1)
        return data

    monkeypatch.setattr(Path, "read_bytes", read_then_replace)

    assert list(iter_landable(tmp_path, STABILITY_WINDOW)) == []


def test_iter_landable_skips_when_restat_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "vanished.md"
    target.write_text("original")
    _age_file(target, STABILITY_WINDOW + 1)
    original_read = Path.read_bytes

    def read_then_delete(self: Path) -> bytes:
        data = original_read(self)
        if self == target:
            self.unlink()
        return data

    monkeypatch.setattr(Path, "read_bytes", read_then_delete)

    assert list(iter_landable(tmp_path, STABILITY_WINDOW)) == []


def test_iter_landable_skips_when_mtime_changes_during_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "rewritten.md"
    target.write_text("original")
    _age_file(target, STABILITY_WINDOW + 1)
    original_read = Path.read_bytes

    def read_then_touch(self: Path) -> bytes:
        data = original_read(self)
        if self == target:
            now = time.time()
            os.utime(self, (now, now))
        return data

    monkeypatch.setattr(Path, "read_bytes", read_then_touch)

    assert list(iter_landable(tmp_path, STABILITY_WINDOW)) == []


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="permission bits unenforced")
def test_iter_landable_skips_unreadable_file(tmp_path: Path) -> None:
    target = tmp_path / "locked.md"
    target.write_text("# Secret\n")
    _age_file(target, STABILITY_WINDOW + 1)
    target.chmod(0o000)

    try:
        assert list(iter_landable(tmp_path, STABILITY_WINDOW)) == []
    finally:
        target.chmod(0o644)


# -- scan: lands rows in Postgres, gated behind real infra -------------------


@pytest.fixture
def memory_storage() -> ObjectStorage:
    fs = fsspec.filesystem("memory")
    return ObjectStorage(fs=fs, root="/nuron-watcher-test")


@pytest.fixture
def db_conn() -> Iterator[psycopg.Connection]:
    if not os.environ.get("POSTGRES_INTEGRATION_TESTS"):
        pytest.skip("POSTGRES_INTEGRATION_TESTS not set -- skipping Postgres integration tests")
    conn = db.from_env()
    try:
        yield conn
    finally:
        conn.close()


def _row_for(conn: psycopg.Connection, digest: str) -> tuple[object, ...] | None:
    cursor = conn.execute(
        "SELECT entry_point, original_filename, state FROM nuron_ai.documents WHERE content_hash = %s",
        (digest,),
    )
    return cursor.fetchone()


def _cleanup(conn: psycopg.Connection, digest: str) -> None:
    conn.execute("DELETE FROM nuron_ai.documents WHERE content_hash = %s", (digest,))
    conn.commit()


def test_scan_lands_new_file_as_landed_row(
    tmp_path: Path, memory_storage: ObjectStorage, db_conn: psycopg.Connection
) -> None:
    target = tmp_path / "decision.md"
    data = f"# A decision {uuid.uuid4().hex}\n".encode()
    target.write_bytes(data)
    _age_file(target, STABILITY_WINDOW + 1)
    digest = hashlib.sha256(data).hexdigest()

    try:
        scan(tmp_path, memory_storage, db_conn, STABILITY_WINDOW)

        row = _row_for(db_conn, digest)
        assert row == ("watched_directory", "decision.md", "landed")
        assert memory_storage.get(f"{digest[:2]}/{digest}") == data
    finally:
        _cleanup(db_conn, digest)


def test_scan_rescan_of_unchanged_file_is_noop(
    tmp_path: Path, memory_storage: ObjectStorage, db_conn: psycopg.Connection
) -> None:
    target = tmp_path / "decision.md"
    data = f"# Decision {uuid.uuid4().hex}\n".encode()
    target.write_bytes(data)
    _age_file(target, STABILITY_WINDOW + 1)
    digest = hashlib.sha256(data).hexdigest()

    try:
        scan(tmp_path, memory_storage, db_conn, STABILITY_WINDOW)
        scan(tmp_path, memory_storage, db_conn, STABILITY_WINDOW)

        count = db_conn.execute(
            "SELECT count(*) FROM nuron_ai.documents WHERE content_hash = %s", (digest,)
        ).fetchone()
        assert count == (1,)
    finally:
        _cleanup(db_conn, digest)


def test_scan_same_hash_different_filename_converges_on_one_row(
    tmp_path: Path, memory_storage: ObjectStorage, db_conn: psycopg.Connection
) -> None:
    data = f"# Decision {uuid.uuid4().hex}\n".encode()
    digest = hashlib.sha256(data).hexdigest()
    first = tmp_path / "original-name.md"
    second_dir = tmp_path / "nested"
    second_dir.mkdir()
    second = second_dir / "duplicate-name.md"
    first.write_bytes(data)
    second.write_bytes(data)
    _age_file(first, STABILITY_WINDOW + 1)
    _age_file(second, STABILITY_WINDOW + 1)

    try:
        scan(tmp_path, memory_storage, db_conn, STABILITY_WINDOW)

        count = db_conn.execute(
            "SELECT count(*) FROM nuron_ai.documents WHERE content_hash = %s", (digest,)
        ).fetchone()
        assert count == (1,)
    finally:
        _cleanup(db_conn, digest)
