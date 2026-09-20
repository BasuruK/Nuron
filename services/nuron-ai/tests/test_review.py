"""Tests for review.py: review queue, edits, Reviewed Source versioning (NU-007)."""

import os
import threading
import time
import uuid
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import fsspec
import psycopg
import pytest
from psycopg import sql

from nuron_ai.core import content_hash
from nuron_ai.review import (
    approve,
    fetch_one,
    list_pending,
    promote_parsed,
    reingest_diff,
    save_edit,
)
from nuron_ai.storage import ObjectStorage


_SCHEMA_SQL = Path(__file__).resolve().parents[3] / "schema" / "schema.sql"


def _nuron_ai_ddl() -> str:
    """Returns schema.sql's nuron_ai DDL, skipping cluster-wide CREATE ROLE."""
    source = _SCHEMA_SQL.read_text()
    start = source.index("SET ROLE nuron_ai_svc;") + len("SET ROLE nuron_ai_svc;")
    end = source.index("\nRESET ROLE;")
    return source[start:end]


def _admin_connect(dbname: str) -> psycopg.Connection:
    """Connects as the compose bootstrap superuser to create/drop isolated test DBs."""
    return psycopg.connect(
        host=os.environ["NURON_AI_DB_HOST"],
        port=os.environ["NURON_AI_DB_PORT"],
        dbname=dbname,
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        autocommit=True,
        cursor_factory=psycopg.ClientCursor,
    )


@pytest.fixture(scope="module")
def review_test_db() -> Iterator[str]:
    """Creates a dedicated empty database with the nuron_ai schema; drops it after."""
    if not os.environ.get("POSTGRES_INTEGRATION_TESTS"):
        pytest.skip(
            "POSTGRES_INTEGRATION_TESTS not set -- skipping Postgres integration tests"
        )
    dbname = f"nuron_review_test_{uuid.uuid4().hex}"
    admin = _admin_connect(os.environ["POSTGRES_DB"])
    try:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
        setup = _admin_connect(dbname)
        try:
            setup.execute("CREATE SCHEMA nuron_ai AUTHORIZATION nuron_ai_svc")
            setup.execute("SET ROLE nuron_ai_svc")
            setup.execute(_nuron_ai_ddl())
        finally:
            setup.close()
        yield dbname
    finally:
        admin.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                sql.Identifier(dbname)
            )
        )
        admin.close()


@pytest.fixture
def db_conn(review_test_db: str) -> Iterator[psycopg.Connection]:
    """Connects to the dedicated review-test database with an empty claim queue."""
    conn = psycopg.connect(
        host=os.environ["NURON_AI_DB_HOST"],
        port=os.environ["NURON_AI_DB_PORT"],
        dbname=review_test_db,
        user="nuron_ai_svc",
        password=os.environ["NURON_AI_DB_PASSWORD"],
    )
    conn.execute("TRUNCATE TABLE nuron_ai.documents CASCADE")
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def memory_storage() -> ObjectStorage:
    return ObjectStorage(fs=fsspec.filesystem("memory"), root="/nuron-review-test")


def _awaiting_review(
    conn: psycopg.Connection,
    storage: ObjectStorage,
    data: bytes,
    filename: str,
    *,
    title: str | None = None,
    author: str | None = None,
    author_source: str | None = None,
    document_date: date | None = None,
    tags: list[str] | None = None,
) -> str:
    """Lands one row directly at awaiting_review, skipping the extraction/parsing workers."""
    digest = content_hash(data)
    storage.put(data)
    conn.execute(
        """
        INSERT INTO nuron_ai.documents
            (content_hash, entry_point, original_filename, state, title, author,
             author_source, document_date, tags, body)
        VALUES (%s, 'watched_directory', %s, 'awaiting_review', %s, %s, %s, %s, %s, %s)
        ON CONFLICT (content_hash) DO NOTHING
        """,
        (digest, filename, title, author, author_source, document_date, tags or [], data.decode("utf-8")),
    )
    conn.commit()
    return digest


def _parsed(conn: psycopg.Connection, storage: ObjectStorage, data: bytes, filename: str) -> str:
    """Lands one row directly at `parsed`, for testing promote_parsed."""
    digest = content_hash(data)
    storage.put(data)
    conn.execute(
        """
        INSERT INTO nuron_ai.documents (content_hash, entry_point, original_filename, state, body)
        VALUES (%s, 'watched_directory', %s, 'parsed', %s)
        ON CONFLICT (content_hash) DO NOTHING
        """,
        (digest, filename, data.decode("utf-8")),
    )
    conn.commit()
    return digest


def _cleanup(conn: psycopg.Connection, *digests: str) -> None:
    conn.execute("DELETE FROM nuron_ai.reviewed_sources WHERE content_hash = ANY(%s)", (list(digests),))
    conn.execute("DELETE FROM nuron_ai.documents WHERE content_hash = ANY(%s)", (list(digests),))
    conn.commit()


def test_list_pending_lists_awaiting_review_rows_oldest_first(
    memory_storage: ObjectStorage, db_conn: psycopg.Connection
) -> None:
    digest_a = _awaiting_review(db_conn, memory_storage, b"# A\n", "a.md")
    digest_b = _awaiting_review(db_conn, memory_storage, b"# B\n", "b.md")
    try:
        pending = list_pending(db_conn)
        assert [item.content_hash for item in pending] == [digest_a, digest_b]
    finally:
        _cleanup(db_conn, digest_a, digest_b)


def test_list_pending_pages_with_limit_and_offset(
    memory_storage: ObjectStorage, db_conn: psycopg.Connection
) -> None:
    digest_a = _awaiting_review(db_conn, memory_storage, b"# A\n", "a.md")
    digest_b = _awaiting_review(db_conn, memory_storage, b"# B\n", "b.md")
    digest_c = _awaiting_review(db_conn, memory_storage, b"# C\n", "c.md")
    digest_d = _awaiting_review(db_conn, memory_storage, b"# D\n", "d.md")
    try:
        first_page = list_pending(db_conn, limit=2, offset=0)
        second_page = list_pending(db_conn, limit=2, offset=2)
        assert [item.content_hash for item in first_page] == [digest_a, digest_b]
        assert [item.content_hash for item in second_page] == [digest_c, digest_d]
    finally:
        _cleanup(db_conn, digest_a, digest_b, digest_c, digest_d)


def test_fetch_one_claims_oldest_awaiting_review_row_and_returns_full_header(
    memory_storage: ObjectStorage, db_conn: psycopg.Connection
) -> None:
    digest = _awaiting_review(
        db_conn,
        memory_storage,
        b"# Decision\n\nBody text.\n",
        "decision.md",
        title="Decision",
        author="Basuru",
        author_source="extracted",
        document_date=date(2026, 5, 14),
        tags=["infra"],
    )
    try:
        item = fetch_one(db_conn, "reviewer-1")
        assert item is not None
        assert item.content_hash == digest
        assert item.title == "Decision"
        assert item.author == "Basuru"
        assert item.author_source == "extracted"
        assert item.document_date == date(2026, 5, 14)
        assert item.tags == ["infra"]
        assert item.body == "# Decision\n\nBody text.\n"

        row = db_conn.execute(
            "SELECT claimed_by, lease_token FROM nuron_ai.documents WHERE content_hash = %s",
            (digest,),
        ).fetchone()
        assert row == ("reviewer-1", item.lease_token)
    finally:
        _cleanup(db_conn, digest)


def test_fetch_one_leaves_a_claimed_row_for_another_worker_alone(
    memory_storage: ObjectStorage, db_conn: psycopg.Connection
) -> None:
    digest = _awaiting_review(db_conn, memory_storage, b"# Claimed\n", "claimed.md")
    db_conn.execute(
        """
        UPDATE nuron_ai.documents
        SET claimed_by = 'reviewer-a', lease_until = now() + interval '1 hour', lease_token = 1
        WHERE content_hash = %s
        """,
        (digest,),
    )
    db_conn.commit()
    try:
        assert fetch_one(db_conn, "reviewer-b") is None
    finally:
        _cleanup(db_conn, digest)


def test_save_edit_updates_fields_while_holding_the_lease(
    memory_storage: ObjectStorage, db_conn: psycopg.Connection
) -> None:
    digest = _awaiting_review(db_conn, memory_storage, b"# Draft\n", "draft.md")
    try:
        item = fetch_one(db_conn, "reviewer-1")
        assert item is not None

        saved = save_edit(
            db_conn,
            digest,
            "reviewer-1",
            item.lease_token,
            title="Final Title",
            author="Basuru",
            author_source="extracted",
            document_date=date(2026, 1, 1),
            tags=["reviewed"],
            body="# Draft\n\nEdited body.\n",
        )
        assert saved

        row = db_conn.execute(
            "SELECT title, body, claimed_by FROM nuron_ai.documents WHERE content_hash = %s",
            (digest,),
        ).fetchone()
        assert row == ("Final Title", "# Draft\n\nEdited body.\n", "reviewer-1")
    finally:
        _cleanup(db_conn, digest)


def test_save_edit_fails_when_lease_token_is_stale(
    memory_storage: ObjectStorage, db_conn: psycopg.Connection
) -> None:
    digest = _awaiting_review(db_conn, memory_storage, b"# Draft\n", "stale-save.md")
    try:
        item = fetch_one(db_conn, "reviewer-1")
        assert item is not None
        db_conn.execute(
            "UPDATE nuron_ai.documents SET lease_token = lease_token + 1 WHERE content_hash = %s",
            (digest,),
        )
        db_conn.commit()

        saved = save_edit(
            db_conn,
            digest,
            "reviewer-1",
            item.lease_token,
            title=None,
            author=None,
            author_source=None,
            document_date=None,
            tags=[],
            body="should not apply",
        )
        assert not saved

        row = db_conn.execute(
            "SELECT body FROM nuron_ai.documents WHERE content_hash = %s", (digest,)
        ).fetchone()
        assert row == ("# Draft\n",)
    finally:
        _cleanup(db_conn, digest)


def test_approve_freezes_first_version_and_transitions_state(
    memory_storage: ObjectStorage, db_conn: psycopg.Connection
) -> None:
    digest = _awaiting_review(
        db_conn, memory_storage, b"# Approved\n\nBody.\n", "approve-v1.md", title="Approved"
    )
    try:
        item = fetch_one(db_conn, "reviewer-1")
        assert item is not None

        version = approve(db_conn, digest, "reviewer-1", item.lease_token)
        assert version == 1

        state = db_conn.execute(
            "SELECT state, claimed_by FROM nuron_ai.documents WHERE content_hash = %s", (digest,)
        ).fetchone()
        assert state == ("content_approved", None)

        reviewed = db_conn.execute(
            "SELECT version, title, body FROM nuron_ai.reviewed_sources WHERE content_hash = %s",
            (digest,),
        ).fetchone()
        assert reviewed == (1, "Approved", "# Approved\n\nBody.\n")
    finally:
        _cleanup(db_conn, digest)


def test_approve_increments_version_on_second_ingest_of_same_filename(
    memory_storage: ObjectStorage, db_conn: psycopg.Connection
) -> None:
    digest_v1 = _awaiting_review(db_conn, memory_storage, b"# V1\n", "reingest.md")
    digest_v2 = _awaiting_review(db_conn, memory_storage, b"# V2\n", "reingest.md")
    try:
        item_v1 = fetch_one(db_conn, "reviewer-1")
        assert item_v1 is not None
        assert approve(db_conn, digest_v1, "reviewer-1", item_v1.lease_token) == 1

        item_v2 = fetch_one(db_conn, "reviewer-1")
        assert item_v2 is not None
        assert item_v2.content_hash == digest_v2
        assert approve(db_conn, digest_v2, "reviewer-1", item_v2.lease_token) == 2
    finally:
        _cleanup(db_conn, digest_v1, digest_v2)


def test_approve_fails_when_lease_lost(
    memory_storage: ObjectStorage, db_conn: psycopg.Connection
) -> None:
    digest = _awaiting_review(db_conn, memory_storage, b"# Lost lease\n", "lost-lease.md")
    try:
        item = fetch_one(db_conn, "reviewer-1")
        assert item is not None
        db_conn.execute(
            "UPDATE nuron_ai.documents SET lease_token = lease_token + 1 WHERE content_hash = %s",
            (digest,),
        )
        db_conn.commit()

        assert approve(db_conn, digest, "reviewer-1", item.lease_token) is None

        row = db_conn.execute(
            "SELECT state FROM nuron_ai.documents WHERE content_hash = %s", (digest,)
        ).fetchone()
        assert row == ("awaiting_review",)
        assert (
            db_conn.execute(
                "SELECT count(*) FROM nuron_ai.reviewed_sources WHERE content_hash = %s", (digest,)
            ).fetchone()
            == (0,)
        )
    finally:
        _cleanup(db_conn, digest)


def test_approve_fails_when_lease_expired(
    memory_storage: ObjectStorage, db_conn: psycopg.Connection
) -> None:
    digest = _awaiting_review(db_conn, memory_storage, b"# Expired lease\n", "expired-lease.md")
    try:
        item = fetch_one(db_conn, "reviewer-1")
        assert item is not None
        db_conn.execute(
            """
            UPDATE nuron_ai.documents
            SET lease_until = clock_timestamp() - interval '1 second'
            WHERE content_hash = %s
            """,
            (digest,),
        )
        db_conn.commit()

        assert approve(db_conn, digest, "reviewer-1", item.lease_token) is None

        row = db_conn.execute(
            "SELECT state FROM nuron_ai.documents WHERE content_hash = %s", (digest,)
        ).fetchone()
        assert row == ("awaiting_review",)
        assert (
            db_conn.execute(
                "SELECT count(*) FROM nuron_ai.reviewed_sources WHERE content_hash = %s", (digest,)
            ).fetchone()
            == (0,)
        )
    finally:
        _cleanup(db_conn, digest)


def test_approve_fails_when_lease_expires_waiting_for_advisory_lock(
    memory_storage: ObjectStorage, db_conn: psycopg.Connection, review_test_db: str
) -> None:
    filename = "expired-during-lock.md"
    digest = _awaiting_review(db_conn, memory_storage, b"# Expires mid-lock\n", filename)
    blocker: psycopg.Connection | None = None
    worker: psycopg.Connection | None = None
    try:
        item = fetch_one(db_conn, "reviewer-1")
        assert item is not None
        db_conn.execute(
            """
            UPDATE nuron_ai.documents
            SET lease_until = clock_timestamp() + interval '2 seconds'
            WHERE content_hash = %s
            """,
            (digest,),
        )
        db_conn.commit()

        blocker = psycopg.connect(
            host=os.environ["NURON_AI_DB_HOST"],
            port=os.environ["NURON_AI_DB_PORT"],
            dbname=review_test_db,
            user="nuron_ai_svc",
            password=os.environ["NURON_AI_DB_PASSWORD"],
        )
        blocker.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (filename,))

        worker = psycopg.connect(
            host=os.environ["NURON_AI_DB_HOST"],
            port=os.environ["NURON_AI_DB_PORT"],
            dbname=review_test_db,
            user="nuron_ai_svc",
            password=os.environ["NURON_AI_DB_PASSWORD"],
        )
        result: list[int | None] = []

        def run_approve() -> None:
            assert worker is not None
            result.append(approve(worker, digest, "reviewer-1", item.lease_token))

        thread = threading.Thread(target=run_approve)
        thread.start()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            waiting = db_conn.execute(
                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted AND pid = %s",
                (worker.info.backend_pid,),
            ).fetchone()
            if waiting == (1,):
                break
            time.sleep(0.05)
        else:
            raise TimeoutError("approve never blocked on advisory lock")
        time.sleep(2.5)
        blocker.rollback()
        thread.join(timeout=10)
        assert not thread.is_alive()
        assert result == [None]

        row = db_conn.execute(
            "SELECT state FROM nuron_ai.documents WHERE content_hash = %s", (digest,)
        ).fetchone()
        assert row == ("awaiting_review",)
        assert (
            db_conn.execute(
                "SELECT count(*) FROM nuron_ai.reviewed_sources WHERE content_hash = %s", (digest,)
            ).fetchone()
            == (0,)
        )
    finally:
        if worker is not None:
            worker.close()
        if blocker is not None:
            blocker.close()
        _cleanup(db_conn, digest)


def test_promote_parsed_moves_parsed_row_to_awaiting_review(
    memory_storage: ObjectStorage, db_conn: psycopg.Connection
) -> None:
    digest = _parsed(db_conn, memory_storage, b"# Just parsed\n", "just-parsed.md")
    try:
        assert promote_parsed(db_conn, "worker-1")

        row = db_conn.execute(
            "SELECT state, claimed_by FROM nuron_ai.documents WHERE content_hash = %s", (digest,)
        ).fetchone()
        assert row == ("awaiting_review", None)
    finally:
        _cleanup(db_conn, digest)


def test_reingest_diff_returns_none_for_first_time_ingest(
    memory_storage: ObjectStorage, db_conn: psycopg.Connection
) -> None:
    digest = _awaiting_review(db_conn, memory_storage, b"# Fresh\n", "fresh.md")
    try:
        assert (
            reingest_diff(
                db_conn,
                memory_storage,
                "fresh.md",
                digest,
                llama_parse_api_key=None,
                llama_parse_tier=None,
            )
            is None
        )
    finally:
        _cleanup(db_conn, digest)


def test_reingest_diff_shows_unified_diff_against_prior_approved_version(
    memory_storage: ObjectStorage, db_conn: psycopg.Connection
) -> None:
    digest_v1 = _awaiting_review(db_conn, memory_storage, b"line one\nline two\n", "changed.md")
    digest_v2 = _awaiting_review(db_conn, memory_storage, b"line one\nline TWO changed\n", "changed.md")
    try:
        item_v1 = fetch_one(db_conn, "reviewer-1")
        assert item_v1 is not None
        assert approve(db_conn, digest_v1, "reviewer-1", item_v1.lease_token) == 1

        result = reingest_diff(
            db_conn,
            memory_storage,
            "changed.md",
            digest_v2,
            llama_parse_api_key=None,
            llama_parse_tier=None,
        )
        assert result is not None
        assert result.prior_version == 1
        assert result.prior_body == "line one\nline two\n"
        assert "-line two" in result.raw_diff
        assert "+line TWO changed" in result.raw_diff
    finally:
        _cleanup(db_conn, digest_v1, digest_v2)
