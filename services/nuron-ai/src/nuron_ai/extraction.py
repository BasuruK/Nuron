"""Format extraction + header parse worker: landed -> extracted -> parsed (NU-006).

Converts every supported format to the pipeline's common markdown form, then reuses
core.parse_header (already format-agnostic) so nothing branches by format past extraction.
See docs/tracer-bullet-01.md "Pipeline state machine" and "Worker claim / lease".
"""

import logging
import os
import time
import uuid
import zipfile
from enum import Enum
from io import BytesIO
from pathlib import Path
from typing import Any

import psycopg
from defusedxml import ElementTree as ET
from defusedxml.common import DefusedXmlException
from llama_cloud import APIConnectionError, InternalServerError, LlamaCloud, RateLimitError
from psycopg import sql

from nuron_ai import db
from nuron_ai.core import object_key, parse_header
from nuron_ai.storage import ObjectStorage, from_env as storage_from_env

logger = logging.getLogger(__name__)

_MAX_DOCX_XML_BYTES = 25 * 1024 * 1024
_MAX_DOCX_COMPRESSION_RATIO = 100
_LEASE_SECONDS = 300.0
_PDF_PARSE_TIMEOUT_SECONDS = 240.0
_MAX_ATTEMPTS = 5
_RETRY_DELAY_SECONDS = 60.0
_POLL_DELAY_SECONDS = 5.0


class ExtractionDeferred(RuntimeError):
    """Raised when extraction is unavailable under the current configuration."""


class PermanentExtractionError(RuntimeError):
    """Raised when retrying the same immutable bytes can never succeed."""


class _ReleaseOperation(Enum):
    DEFER = "defer"
    FAIL = "fail"
    RETRY = "retry"
    EXTRACTED = "extracted"
    PARSED = "parsed"


_RELEASE_SET_SQL = {
    _ReleaseOperation.DEFER: sql.SQL(
        "next_attempt_at = now() + %(retry_delay_seconds)s * interval '1 second'"
    ),
    _ReleaseOperation.FAIL: sql.SQL("state = 'failed'"),
    _ReleaseOperation.RETRY: sql.SQL(
        """
        attempt_count = attempt_count + 1,
        next_attempt_at = now() + %(retry_delay_seconds)s * interval '1 second',
        state = CASE WHEN attempt_count + 1 >= %(max_attempts)s
                     THEN 'failed'::nuron_ai.pipeline_state
                     ELSE state END
        """
    ),
    _ReleaseOperation.EXTRACTED: sql.SQL(
        "state = 'extracted', body = %(body)s, attempt_count = 0, next_attempt_at = NULL"
    ),
    _ReleaseOperation.PARSED: sql.SQL(
        """
        state = 'parsed', title = %(title)s, author = %(author)s,
        author_source = %(author_source)s, document_date = %(document_date)s, tags = %(tags)s,
        attempt_count = 0, next_attempt_at = NULL
        """
    ),
}


def extract_markdown(
    data: bytes,
    filename: str,
    *,
    llama_parse_api_key: str | None,
    llama_parse_tier: str | None,
) -> str:
    """Converts one landed file's bytes to Markdown for header parsing."""
    suffix = Path(filename).suffix.lower()
    if suffix in {".md", ".txt"}:
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as err:
            raise PermanentExtractionError(f"{suffix} content is not valid UTF-8") from err

    if suffix == ".docx":
        tag = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        try:
            with zipfile.ZipFile(BytesIO(data)) as archive:
                document_info = archive.getinfo("word/document.xml")
                if document_info.file_size > _MAX_DOCX_XML_BYTES:
                    raise PermanentExtractionError(
                        f"word/document.xml exceeds {_MAX_DOCX_XML_BYTES} bytes"
                    )
                if (
                    document_info.file_size
                    > document_info.compress_size * _MAX_DOCX_COMPRESSION_RATIO
                ):
                    raise PermanentExtractionError(
                        "word/document.xml exceeds the maximum compression ratio"
                    )
                with archive.open(document_info) as document:
                    document_xml = document.read(_MAX_DOCX_XML_BYTES + 1)
            if len(document_xml) > _MAX_DOCX_XML_BYTES:
                raise PermanentExtractionError(
                    f"word/document.xml exceeds {_MAX_DOCX_XML_BYTES} bytes"
                )
            root = ET.fromstring(document_xml, forbid_dtd=True)
        except DefusedXmlException as err:
            raise PermanentExtractionError(f"unsafe .docx XML: {err}") from err
        except (KeyError, zipfile.BadZipFile, ET.ParseError) as err:
            raise PermanentExtractionError(f"unreadable .docx: {err}") from err

        paragraphs = []
        for paragraph in root.iter(f"{tag}p"):
            paragraph_text = "".join(node.text or "" for node in paragraph.iter(f"{tag}t"))
            style = paragraph.find(f"{tag}pPr/{tag}pStyle")
            if style is not None and style.get(f"{tag}val") == "Heading1":
                paragraph_text = f"# {paragraph_text}"
            paragraphs.append(paragraph_text)
        return "\n\n".join(paragraphs)

    if suffix == ".pdf":
        if not llama_parse_api_key or not llama_parse_tier:
            raise ExtractionDeferred(
                "PDF extraction is disabled -- set LLAMA_PARSE_ENABLED=true, "
                "LLAMA_PARSE_API_KEY and LLAMA_PARSE_TIER"
            )

        client = LlamaCloud(api_key=llama_parse_api_key)
        uploaded = client.files.create(file=(filename, data, "application/pdf"), purpose="parse")
        try:
            result = client.parsing.parse(
                tier=llama_parse_tier,
                version="latest",
                file_id=uploaded.id,
                expand=["markdown_full"],
                timeout=_PDF_PARSE_TIMEOUT_SECONDS,
            )
            text = result.markdown_full or ""
            if not text.strip():
                raise PermanentExtractionError(
                    "PDF extracted to near nothing -- likely a scanned image with no text layer "
                    "(no OCR in this pipeline)"
                )
            return text
        finally:
            try:
                client.files.delete(file_id=uploaded.id)
            except Exception:  # cleanup must not mask the extraction outcome
                logger.warning("could not delete LlamaCloud upload %s", uploaded.id)

    raise ValueError(f"unsupported extension for extraction: {suffix!r}")


def _claim(
    conn: psycopg.Connection,
    worker_id: str,
    state: str,
    *,
    lease_seconds: float = _LEASE_SECONDS,
) -> tuple[Any, ...] | None:
    """Claims one claimable row in `state`, bumping the lease -- None when nothing to take."""
    claimed = conn.execute(
        """
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
        RETURNING content_hash, original_filename, body, lease_token
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
    operation: _ReleaseOperation,
    params: dict[str, Any],
) -> None:
    """Updates and releases a row only while this worker still holds its lease."""
    conn.execute(
        sql.SQL(
            """
        UPDATE nuron_ai.documents
        SET {},
            claimed_by = NULL,
            lease_until = NULL
        WHERE content_hash = %(content_hash)s
          AND claimed_by = %(worker_id)s
          AND lease_token = %(lease_token)s
        """
        ).format(_RELEASE_SET_SQL[operation]),
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
    """Claims one landed row and advances, defers, retries, or fails extraction."""
    claimed = _claim(conn, worker_id, "landed", lease_seconds=lease_seconds)
    if claimed is None:
        return False

    digest, original_filename, _, lease_token = claimed
    try:
        data = storage.get(object_key(digest))
        markdown = extract_markdown(
            data,
            original_filename,
            llama_parse_api_key=llama_parse_api_key,
            llama_parse_tier=llama_parse_tier,
        )
    except ExtractionDeferred as err:
        logger.info("extraction deferred for %s (%s): %s", digest, original_filename, err)
        _release(
            conn,
            digest,
            worker_id,
            lease_token,
            _ReleaseOperation.DEFER,
            {"retry_delay_seconds": _RETRY_DELAY_SECONDS},
        )
        return True
    except PermanentExtractionError as err:
        # Permanent, not transient -- retrying can never succeed (a scanned PDF stays
        # scanned), and each retry against LlamaParse would be another paid call for
        # nothing. Fail straight away instead of burning the attempt budget on it.
        logger.warning("extraction permanently failed for %s (%s): %s", digest, original_filename, err)
        _release(conn, digest, worker_id, lease_token, _ReleaseOperation.FAIL, {})
        return True
    except (OSError, APIConnectionError, RateLimitError, InternalServerError) as err:
        logger.warning("extraction failed for %s (%s): %s", digest, original_filename, err)
        _release(
            conn,
            digest,
            worker_id,
            lease_token,
            _ReleaseOperation.RETRY,
            {"retry_delay_seconds": _RETRY_DELAY_SECONDS, "max_attempts": _MAX_ATTEMPTS},
        )
        return True

    _release(
        conn,
        digest,
        worker_id,
        lease_token,
        _ReleaseOperation.EXTRACTED,
        {"body": markdown},
    )
    return True


def parse_pending(
    conn: psycopg.Connection,
    worker_id: str,
    source_owner: str | None,
    lease_seconds: float = _LEASE_SECONDS,
) -> bool:
    """Claims one extracted row and advances it through deterministic header parsing."""
    claimed = _claim(conn, worker_id, "extracted", lease_seconds=lease_seconds)
    if claimed is None:
        return False

    digest, original_filename, body, lease_token = claimed
    try:
        header = parse_header(body, filename=original_filename, source_owner=source_owner)
    except Exception as err:
        logger.warning("parse failed for %s (%s): %s", digest, original_filename, err)
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
        _ReleaseOperation.PARSED,
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
