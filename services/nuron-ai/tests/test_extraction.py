import io
import os
import zipfile
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import fsspec
import psycopg
import pytest
from llama_cloud import APIConnectionError

from nuron_ai import db
from nuron_ai.core import content_hash, parse_header
from nuron_ai.extraction import (
    _MAX_ATTEMPTS,
    ExtractionDeferred,
    PermanentExtractionError,
    extract_markdown,
    extract_pending,
    parse_pending,
)
from nuron_ai.storage import ObjectStorage

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "watched"

# -- extract_markdown: passthrough / unsupported ------------------------------


def test_extract_markdown_passes_through_md() -> None:
    data = "# Subject\n\nBody.\n".encode()
    text = extract_markdown(data, "note.md", llama_parse_api_key=None, llama_parse_tier=None)
    assert text == "# Subject\n\nBody.\n"


def test_extract_markdown_passes_through_txt() -> None:
    data = "Plain notes.\n".encode()
    text = extract_markdown(data, "note.txt", llama_parse_api_key=None, llama_parse_tier=None)
    assert text == "Plain notes.\n"


def test_extract_markdown_rejects_non_utf8_text_permanently() -> None:
    with pytest.raises(PermanentExtractionError, match="not valid UTF-8"):
        extract_markdown(b"\xff", "note.txt", llama_parse_api_key=None, llama_parse_tier=None)


def test_extract_markdown_rejects_unsupported_extension() -> None:
    with pytest.raises(PermanentExtractionError, match="unsupported extension"):
        extract_markdown(b"{}", "note.json", llama_parse_api_key=None, llama_parse_tier=None)


# -- extract_markdown: .docx via the stdlib word/document.xml read -------------


def _docx_bytes(paragraph_text: str, style: str | None = None) -> bytes:
    """Builds a minimal valid .docx (extraction only reads word/document.xml)."""
    paragraph_properties = ""
    if style:
        paragraph_properties = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>'
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body><w:p>{paragraph_properties}<w:r><w:t>{paragraph_text}</w:t></w:r></w:p></w:body>"
        "</w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", document_xml)
    return buffer.getvalue()


def test_extract_markdown_docx_extracts_paragraph_text() -> None:
    data = _docx_bytes("Dropping the session store for stateless JWT.")

    text = extract_markdown(data, "decision.docx", llama_parse_api_key=None, llama_parse_tier=None)

    assert text.strip() == "Dropping the session store for stateless JWT."


def test_extract_markdown_docx_heading_one_becomes_document_title() -> None:
    data = _docx_bytes("Dropping the session store", style="Heading1")

    text = extract_markdown(data, "decision.docx", llama_parse_api_key=None, llama_parse_tier=None)
    header = parse_header(text, filename="decision.docx", source_owner=None)

    assert header.subject == "Dropping the session store"


def test_extract_markdown_docx_heading_two_stays_plain() -> None:
    data = _docx_bytes("Section", style="Heading2")

    text = extract_markdown(data, "decision.docx", llama_parse_api_key=None, llama_parse_tier=None)

    assert text.strip() == "Section"


def test_extract_markdown_docx_rejects_oversized_document_xml() -> None:
    max_accepted_bytes = 25 * 1024 * 1024
    data = _docx_bytes("x" * max_accepted_bytes)

    with pytest.raises(PermanentExtractionError, match="document.xml exceeds"):
        extract_markdown(data, "decision.docx", llama_parse_api_key=None, llama_parse_tier=None)


def test_extract_markdown_docx_rejects_excessive_compression_ratio() -> None:
    data = _docx_bytes("x" * 200_000)

    with pytest.raises(PermanentExtractionError, match="compression ratio"):
        extract_markdown(data, "decision.docx", llama_parse_api_key=None, llama_parse_tier=None)


def test_extract_markdown_docx_rejects_dtd() -> None:
    document_xml = (
        '<?xml version="1.0"?>'
        '<!DOCTYPE w:document [<!ENTITY payload "hostile">]>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>&payload;</w:t></w:r></w:p></w:body>"
        "</w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", document_xml)

    with pytest.raises(PermanentExtractionError, match="DTD"):
        extract_markdown(
            buffer.getvalue(),
            "decision.docx",
            llama_parse_api_key=None,
            llama_parse_tier=None,
        )


def test_extract_markdown_docx_rejects_corrupt_archive_permanently() -> None:
    with pytest.raises(PermanentExtractionError, match="unreadable .docx"):
        extract_markdown(
            b"not a zip archive",
            "decision.docx",
            llama_parse_api_key=None,
            llama_parse_tier=None,
        )


# -- extract_markdown: .pdf via LlamaParse (mocked -- no network) ------------


def _stub_llama_cloud(monkeypatch: pytest.MonkeyPatch, markdown_text: str) -> MagicMock:
    """Patches LlamaCloud to return deterministic extracted Markdown."""
    client = MagicMock()
    client.files.create.return_value = SimpleNamespace(id="file-123")
    client.parsing.parse.return_value = SimpleNamespace(markdown_full=markdown_text)
    monkeypatch.setattr("nuron_ai.extraction.LlamaCloud", MagicMock(return_value=client))
    return client


def test_extract_markdown_pdf_deferred_without_api_key() -> None:
    with pytest.raises(ExtractionDeferred, match="PDF extraction is disabled"):
        extract_markdown(
            b"%PDF-1.4\n",
            "decision.pdf",
            llama_parse_api_key=None,
            llama_parse_tier="fast",
        )


def test_extract_markdown_pdf_deferred_without_tier() -> None:
    with pytest.raises(ExtractionDeferred, match="PDF extraction is disabled"):
        extract_markdown(
            b"%PDF-1.4\n",
            "decision.pdf",
            llama_parse_api_key="key",
            llama_parse_tier=None,
        )


def test_extract_markdown_pdf_calls_llama_parse_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _stub_llama_cloud(
        monkeypatch, "# Decision\n\nDropping sessions for JWT, plenty of text."
    )

    text = extract_markdown(
        b"%PDF-1.4\n", "decision.pdf", llama_parse_api_key="key", llama_parse_tier="fast"
    )

    assert text == "# Decision\n\nDropping sessions for JWT, plenty of text."
    client.parsing.parse.assert_called_once_with(
        tier="fast",
        version="latest",
        file_id="file-123",
        expand=["markdown_full"],
        timeout=240.0,
    )
    client.files.delete.assert_called_once_with(file_id="file-123")


def test_extract_markdown_pdf_accepts_short_nonempty_result(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_llama_cloud(monkeypatch, "Approved.")

    text = extract_markdown(
        b"%PDF-1.4\n", "decision.pdf", llama_parse_api_key="key", llama_parse_tier="fast"
    )

    assert text == "Approved."


def test_extract_markdown_pdf_near_empty_result_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _stub_llama_cloud(monkeypatch, "   \n  ")

    with pytest.raises(PermanentExtractionError, match="near nothing"):
        extract_markdown(
            b"%PDF-1.4\n",
            "scan.pdf",
            llama_parse_api_key="key",
            llama_parse_tier="fast",
        )
    client.files.delete.assert_called_once_with(file_id="file-123")


def test_extract_markdown_pdf_deletes_upload_when_parsing_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _stub_llama_cloud(monkeypatch, "")
    client.parsing.parse.side_effect = RuntimeError("parser bug")

    with pytest.raises(RuntimeError, match="parser bug"):
        extract_markdown(
            b"%PDF-1.4\n",
            "scan.pdf",
            llama_parse_api_key="key",
            llama_parse_tier="fast",
        )
    client.files.delete.assert_called_once_with(file_id="file-123")


def _claimed_row(filename: str, body: str | None = None) -> tuple:
    """A db.claim RETURNING tuple: hash, filename, empty header, body, lease_token."""
    return ("a" * 64, filename, None, None, None, None, [], body, 3)


def test_extract_pending_retries_llama_cloud_connection_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _stub_llama_cloud(monkeypatch, "")
    client.parsing.parse.side_effect = APIConnectionError(request=MagicMock())
    conn = MagicMock(spec=psycopg.Connection)
    conn.execute.return_value.fetchone.return_value = _claimed_row("decision.pdf")
    conn.execute.return_value.rowcount = 1
    storage = MagicMock(spec=ObjectStorage)
    storage.get.return_value = b"%PDF-1.4\n"

    claimed = extract_pending(
        conn,
        storage,
        "worker-1",
        llama_parse_api_key="key",
        llama_parse_tier="fast",
    )

    assert claimed is True
    retry_call = conn.execute.call_args_list[1]
    assert "attempt_count = attempt_count + 1" in str(retry_call.args[0])
    client.files.delete.assert_called_once_with(file_id="file-123")


def test_extract_pending_reraises_unexpected_programming_errors() -> None:
    conn = MagicMock(spec=psycopg.Connection)
    conn.execute.return_value.fetchone.return_value = _claimed_row("decision.md")
    conn.execute.return_value.rowcount = 1
    storage = MagicMock(spec=ObjectStorage)
    storage.get.side_effect = TypeError("programming bug")

    with pytest.raises(TypeError, match="programming bug"):
        extract_pending(
            conn,
            storage,
            "worker-1",
            llama_parse_api_key=None,
            llama_parse_tier=None,
        )

    assert conn.execute.call_count == 2
    retry_call = conn.execute.call_args_list[1]
    assert "attempt_count = attempt_count + 1" in str(retry_call.args[0])


def test_extract_pending_surfaces_lost_lease_during_release() -> None:
    conn = MagicMock(spec=psycopg.Connection)
    conn.execute.return_value.fetchone.return_value = _claimed_row("decision.md")
    conn.execute.return_value.rowcount = 0
    storage = MagicMock(spec=ObjectStorage)
    storage.get.return_value = b"# Decision\n"

    with pytest.raises(RuntimeError, match="lost lease"):
        extract_pending(
            conn,
            storage,
            "worker-1",
            llama_parse_api_key=None,
            llama_parse_tier=None,
        )

    conn.rollback.assert_called_once_with()
    conn.commit.assert_called_once_with()


def test_extract_pending_preserves_lost_lease_when_rollback_also_fails() -> None:
    conn = MagicMock(spec=psycopg.Connection)
    conn.execute.return_value.fetchone.return_value = _claimed_row("decision.md")
    conn.execute.return_value.rowcount = 0
    conn.rollback.side_effect = psycopg.OperationalError("connection closed")
    storage = MagicMock(spec=ObjectStorage)
    storage.get.return_value = b"# Decision\n"

    with pytest.raises(RuntimeError, match="lost lease") as raised:
        extract_pending(
            conn,
            storage,
            "worker-1",
            llama_parse_api_key=None,
            llama_parse_tier=None,
        )

    assert raised.value.__notes__ == [
        "rollback after lost lease also failed: OperationalError: connection closed",
    ]


def test_extract_pending_marks_unsupported_extension_failed_without_retrying() -> None:
    conn = MagicMock(spec=psycopg.Connection)
    conn.execute.return_value.fetchone.return_value = _claimed_row("note.json")
    conn.execute.return_value.rowcount = 1
    storage = MagicMock(spec=ObjectStorage)
    storage.get.return_value = b"{}"

    claimed = extract_pending(
        conn,
        storage,
        "worker-1",
        llama_parse_api_key=None,
        llama_parse_tier=None,
    )

    assert claimed is True
    assert conn.execute.call_count == 2
    fail_call = conn.execute.call_args_list[1]
    assert "state = 'failed'" in str(fail_call.args[0])
    assert "attempt_count = attempt_count + 1" not in str(fail_call.args[0])


def test_extract_pending_caps_disabled_pdf_deferrals() -> None:
    conn = MagicMock(spec=psycopg.Connection)
    conn.execute.return_value.fetchone.return_value = _claimed_row("decision.pdf")
    conn.execute.return_value.rowcount = 1
    storage = MagicMock(spec=ObjectStorage)
    storage.get.return_value = b"%PDF-1.4\n"

    claimed = extract_pending(
        conn,
        storage,
        "worker-1",
        llama_parse_api_key=None,
        llama_parse_tier=None,
    )

    assert claimed is True
    defer_call = conn.execute.call_args_list[1]
    assert "attempt_count = attempt_count + 1" in str(defer_call.args[0])
    assert defer_call.args[1]["max_attempts"] == _MAX_ATTEMPTS


def test_parse_pending_retries_unexpected_parse_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = MagicMock(spec=psycopg.Connection)
    conn.execute.return_value.fetchone.return_value = _claimed_row("decision.md", "# Decision")
    conn.execute.return_value.rowcount = 1
    monkeypatch.setattr(
        "nuron_ai.extraction.parse_header",
        MagicMock(side_effect=ValueError("malformed header")),
    )

    claimed = parse_pending(conn, "worker-1", source_owner=None)

    assert claimed is True
    assert conn.execute.call_count == 2
    retry_call = conn.execute.call_args_list[1]
    assert "attempt_count = attempt_count + 1" in str(retry_call.args[0])


# -- extract_pending / parse_pending: real Postgres, gated behind infra ------


@pytest.fixture
def db_conn() -> Iterator[psycopg.Connection]:
    if not os.environ.get("POSTGRES_INTEGRATION_TESTS"):
        pytest.skip("POSTGRES_INTEGRATION_TESTS not set -- skipping Postgres integration tests")
    conn = db.from_env()
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def memory_storage() -> ObjectStorage:
    return ObjectStorage(fs=fsspec.filesystem("memory"), root="/nuron-extraction-test")


def _land(conn: psycopg.Connection, storage: ObjectStorage, data: bytes, filename: str) -> str:
    """Lands one row directly, mirroring watcher.scan()'s insert, for integration tests."""
    digest = content_hash(data)
    storage.put(data)
    conn.execute(
        """
        INSERT INTO nuron_ai.documents (content_hash, entry_point, original_filename)
        VALUES (%s, 'watched_directory', %s)
        ON CONFLICT (content_hash) DO NOTHING
        """,
        (digest, filename),
    )
    conn.commit()
    return digest


def _cleanup(conn: psycopg.Connection, digest: str) -> None:
    conn.execute("DELETE FROM nuron_ai.documents WHERE content_hash = %s", (digest,))
    conn.commit()


def test_extract_then_parse_pending_take_a_landed_md_row_to_parsed(
    memory_storage: ObjectStorage, db_conn: psycopg.Connection
) -> None:
    data = b"# Moving off server-side sessions\n\nBody.\n\n\xe2\x80\x94 Basuru, 2026-05-14\n"
    digest = _land(db_conn, memory_storage, data, "decision.md")

    try:
        assert extract_pending(
            db_conn, memory_storage, "worker-1", llama_parse_api_key=None, llama_parse_tier=None
        )
        assert parse_pending(db_conn, "worker-1", source_owner=None)

        row = db_conn.execute(
            "SELECT state, title, author, author_source, document_date "
            "FROM nuron_ai.documents WHERE content_hash = %s",
            (digest,),
        ).fetchone()
        assert row == (
            "parsed",
            "Moving off server-side sessions",
            "Basuru",
            "extracted",
            date(2026, 5, 14),
        )
    finally:
        _cleanup(db_conn, digest)


def test_extract_pending_fails_disabled_pdf_after_max_deferrals(
    memory_storage: ObjectStorage, db_conn: psycopg.Connection
) -> None:
    digest = _land(db_conn, memory_storage, b"%PDF-1.4\n", "decision.pdf")

    try:
        for attempt in range(_MAX_ATTEMPTS):
            assert extract_pending(
                db_conn,
                memory_storage,
                "worker-1",
                llama_parse_api_key=None,
                llama_parse_tier=None,
            )
            if attempt < _MAX_ATTEMPTS - 1:
                db_conn.execute(
                    "UPDATE nuron_ai.documents SET next_attempt_at = now() "
                    "WHERE content_hash = %s",
                    (digest,),
                )
                db_conn.commit()

        row = db_conn.execute(
            "SELECT state, attempt_count, claimed_by IS NOT NULL "
            "FROM nuron_ai.documents WHERE content_hash = %s",
            (digest,),
        ).fetchone()
        assert row == ("failed", _MAX_ATTEMPTS, False)
    finally:
        _cleanup(db_conn, digest)


def test_extract_pending_marks_corrupt_docx_failed_without_retrying(
    memory_storage: ObjectStorage, db_conn: psycopg.Connection
) -> None:
    digest = _land(db_conn, memory_storage, b"not a zip archive", "decision.docx")

    try:
        assert extract_pending(
            db_conn, memory_storage, "worker-1", llama_parse_api_key=None, llama_parse_tier=None
        )

        row = db_conn.execute(
            "SELECT state, attempt_count FROM nuron_ai.documents WHERE content_hash = %s",
            (digest,),
        ).fetchone()
        assert row == ("failed", 0)
    finally:
        _cleanup(db_conn, digest)


def test_extract_pending_records_transient_failure_for_retry(
    memory_storage: ObjectStorage, db_conn: psycopg.Connection
) -> None:
    digest = _land(db_conn, memory_storage, b"# Retry later\n", "decision.md")
    unavailable_storage = MagicMock(spec=ObjectStorage)
    unavailable_storage.get.side_effect = OSError("RustFS unavailable")

    try:
        assert extract_pending(
            db_conn,
            unavailable_storage,
            "worker-1",
            llama_parse_api_key=None,
            llama_parse_tier=None,
        )

        row = db_conn.execute(
            "SELECT state, attempt_count, next_attempt_at IS NOT NULL "
            "FROM nuron_ai.documents WHERE content_hash = %s",
            (digest,),
        ).fetchone()
        assert row == ("landed", 1, True)
    finally:
        _cleanup(db_conn, digest)


def test_extract_pending_leaves_a_claimed_row_for_another_worker_alone(
    memory_storage: ObjectStorage, db_conn: psycopg.Connection
) -> None:
    data = b"# Held by another worker\n"
    digest = _land(db_conn, memory_storage, data, "held.md")

    try:
        db_conn.execute(
            """
            UPDATE nuron_ai.documents
            SET claimed_by = 'other-worker', lease_until = now() + interval '1 hour'
            WHERE content_hash = %s
            """,
            (digest,),
        )
        db_conn.commit()

        claimed = extract_pending(
            db_conn, memory_storage, "worker-1", llama_parse_api_key=None, llama_parse_tier=None
        )

        assert claimed is False
    finally:
        _cleanup(db_conn, digest)


# -- A11: PDF fixture E, extracted for real via LlamaParse -------------------
# Non-blocking by design (docs/tracer-bullet-01.md): gated on real credentials/network,
# never required for A1-A10 or for CI. Run with LLAMA_PARSE_ENABLED=true and a real
# LLAMA_PARSE_API_KEY to actually exercise it -- this calls the live LlamaCloud API and
# spends real per-page credits (docs/tracer-bullet-01.md §7).


def test_a11_pdf_fixture_header_matches_the_markdown_fixture_it_mirrors() -> None:
    if os.environ.get("LLAMA_PARSE_ENABLED", "false").lower() != "true":
        pytest.skip("LLAMA_PARSE_ENABLED not set -- skipping the real LlamaParse call (A11)")
    api_key = os.environ.get("LLAMA_PARSE_API_KEY")
    tier = os.environ.get("LLAMA_PARSE_TIER")
    if not api_key or not tier:
        pytest.skip("LLAMA_PARSE_API_KEY/LLAMA_PARSE_TIER not set -- skipping A11")

    pdf_bytes = FIXTURES.joinpath("auth-token-decision.pdf").read_bytes()
    markdown = extract_markdown(
        pdf_bytes,
        "auth-token-decision.pdf",
        llama_parse_api_key=api_key,
        llama_parse_tier=tier,
    )
    header = parse_header(markdown, filename="auth-token-decision.pdf", source_owner=None)

    md_header = parse_header(
        FIXTURES.joinpath("auth-token-decision.md").read_text(),
        filename="auth-token-decision.md",
        source_owner=None,
    )
    assert header.subject == md_header.subject
    assert header.author == md_header.author
    assert header.author_source == "extracted"
    assert header.timestamp == md_header.timestamp
