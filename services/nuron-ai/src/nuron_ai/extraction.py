"""Format extraction + header parse worker: landed -> extracted -> parsed (NU-006).

Converts every supported format to the pipeline's common markdown form, then reuses
core.parse_header (already format-agnostic) so nothing branches by format past extraction.
See docs/tracer-bullet-01.md "Pipeline state machine" and "Worker claim / lease".
"""

import logging
import os
import time
import uuid
import xml.etree.ElementTree as ET
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any

import psycopg

from nuron_ai import db
from nuron_ai.core import object_key, parse_header
from nuron_ai.storage import ObjectStorage, from_env as storage_from_env

logger = logging.getLogger(__name__)

# ponytail: flat character-count floor, not per-page text density -- a genuinely short but
# real PDF could trip this; revisit against a real corpus if that ever happens.
_MIN_PDF_EXTRACTED_CHARS = 20
_LEASE_SECONDS = 300.0
_MAX_ATTEMPTS = 5
_RETRY_DELAY_SECONDS = 60.0
_POLL_DELAY_SECONDS = 5.0


class PermanentExtractionError(RuntimeError):
    """Raised when extraction can never succeed -- PDF parsing disabled, or a scanned,
    image-only PDF with no text layer (no OCR in this pipeline)."""


def extract_markdown(
    data: bytes,
    filename: str,
    *,
    llama_parse_api_key: str | None,
    llama_parse_tier: str | None,
) -> str:
    """Converts one landed file's bytes to markdown -- the common form header parse expects."""
    suffix = Path(filename).suffix.lower()
    if suffix in {".md", ".txt"}:
        return data.decode("utf-8")
    if suffix == ".docx":
        return _extract_docx(data)
    if suffix == ".pdf":
        return _extract_pdf(data, filename, llama_parse_api_key, llama_parse_tier)
    raise ValueError(f"unsupported extension for extraction: {suffix!r}")


def _extract_docx(data: bytes) -> str:
    """Extracts paragraph text from a .docx's word/document.xml -- stdlib, no deps."""
    tag = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(BytesIO(data)) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    return "\n\n".join(
        "".join(node.text or "" for node in paragraph.iter(f"{tag}t"))
        for paragraph in root.iter(f"{tag}p")
    )


def _extract_pdf(
    data: bytes, filename: str, api_key: str | None, tier: str | None
) -> str:
    """Extracts markdown from a .pdf via LlamaParse -- configurable, off by default (SS5.1)."""
    if not api_key or not tier:
        raise PermanentExtractionError(
            "PDF extraction is disabled -- set LLAMA_PARSE_ENABLED=true, LLAMA_PARSE_API_KEY "
            "and LLAMA_PARSE_TIER (dev/test only; verify current per-page credit pricing for "
            "the chosen tier first -- docs/tracer-bullet-01.md §7)"
        )

    from llama_cloud import LlamaCloud

    client = LlamaCloud(api_key=api_key)
    uploaded = client.files.create(file=(filename, data, "application/pdf"), purpose="parse")
    result = client.parsing.parse(
        tier=tier, version="latest", file_id=uploaded.id, expand=["markdown"]
    )
    text = result.markdown_full or ""
    if len(text.strip()) < _MIN_PDF_EXTRACTED_CHARS:
        raise PermanentExtractionError(
            "PDF extracted to near nothing -- likely a scanned image with no text layer "
            "(no OCR in this pipeline)"
        )
    return text


def _claim(
    conn: psycopg.Connection,
    worker_id: str,
    state: str,
    *,
    extra_columns: str = "",
    lease_seconds: float = _LEASE_SECONDS,
) -> tuple[Any, ...] | None:
    """Claims one claimable row in `state`, bumping the lease -- None when nothing to take."""
    claimed = conn.execute(
        f"""
        UPDATE nuron_ai.documents
        SET claimed_by = %(worker_id)s,
            lease_until = now() + %(lease_seconds)s * interval '1 second',
            lease_token = lease_token + 1
        WHERE content_hash = (
            SELECT content_hash
            FROM nuron_ai.documents
            WHERE state = %(state)s::nuron_ai.pipeline_state
              AND (lease_until IS NULL OR lease_until < now())
              AND (next_attempt_at IS NULL OR next_attempt_at <= now())
            ORDER BY created_at
            FOR UPDATE SKIP LOCKED
            LIMIT 1
        )
        RETURNING content_hash, original_filename{extra_columns}, lease_token
        """,
        {"worker_id": worker_id, "lease_seconds": lease_seconds, "state": state},
    ).fetchone()
    conn.commit()
    return claimed


def _release(
    conn: psycopg.Connection,
    digest: str,
    worker_id: str,
    lease_token: int,
    set_sql: str,
    params: dict[str, Any],
) -> None:
    """Applies `set_sql` to the row we hold, then lets go of the lease -- the
    claimed_by/lease_token guard means a stolen (lease-expired) row is never written
    by the worker that lost it."""
    conn.execute(
        f"""
        UPDATE nuron_ai.documents
        SET {set_sql},
            claimed_by = NULL,
            lease_until = NULL
        WHERE content_hash = %(content_hash)s
          AND claimed_by = %(worker_id)s
          AND lease_token = %(lease_token)s
        """,
        {**params, "content_hash": digest, "worker_id": worker_id, "lease_token": lease_token},
    )
    conn.commit()


def extract_pending(
    conn: psycopg.Connection,
    storage: ObjectStorage,
    worker_id: str,
    *,
    llama_parse_api_key: str | None,
    llama_parse_tier: str | None,
    lease_seconds: float = _LEASE_SECONDS,
) -> bool:
    """Claims one `landed` row, extracts it to markdown, and advances it to `extracted`.

    Extraction failures soft-fail through attempt_count/next_attempt_at (tracer-bullet-01.md
    "Attempts") instead of raising -- the same loop a failed compile or persist will reuse.
    Returns whether a row was claimed, so main()'s poll loop knows whether to keep going.
    """
    claimed = _claim(conn, worker_id, "landed", lease_seconds=lease_seconds)
    if claimed is None:
        return False

    digest, original_filename, lease_token = claimed
    try:
        data = storage.get(object_key(digest))
        markdown = extract_markdown(
            data,
            original_filename,
            llama_parse_api_key=llama_parse_api_key,
            llama_parse_tier=llama_parse_tier,
        )
    except PermanentExtractionError as err:
        # Permanent, not transient -- retrying can never succeed (a scanned PDF stays
        # scanned), and each retry against LlamaParse would be another paid call for
        # nothing. Fail straight away instead of burning the attempt budget on it.
        logger.warning("extraction permanently failed for %s (%s): %s", digest, original_filename, err)
        _release(conn, digest, worker_id, lease_token, "state = 'failed'", {})
        return True
    except Exception as err:
        logger.warning("extraction failed for %s (%s): %s", digest, original_filename, err)
        _release(
            conn,
            digest,
            worker_id,
            lease_token,
            """
            attempt_count = attempt_count + 1,
            next_attempt_at = now() + %(retry_delay_seconds)s * interval '1 second',
            state = CASE WHEN attempt_count + 1 >= %(max_attempts)s
                         THEN 'failed'::nuron_ai.pipeline_state
                         ELSE state END
            """,
            {"retry_delay_seconds": _RETRY_DELAY_SECONDS, "max_attempts": _MAX_ATTEMPTS},
        )
        return True

    _release(
        conn,
        digest,
        worker_id,
        lease_token,
        "state = 'extracted', body = %(body)s, attempt_count = 0, next_attempt_at = NULL",
        {"body": markdown},
    )
    return True


def parse_pending(
    conn: psycopg.Connection,
    worker_id: str,
    source_owner: str | None,
    lease_seconds: float = _LEASE_SECONDS,
) -> bool:
    """Claims one `extracted` row and runs the deterministic header parse onto `parsed`.

    No failure handling here: parse_header is a pure, total function over already-extracted
    text -- it degrades to blank fields, it does not raise.
    """
    claimed = _claim(
        conn, worker_id, "extracted", extra_columns=", body", lease_seconds=lease_seconds
    )
    if claimed is None:
        return False

    digest, original_filename, body, lease_token = claimed
    header = parse_header(body, filename=original_filename, source_owner=source_owner)

    _release(
        conn,
        digest,
        worker_id,
        lease_token,
        """
        state = 'parsed', title = %(title)s, author = %(author)s,
        author_source = %(author_source)s, document_date = %(document_date)s, tags = %(tags)s,
        attempt_count = 0, next_attempt_at = NULL
        """,
        {
            "title": header.subject,
            "author": header.author,
            "author_source": header.author_source,
            "document_date": header.timestamp,
            "tags": header.tags,
        },
    )
    return True


def main() -> None:
    """Runs the extract/parse worker forever, polling for pending rows."""
    logging.basicConfig(level=logging.INFO)
    worker_id = uuid.uuid4().hex
    source_owner = os.environ.get("SOURCE_OWNER") or None
    llama_parse_enabled = os.environ.get("LLAMA_PARSE_ENABLED", "false").lower() == "true"
    llama_parse_api_key: str | None = None
    llama_parse_tier: str | None = None
    if llama_parse_enabled:
        llama_parse_api_key = os.environ.get("LLAMA_PARSE_API_KEY") or None
        llama_parse_tier = os.environ.get("LLAMA_PARSE_TIER") or None
    storage: ObjectStorage | None = None

    while True:
        try:
            if storage is None:
                storage = storage_from_env()
            # ponytail: a fresh connection every poll, not a pool -- fine at tracer-bullet
            # scale; add pooling if this loop ever needs to run hotter than a few seconds.
            with db.from_env() as conn:
                did_extract = extract_pending(
                    conn,
                    storage,
                    worker_id,
                    llama_parse_api_key=llama_parse_api_key,
                    llama_parse_tier=llama_parse_tier,
                )
                did_parse = parse_pending(conn, worker_id, source_owner)
        except Exception:
            storage = None
            logger.exception(
                "extraction worker failed; retrying after %.0f seconds", _RETRY_DELAY_SECONDS
            )
            time.sleep(_RETRY_DELAY_SECONDS)
            continue
        if not (did_extract or did_parse):
            time.sleep(_POLL_DELAY_SECONDS)


if __name__ == "__main__":
    main()
