import io
import os
import zipfile
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import psycopg
import pytest

from nuron_ai import db
from nuron_ai.core import parse_header
from nuron_ai.extraction import (
    PermanentExtractionError,
    extract_markdown,
    extract_pending,
    parse_pending,
)
from nuron_ai.storage import ObjectStorage

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "watched"

# -- extract_markdown: passthrough / unsupported ------------------------------


def test_extract_markdown_passes_through_md():
    data = "# Subject\n\nBody.\n".encode()
    text = extract_markdown(data, "note.md", llama_parse_api_key=None, llama_parse_tier=None)
    assert text == "# Subject\n\nBody.\n"


def test_extract_markdown_passes_through_txt():
    data = "Plain notes.\n".encode()
    text = extract_markdown(data, "note.txt", llama_parse_api_key=None, llama_parse_tier=None)
    assert text == "Plain notes.\n"


def test_extract_markdown_rejects_unsupported_extension():
    with pytest.raises(ValueError, match="unsupported extension"):
        extract_markdown(b"{}", "note.json", llama_parse_api_key=None, llama_parse_tier=None)


# -- extract_markdown: .docx via the stdlib word/document.xml read -------------


def _docx_bytes(paragraph_text: str) -> bytes:
    """Builds a minimal valid .docx (extraction only reads word/document.xml)."""
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body><w:p><w:r><w:t>{paragraph_text}</w:t></w:r></w:p></w:body>"
        "</w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", document_xml)
    return buffer.getvalue()


def test_extract_markdown_docx_extracts_paragraph_text():
    data = _docx_bytes("Dropping the session store for stateless JWT.")

    text = extract_markdown(data, "decision.docx", llama_parse_api_key=None, llama_parse_tier=None)

    assert text.strip() == "Dropping the session store for stateless JWT."


# -- extract_markdown: .pdf via LlamaParse (mocked -- no network) ------------


def _stub_llama_cloud(monkeypatch: pytest.MonkeyPatch, markdown_text: str) -> MagicMock:
    """Patches llama_cloud.LlamaCloud so _extract_pdf never calls the real API."""
    client = MagicMock()
    client.files.create.return_value = SimpleNamespace(id="file-123")
    client.parsing.parse.return_value = SimpleNamespace(markdown_full=markdown_text)
    monkeypatch.setattr("llama_cloud.LlamaCloud", MagicMock(return_value=client))
    return client


def test_extract_markdown_pdf_disabled_without_api_key():
    with pytest.raises(PermanentExtractionError, match="PDF extraction is disabled"):
        extract_markdown(b"%PDF-1.4\n", "decision.pdf", llama_parse_api_key=None, llama_parse_tier="fast")


def test_extract_markdown_pdf_disabled_without_tier():
    with pytest.raises(PermanentExtractionError, match="PDF extraction is disabled"):
        extract_markdown(b"%PDF-1.4\n", "decision.pdf", llama_parse_api_key="key", llama_parse_tier=None)


def test_extract_markdown_pdf_calls_llama_parse_when_enabled(monkeypatch: pytest.MonkeyPatch):
    client = _stub_llama_cloud(monkeypatch, "# Decision\n\nDropping sessions for JWT, plenty of text.")

    text = extract_markdown(
        b"%PDF-1.4\n", "decision.pdf", llama_parse_api_key="key", llama_parse_tier="fast"
    )

    assert text == "# Decision\n\nDropping sessions for JWT, plenty of text."
    client.files.create.assert_called_once_with(
        file=("decision.pdf", b"%PDF-1.4\n", "application/pdf"), purpose="parse"
    )
    client.parsing.parse.assert_called_once_with(
        tier="fast", version="latest", file_id="file-123", expand=["markdown"]
    )


def test_extract_markdown_pdf_near_empty_result_fails_loudly(monkeypatch: pytest.MonkeyPatch):
    _stub_llama_cloud(monkeypatch, "   \n  ")

    with pytest.raises(PermanentExtractionError, match="near nothing"):
        extract_markdown(b"%PDF-1.4\n", "scan.pdf", llama_parse_api_key="key", llama_parse_tier="fast")


# -- extract_pending: claim/advance against a mocked connection --------------


def test_extract_pending_returns_false_when_nothing_to_claim():
    conn = MagicMock(spec=psycopg.Connection)
    conn.execute.return_value.fetchone.return_value = None
    storage = MagicMock(spec=ObjectStorage)

    claimed = extract_pending(
        conn, storage, "worker-1", llama_parse_api_key=None, llama_parse_tier=None
    )

    assert claimed is False
    storage.get.assert_not_called()


def test_extract_pending_advances_landed_row_to_extracted():
    conn = MagicMock(spec=psycopg.Connection)
    conn.execute.return_value.fetchone.return_value = ("abc123", "decision.md", 7)
    storage = MagicMock(spec=ObjectStorage)
    storage.get.return_value = b"# Decision\n\nBody.\n"

    claimed = extract_pending(
        conn, storage, "worker-1", llama_parse_api_key=None, llama_parse_tier=None
    )

    assert claimed is True
    storage.get.assert_called_once_with("ab/abc123")
    advance_call = conn.execute.call_args_list[1]
    assert "state = 'extracted'" in advance_call.args[0]
    assert advance_call.args[1] == {
        "body": "# Decision\n\nBody.\n",
        "content_hash": "abc123",
        "worker_id": "worker-1",
        "lease_token": 7,
    }
    assert conn.commit.call_count == 2


def test_extract_pending_soft_fails_on_transient_error_without_raising():
    conn = MagicMock(spec=psycopg.Connection)
    conn.execute.return_value.fetchone.return_value = ("abc123", "decision.md", 3)
    storage = MagicMock(spec=ObjectStorage)
    storage.get.side_effect = OSError("RustFS unavailable")

    claimed = extract_pending(
        conn, storage, "worker-1", llama_parse_api_key=None, llama_parse_tier=None
    )

    assert claimed is True
    failure_call = conn.execute.call_args_list[1]
    assert "attempt_count = attempt_count + 1" in failure_call.args[0]
    assert failure_call.args[1] == {
        "retry_delay_seconds": 60.0,
        "max_attempts": 5,
        "content_hash": "abc123",
        "worker_id": "worker-1",
        "lease_token": 3,
    }


def test_extract_pending_fails_immediately_on_disabled_pdf_extraction_without_retrying():
    # A disabled/near-empty PDF is a permanent condition, not a transient one -- it must
    # not burn the attempt budget (each retry against LlamaParse would be a paid call).
    conn = MagicMock(spec=psycopg.Connection)
    conn.execute.return_value.fetchone.return_value = ("abc123", "decision.pdf", 3)
    storage = MagicMock(spec=ObjectStorage)
    storage.get.return_value = b"%PDF-1.4\n"

    claimed = extract_pending(
        conn, storage, "worker-1", llama_parse_api_key=None, llama_parse_tier=None
    )

    assert claimed is True
    failure_call = conn.execute.call_args_list[1]
    assert "state = 'failed'" in failure_call.args[0]
    assert "attempt_count" not in failure_call.args[0]
    assert failure_call.args[1] == {
        "content_hash": "abc123",
        "worker_id": "worker-1",
        "lease_token": 3,
    }


def test_extract_pending_fails_immediately_on_near_empty_pdf_without_retrying(
    monkeypatch: pytest.MonkeyPatch,
):
    _stub_llama_cloud(monkeypatch, "  ")
    conn = MagicMock(spec=psycopg.Connection)
    conn.execute.return_value.fetchone.return_value = ("abc123", "scan.pdf", 9)
    storage = MagicMock(spec=ObjectStorage)
    storage.get.return_value = b"%PDF-1.4\n"

    claimed = extract_pending(
        conn, storage, "worker-1", llama_parse_api_key="key", llama_parse_tier="fast"
    )

    assert claimed is True
    failure_call = conn.execute.call_args_list[1]
    assert "state = 'failed'" in failure_call.args[0]
    assert "attempt_count" not in failure_call.args[0]


# -- parse_pending: claim/advance against a mocked connection ----------------


def test_parse_pending_returns_false_when_nothing_to_claim():
    conn = MagicMock(spec=psycopg.Connection)
    conn.execute.return_value.fetchone.return_value = None

    assert parse_pending(conn, "worker-1", source_owner=None) is False


def test_parse_pending_advances_extracted_row_to_parsed():
    conn = MagicMock(spec=psycopg.Connection)
    body = "# Moving off server-side sessions\n\nBody.\n\n— Basuru, 2026-05-14\n"
    conn.execute.return_value.fetchone.return_value = ("abc123", "decision.md", body, 4)

    claimed = parse_pending(conn, "worker-1", source_owner=None)

    assert claimed is True
    advance_call = conn.execute.call_args_list[1]
    assert "state = 'parsed'" in advance_call.args[0]
    assert advance_call.args[1] == {
        "title": "Moving off server-side sessions",
        "author": "Basuru",
        "author_source": "extracted",
        "document_date": date(2026, 5, 14),
        "tags": [],
        "content_hash": "abc123",
        "worker_id": "worker-1",
        "lease_token": 4,
    }


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
    import fsspec

    return ObjectStorage(fs=fsspec.filesystem("memory"), root="/nuron-extraction-test")


def _land(conn: psycopg.Connection, storage: ObjectStorage, data: bytes, filename: str) -> str:
    """Lands one row directly, mirroring watcher.scan()'s insert, for integration tests."""
    from nuron_ai.core import content_hash

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
):
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
        assert row == ("parsed", "Moving off server-side sessions", "Basuru", "extracted", date(2026, 5, 14))
    finally:
        _cleanup(db_conn, digest)


def test_extract_pending_leaves_a_claimed_row_for_another_worker_alone(
    memory_storage: ObjectStorage, db_conn: psycopg.Connection
):
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


def test_a11_pdf_fixture_header_matches_the_markdown_fixture_it_mirrors():
    if os.environ.get("LLAMA_PARSE_ENABLED", "false").lower() != "true":
        pytest.skip("LLAMA_PARSE_ENABLED not set -- skipping the real LlamaParse call (A11)")
    api_key = os.environ.get("LLAMA_PARSE_API_KEY")
    tier = os.environ.get("LLAMA_PARSE_TIER")
    if not api_key or not tier:
        pytest.skip("LLAMA_PARSE_API_KEY/LLAMA_PARSE_TIER not set -- skipping A11")

    pdf_bytes = FIXTURES.joinpath("auth-token-decision.pdf").read_bytes()
    markdown = extract_markdown(
        pdf_bytes, "auth-token-decision.pdf", llama_parse_api_key=api_key, llama_parse_tier=tier
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
