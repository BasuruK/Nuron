import hashlib
import os
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import MagicMock, call

import fsspec
import psycopg
import pytest

from nuron_ai import db, watcher
from nuron_ai.storage import ObjectStorage
from nuron_ai.watcher import iter_landable, scan

STABILITY_WINDOW = 30.0
MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024
RUNNING_AS_ROOT = getattr(os, "geteuid", lambda: -1)() == 0

# -- iter_landable: pure filesystem decisions, no Postgres/RustFS needed -----


def _age_file(path: Path, seconds_old: float) -> None:
    """Backdates a file's mtime by seconds_old so it reads as scan-stable."""
    old = time.time() - seconds_old
    os.utime(path, (old, old))


def test_iter_landable_yields_stable_supported_file(tmp_path: Path) -> None:
    target = tmp_path / "decision.md"
    target.write_bytes(b"# Subject\n")
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
    target.write_bytes(b"# Nested\n")
    _age_file(target, STABILITY_WINDOW + 1)

    results = list(iter_landable(tmp_path, STABILITY_WINDOW))

    assert results == [(target, b"# Nested\n")]


def test_iter_landable_skips_symlink_to_file_outside_root(tmp_path: Path) -> None:
    watched = tmp_path / "watched"
    watched.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("# Outside\n")
    _age_file(outside, STABILITY_WINDOW + 1)
    link = watched / "linked.md"
    try:
        link.symlink_to(outside)
    except OSError as err:
        pytest.skip(f"symlinks unavailable: {err}")

    assert list(iter_landable(watched, STABILITY_WINDOW)) == []


@pytest.mark.skipif(
    not hasattr(os, "mkfifo") or not hasattr(os, "O_NONBLOCK"),
    reason="FIFO non-blocking open unavailable",
)
def test_iter_landable_does_not_block_when_file_is_replaced_by_fifo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "replaced.md"
    target.write_text("# Original\n")
    _age_file(target, STABILITY_WINDOW + 1)
    original_open = os.open
    replaced = False

    def replace_then_open(
        path: str | bytes | os.PathLike[str], flags: int, *args: object, **kwargs: object
    ) -> int:
        nonlocal replaced
        if Path(path) == target and not replaced:
            replaced = True
            target.unlink()
            os.mkfifo(target)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(watcher.os, "open", replace_then_open)

    landed: list[tuple[Path, bytes]] | None = None
    error: BaseException | None = None
    done = threading.Event()

    def run() -> None:
        nonlocal landed, error
        try:
            landed = list(iter_landable(tmp_path, STABILITY_WINDOW))
        except BaseException as err:
            error = err
        finally:
            done.set()

    # Blocking FIFO open freezes the caller. Fail if that regresses.
    threading.Thread(target=run, daemon=True).start()
    if not done.wait(5):
        pytest.fail("iter_landable blocked after the file was replaced by a FIFO")
    if error is not None:
        raise error
    assert landed == []


def test_iter_landable_skips_zero_byte_file(tmp_path: Path) -> None:
    target = tmp_path / "empty.md"
    target.write_bytes(b"")
    _age_file(target, STABILITY_WINDOW + 1)

    assert list(iter_landable(tmp_path, STABILITY_WINDOW)) == []


def test_iter_landable_skips_oversized_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "oversized.pdf"
    with target.open("wb") as handle:
        handle.truncate(MAX_FILE_SIZE_BYTES + 1)
    _age_file(target, STABILITY_WINDOW + 1)

    def fail_read(_self: Path) -> bytes:
        raise AssertionError("oversized file must not be read")

    monkeypatch.setattr(Path, "read_bytes", fail_read)

    assert list(iter_landable(tmp_path, STABILITY_WINDOW)) == []


def test_iter_landable_bounds_read_when_file_grows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "growing.pdf"
    target.write_bytes(b"small")
    _age_file(target, STABILITY_WINDOW + 1)
    monkeypatch.setattr(watcher, "_MAX_FILE_SIZE_BYTES", 16)
    scripted_reads = iter([b"small" + b"x" * 11, b"x" * 9, b""])
    returned_sizes: list[int] = []
    grew = False

    def grow_then_read(file_descriptor: int, count: int) -> bytes:
        nonlocal grew
        if not grew:
            grew = True
            with target.open("ab") as handle:
                handle.write(b"x" * 20)
        data = next(scripted_reads)
        assert len(data) <= count
        returned_sizes.append(len(data))
        return data

    monkeypatch.setattr(Path, "read_bytes", lambda _self: pytest.fail("unbounded read"))
    monkeypatch.setattr(watcher.os, "read", grow_then_read)

    assert list(iter_landable(tmp_path, STABILITY_WINDOW)) == []
    assert sum(returned_sizes) <= watcher._MAX_FILE_SIZE_BYTES


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
    good_before.write_bytes(b"# Good before\n")
    bad.write_bytes(b"\xff\xfe not utf-8")
    good_after.write_bytes(b"# Good after\n")
    for path in (good_before, bad, good_after):
        _age_file(path, STABILITY_WINDOW + 1)

    results = list(iter_landable(tmp_path, STABILITY_WINDOW))

    assert results == [
        (good_before, b"# Good before\n"),
        (good_after, b"# Good after\n"),
    ]


def test_iter_landable_skips_file_when_lstat_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unreadable = tmp_path / "a-unreadable.md"
    good = tmp_path / "b-good.md"
    unreadable.write_bytes(b"# Unreadable\n")
    good.write_bytes(b"# Good\n")
    _age_file(good, STABILITY_WINDOW + 1)
    original_lstat = Path.lstat

    def lstat(self: Path) -> os.stat_result:
        if self == unreadable:
            raise PermissionError("permission denied")
        return original_lstat(self)

    monkeypatch.setattr(Path, "lstat", lstat)

    assert list(iter_landable(tmp_path, STABILITY_WINDOW)) == [(good, b"# Good\n")]


def test_iter_landable_skips_file_mutated_during_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "mutating.md"
    target.write_text("original")
    _age_file(target, STABILITY_WINDOW + 1)
    original_read = os.read
    mutated = False

    def read_then_mutate(file_descriptor: int, count: int) -> bytes:
        nonlocal mutated
        data = original_read(file_descriptor, count)
        if not mutated:
            mutated = True
            target.write_text("torn write")
        return data

    monkeypatch.setattr(watcher.os, "read", read_then_mutate)

    assert list(iter_landable(tmp_path, STABILITY_WINDOW)) == []


def test_iter_landable_skips_replaced_file_during_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "replaced.md"
    target.write_text("original")
    _age_file(target, STABILITY_WINDOW + 1)
    original_read = os.read
    original_lstat = Path.lstat
    original_stat = target.lstat()
    replacement_stat = MagicMock(
        spec=os.stat_result,
        st_dev=original_stat.st_dev + 1,
        st_ino=original_stat.st_ino + 1,
        st_size=original_stat.st_size,
        st_mtime_ns=original_stat.st_mtime_ns,
    )
    read_completed = False

    def read_then_replace(file_descriptor: int, count: int) -> bytes:
        nonlocal read_completed
        data = original_read(file_descriptor, count)
        read_completed = True
        return data

    def lstat_after_replacement(self: Path) -> os.stat_result:
        if self == target and read_completed:
            return replacement_stat
        return original_lstat(self)

    monkeypatch.setattr(watcher.os, "read", read_then_replace)
    monkeypatch.setattr(Path, "lstat", lstat_after_replacement)

    assert list(iter_landable(tmp_path, STABILITY_WINDOW)) == []


def test_iter_landable_skips_when_restat_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "vanished.md"
    target.write_text("original")
    _age_file(target, STABILITY_WINDOW + 1)
    original_lstat = Path.lstat
    target_lstat_calls = 0

    def fail_final_lstat(self: Path) -> os.stat_result:
        nonlocal target_lstat_calls
        if self == target:
            target_lstat_calls += 1
            if target_lstat_calls == 2:
                raise FileNotFoundError("file vanished after descriptor read")
        return original_lstat(self)

    monkeypatch.setattr(Path, "lstat", fail_final_lstat)

    assert list(iter_landable(tmp_path, STABILITY_WINDOW)) == []
    assert target_lstat_calls == 2


def test_iter_landable_skips_when_mtime_changes_during_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "rewritten.md"
    target.write_text("original")
    _age_file(target, STABILITY_WINDOW + 1)
    original_read = os.read
    touched = False

    def read_then_touch(file_descriptor: int, count: int) -> bytes:
        nonlocal touched
        data = original_read(file_descriptor, count)
        if not touched:
            touched = True
            now = time.time()
            os.utime(target, (now, now))
        return data

    monkeypatch.setattr(watcher.os, "read", read_then_touch)

    assert list(iter_landable(tmp_path, STABILITY_WINDOW)) == []


@pytest.mark.skipif(os.name == "nt" or RUNNING_AS_ROOT, reason="permission bits unenforced")
def test_iter_landable_skips_unreadable_file(tmp_path: Path) -> None:
    target = tmp_path / "locked.md"
    target.write_text("# Secret\n")
    _age_file(target, STABILITY_WINDOW + 1)
    target.chmod(0o000)

    try:
        assert list(iter_landable(tmp_path, STABILITY_WINDOW)) == []
    finally:
        target.chmod(0o644)


@pytest.mark.parametrize(
    ("failure_site", "failure"),
    [
        ("storage", OSError("RustFS unavailable")),
        ("connect", psycopg.OperationalError("PostgreSQL unavailable")),
        ("scan", psycopg.OperationalError("PostgreSQL unavailable")),
        ("scan", OSError("RustFS unavailable")),
    ],
)
def test_main_retries_after_transient_backend_failure(
    monkeypatch: pytest.MonkeyPatch, failure_site: str, failure: Exception
) -> None:
    monkeypatch.setenv("WATCHED_DIRECTORY", ".")
    monkeypatch.setenv("SCAN_INTERVAL_HOURS", "1")
    monkeypatch.setenv("MTIME_STABILITY_WINDOW_SECONDS", "30")
    storage_attempts = 0
    created_storages: list[object] = []
    connection = object()
    connection_attempts = 0
    scan_attempts = 0
    scan_storages: list[object] = []
    sleep_delays: list[float] = []

    def create_storage() -> object:
        nonlocal storage_attempts
        storage_attempts += 1
        if failure_site == "storage" and storage_attempts == 1:
            raise failure
        storage = object()
        created_storages.append(storage)
        return storage

    def connect() -> nullcontext[object]:
        nonlocal connection_attempts
        connection_attempts += 1
        if failure_site == "connect" and connection_attempts == 1:
            raise failure
        return nullcontext(connection)

    def run_scan(*args: object) -> None:
        nonlocal scan_attempts
        scan_attempts += 1
        scan_storages.append(args[1])
        if failure_site == "scan" and scan_attempts == 1:
            raise failure

    def sleep(seconds: float) -> None:
        sleep_delays.append(seconds)
        if len(sleep_delays) == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(watcher, "storage_from_env", create_storage)
    monkeypatch.setattr(db, "from_env", connect)
    monkeypatch.setattr(watcher, "scan", run_scan)
    monkeypatch.setattr(watcher.time, "sleep", sleep)

    with pytest.raises(KeyboardInterrupt):
        watcher.main()

    assert storage_attempts == 2
    assert connection_attempts == (1 if failure_site == "storage" else 2)
    if failure_site in {"storage", "connect"}:
        assert scan_attempts == 1
    else:
        assert scan_attempts == 2
        assert scan_storages == created_storages
    assert sleep_delays == [60.0, 3600.0]


def test_scan_continues_after_file_failure_then_raises(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    first_failing = tmp_path / "a-first-failing.md"
    first_valid = tmp_path / "b-first-valid.md"
    second_failing = tmp_path / "c-second-failing.md"
    second_valid = tmp_path / "d-second-valid.md"
    first_failing.write_bytes(b"# First failing\n")
    first_valid.write_bytes(b"# First valid\n")
    second_failing.write_bytes(b"# Second failing\n")
    second_valid.write_bytes(b"# Second valid\n")
    for path in (first_failing, first_valid, second_failing, second_valid):
        _age_file(path, STABILITY_WINDOW + 1)
    storage = MagicMock(spec=ObjectStorage)
    storage.put.side_effect = [
        OSError("RustFS rejected first file"),
        "stored",
        OSError("RustFS rejected second file"),
        "stored",
    ]
    conn = MagicMock(spec=psycopg.Connection)

    with pytest.raises(OSError, match="RustFS rejected first file") as raised:
        scan(tmp_path, storage, conn, STABILITY_WINDOW)

    assert storage.put.call_args_list == [
        call(b"# First failing\n"),
        call(b"# First valid\n"),
        call(b"# Second failing\n"),
        call(b"# Second valid\n"),
    ]
    landed_names = [execute_call.args[1][1] for execute_call in conn.execute.call_args_list]
    assert landed_names == [first_valid.name, second_valid.name]
    assert conn.commit.call_count == 2
    assert conn.rollback.call_count == 2
    assert raised.value.__notes__ == [
        f"failed to land watched file {first_failing}: OSError: RustFS rejected first file",
        f"failed to land watched file {second_failing}: OSError: RustFS rejected second file",
    ]
    assert caplog.records == []


def test_scan_lands_reachable_file_then_raises_traversal_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    failed_subtree = tmp_path / "a-failed"
    failed_subtree.mkdir()
    valid = tmp_path / "b-valid.md"
    data = b"# Valid\n"
    valid.write_bytes(data)
    _age_file(valid, STABILITY_WINDOW + 1)
    original_scandir = os.scandir
    original_path_scandir = getattr(Path, "_scandir")

    def scandir(path: str | os.PathLike[str]) -> Iterator[os.DirEntry[str]]:
        if Path(path) == failed_subtree:
            raise PermissionError(f"cannot enumerate {failed_subtree}")
        return original_scandir(path)

    def path_scandir(path: Path) -> Iterator[os.DirEntry[str]]:
        if path == failed_subtree:
            failed_scandir = MagicMock()
            failed_scandir.__enter__.return_value = failed_scandir
            failed_scandir.__iter__.side_effect = PermissionError(
                f"cannot enumerate {failed_subtree}"
            )
            return failed_scandir
        return original_path_scandir(path)

    monkeypatch.setattr(watcher.os, "scandir", scandir)
    monkeypatch.setattr(Path, "_scandir", path_scandir)
    storage = MagicMock(spec=ObjectStorage)
    conn = MagicMock(spec=psycopg.Connection)

    with pytest.raises(PermissionError) as raised:
        scan(tmp_path, storage, conn, STABILITY_WINDOW)

    assert str(failed_subtree) in str(raised.value)
    storage.put.assert_called_once_with(data)
    assert conn.execute.call_args.args[1][1] == valid.name
    conn.commit.assert_called_once_with()


def test_scan_retains_landing_and_deferred_traversal_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    failing = tmp_path / "a-failing.md"
    reachable = tmp_path / "b-reachable.md"
    failing_data = b"# Failing\n"
    reachable_data = b"# Reachable\n"

    def landable_files(
        _root: Path, _stability_window_seconds: float
    ) -> Iterator[tuple[Path, bytes]]:
        yield failing, failing_data
        yield reachable, reachable_data
        raise PermissionError("cannot enumerate deferred-subtree")

    monkeypatch.setattr(watcher, "iter_landable", landable_files)
    storage = MagicMock(spec=ObjectStorage)
    storage.put.side_effect = [OSError("RustFS rejected failing file"), "stored"]
    conn = MagicMock(spec=psycopg.Connection)

    with pytest.raises(OSError, match="RustFS rejected failing file") as raised:
        scan(tmp_path, storage, conn, STABILITY_WINDOW)

    storage.put.assert_has_calls([call(failing_data), call(reachable_data)])
    assert conn.execute.call_args.args[1][1] == reachable.name
    conn.commit.assert_called_once_with()
    conn.rollback.assert_called_once_with()
    assert raised.value.__notes__ == [
        f"failed to land watched file {failing}: OSError: RustFS rejected failing file",
        (
            "watched directory traversal also failed: "
            "PermissionError: cannot enumerate deferred-subtree"
        ),
    ]


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

        row = db_conn.execute(
            "SELECT entry_point, original_filename, state "
            "FROM nuron_ai.documents WHERE content_hash = %s",
            (digest,),
        ).fetchone()
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
