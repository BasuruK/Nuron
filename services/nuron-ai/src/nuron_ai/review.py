"""Review gate 1: review queue, body/header edits, Reviewed Source versioning (NU-007).

Every file blocks at `awaiting_review` for a human. Approval freezes an immutable Reviewed
Source version -- the Evidence root `evidence_span` offsets resolve into, never the raw file
(CONTEXT.md "Evidence root", docs/tracer-bullet-01.md "Human review gate 1").
"""

import difflib
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime

import psycopg
from psycopg import sql

from nuron_ai import db
from nuron_ai.core import AuthorSource, object_key
from nuron_ai.extraction import ExtractionDeferred, PermanentExtractionError, extract_markdown
from nuron_ai.storage import CorruptedWriteError, ObjectStorage

logger = logging.getLogger(__name__)

_REVIEW_LEASE_SECONDS = 1800.0  # a human review session, not a worker poll -- extraction.py's 300s would time out mid-edit.
_PROMOTION_LEASE_SECONDS = 300.0  # automatic flip, no human wait -- matches extraction.py's worker lease.
_POLL_DELAY_SECONDS = 5.0
_RETRY_DELAY_SECONDS = 60.0

_REVIEW_ITEM_COLUMNS = sql.SQL(
    "content_hash, original_filename, title, author, author_source, "
    "document_date, tags, body, lease_token"
)


@dataclass(frozen=True)
class PendingItem:
    """One row queued at `awaiting_review`, for the queue listing."""

    content_hash: str
    original_filename: str
    title: str | None
    created_at: datetime


@dataclass(frozen=True)
class ReviewItem:
    """One claimed row's full Content Header + body, ready for editing."""

    content_hash: str
    original_filename: str
    title: str | None
    author: str | None
    author_source: AuthorSource | None
    document_date: date | None
    tags: list[str]
    body: str
    lease_token: int


@dataclass(frozen=True)
class ReingestDiff:
    """A re-ingested file's raw content, diffed against its last approved version."""

    prior_version: int
    prior_body: str
    raw_diff: str


def list_pending(conn: psycopg.Connection) -> list[PendingItem]:
    """Lists every row awaiting review, oldest first -- the reviewer's queue view."""
    rows = conn.execute(
        """
        SELECT content_hash, original_filename, title, created_at
        FROM nuron_ai.documents
        WHERE state = 'awaiting_review'
        ORDER BY created_at
        """
    ).fetchall()
    conn.commit()
    return [PendingItem(*row) for row in rows]


def fetch_one(
    conn: psycopg.Connection,
    worker_id: str,
    lease_seconds: float = _REVIEW_LEASE_SECONDS,
) -> ReviewItem | None:
    """Claims the oldest awaiting_review row for one reviewer's session; None if the queue is empty."""
    claimed = db.claim(
        conn, worker_id, "awaiting_review", _REVIEW_ITEM_COLUMNS, lease_seconds=lease_seconds
    )
    if claimed is None:
        return None
    return ReviewItem(*claimed)


def save_edit(
    conn: psycopg.Connection,
    content_hash: str,
    worker_id: str,
    lease_token: int,
    *,
    title: str | None,
    author: str | None,
    author_source: AuthorSource | None,
    document_date: date | None,
    tags: list[str],
    body: str,
) -> bool:
    """Saves a reviewer's in-progress edits without releasing the claim; False if the lease was lost."""
    row = conn.execute(
        """
        UPDATE nuron_ai.documents
        SET title = %(title)s, author = %(author)s, author_source = %(author_source)s,
            document_date = %(document_date)s, tags = %(tags)s, body = %(body)s
        WHERE content_hash = %(content_hash)s
          AND claimed_by = %(worker_id)s
          AND lease_token = %(lease_token)s
          AND state = 'awaiting_review'
        RETURNING content_hash
        """,
        {
            "title": title,
            "author": author,
            "author_source": author_source,
            "document_date": document_date,
            "tags": tags,
            "body": body,
            "content_hash": content_hash,
            "worker_id": worker_id,
            "lease_token": lease_token,
        },
    ).fetchone()
    conn.commit()
    saved = row is not None
    if saved:
        # A rubber-stamped approval with no preceding edit-log entry is the failure mode the
        # spec calls out explicitly -- this line is what makes that observable after the fact.
        logger.info("worker %s saved edits for %s", worker_id, content_hash)
    return saved


def approve(
    conn: psycopg.Connection,
    content_hash: str,
    worker_id: str,
    lease_token: int,
) -> int | None:
    """Freezes the current row into a new Reviewed Source version; None if the lease was lost."""
    row = conn.execute(
        """
        SELECT original_filename, title, author, author_source, document_date, tags, body
        FROM nuron_ai.documents
        WHERE content_hash = %(content_hash)s
          AND claimed_by = %(worker_id)s
          AND lease_token = %(lease_token)s
          AND state = 'awaiting_review'
        FOR UPDATE
        """,
        {"content_hash": content_hash, "worker_id": worker_id, "lease_token": lease_token},
    ).fetchone()
    if row is None:
        conn.rollback()
        return None
    original_filename, title, author, author_source, document_date, tags, body = row

    # Serializes concurrent approvals across different content_hash rows that share this
    # filename -- otherwise two re-ingested versions could both read MAX(version)=0 and both
    # freeze as version 1 (UNIQUE(content_hash, version) can't catch that: content_hash differs).
    conn.execute(
        "SELECT pg_advisory_xact_lock(hashtext(%(original_filename)s))",
        {"original_filename": original_filename},
    )

    # Version lineage follows original_filename, not content_hash -- a re-ingested file lands
    # under a new content_hash every time (CONTEXT.md "Content hash"), so content_hash alone
    # would never accumulate more than one version.
    next_version_row = conn.execute(
        """
        SELECT COALESCE(MAX(rs.version), 0) + 1
        FROM nuron_ai.reviewed_sources rs
        JOIN nuron_ai.documents d ON d.content_hash = rs.content_hash
        WHERE d.original_filename = %(original_filename)s
        """,
        {"original_filename": original_filename},
    ).fetchone()
    if next_version_row is None:
        raise RuntimeError("COALESCE(...) aggregate query returned no row -- should be impossible")
    next_version = next_version_row[0]

    conn.execute(
        """
        INSERT INTO nuron_ai.reviewed_sources
            (content_hash, version, title, author, author_source, document_date, tags, body)
        VALUES (%(content_hash)s, %(version)s, %(title)s, %(author)s, %(author_source)s,
                %(document_date)s, %(tags)s, %(body)s)
        """,
        {
            "content_hash": content_hash,
            "version": next_version,
            "title": title,
            "author": author,
            "author_source": author_source,
            "document_date": document_date,
            "tags": tags,
            "body": body,
        },
    )
    transitioned = conn.execute(
        """
        UPDATE nuron_ai.documents
        SET state = 'content_approved', claimed_by = NULL, lease_until = NULL
        WHERE content_hash = %(content_hash)s
          AND claimed_by = %(worker_id)s
          AND lease_token = %(lease_token)s
          AND state = 'awaiting_review'
        RETURNING content_hash
        """,
        {"content_hash": content_hash, "worker_id": worker_id, "lease_token": lease_token},
    ).fetchone()
    if transitioned is None:
        conn.rollback()
        return None
    conn.commit()
    logger.info(
        "worker %s approved %s as reviewed_sources version %d", worker_id, content_hash, next_version
    )
    return next_version


def reingest_diff(
    conn: psycopg.Connection,
    storage: ObjectStorage,
    original_filename: str,
    new_content_hash: str,
    *,
    llama_parse_api_key: str | None,
    llama_parse_tier: str | None,
) -> ReingestDiff | None:
    """Diffs this pending file's raw content against the version last approved for its filename."""
    prior = conn.execute(
        """
        SELECT rs.content_hash, rs.version, rs.body
        FROM nuron_ai.reviewed_sources rs
        JOIN nuron_ai.documents d ON d.content_hash = rs.content_hash
        WHERE d.original_filename = %(original_filename)s
        ORDER BY rs.version DESC
        LIMIT 1
        """,
        {"original_filename": original_filename},
    ).fetchone()
    conn.commit()
    if prior is None:
        return None
    prior_content_hash, prior_version, prior_body = prior

    # Re-extract both raw byte sets rather than trust stored `body` values -- a reviewer's prior
    # edits already overwrote the pristine extracted text for the prior version. Extraction can be
    # legitimately unavailable (PDF support toggled off) or storage can have pruned an old object --
    # both mean "no diff to show" for the reviewer, not a crash.
    try:
        prior_raw = storage.get(object_key(prior_content_hash))
        new_raw = storage.get(object_key(new_content_hash))
        prior_text = extract_markdown(
            prior_raw,
            original_filename,
            llama_parse_api_key=llama_parse_api_key,
            llama_parse_tier=llama_parse_tier,
        )
        new_text = extract_markdown(
            new_raw,
            original_filename,
            llama_parse_api_key=llama_parse_api_key,
            llama_parse_tier=llama_parse_tier,
        )
    except (ExtractionDeferred, PermanentExtractionError, CorruptedWriteError, OSError) as err:
        logger.warning("could not compute reingest diff for %s: %s", original_filename, err)
        return None
    diff_lines = difflib.unified_diff(
        prior_text.splitlines(keepends=True),
        new_text.splitlines(keepends=True),
        fromfile=f"{original_filename}@v{prior_version}",
        tofile=f"{original_filename}@pending",
    )
    return ReingestDiff(prior_version=prior_version, prior_body=prior_body, raw_diff="".join(diff_lines))


def promote_parsed(
    conn: psycopg.Connection,
    worker_id: str,
    lease_seconds: float = _PROMOTION_LEASE_SECONDS,
) -> bool:
    """Claims one parsed row and promotes it straight to awaiting_review."""
    # Nothing computed here -- parsing (NU-006) already finished. This flip is what makes the
    # row reachable to list_pending/fetch_one: tracer-bullet-01.md "Flow" has no human step
    # between parsed and awaiting_review.
    claimed = db.claim(
        conn, worker_id, "parsed", sql.SQL("content_hash, lease_token"), lease_seconds=lease_seconds
    )
    if claimed is None:
        return False
    claimed_content_hash, lease_token = claimed
    cursor = conn.execute(
        """
        UPDATE nuron_ai.documents
        SET state = 'awaiting_review', claimed_by = NULL, lease_until = NULL
        WHERE content_hash = %(content_hash)s
          AND claimed_by = %(worker_id)s
          AND lease_token = %(lease_token)s
        """,
        {"content_hash": claimed_content_hash, "worker_id": worker_id, "lease_token": lease_token},
    )
    if cursor.rowcount == 0:
        conn.rollback()
        return False
    conn.commit()
    return True


def main() -> None:
    """Runs the parsed -> awaiting_review promotion worker forever, polling for pending rows."""
    logging.basicConfig(level=logging.INFO)
    worker_id = uuid.uuid4().hex

    while True:
        try:
            with db.from_env() as conn:
                promoted = promote_parsed(conn, worker_id)
        except Exception:
            logger.exception(
                "review promotion worker failed; retrying after %.0f seconds", _RETRY_DELAY_SECONDS
            )
            time.sleep(_RETRY_DELAY_SECONDS)
            continue
        if not promoted:
            time.sleep(_POLL_DELAY_SECONDS)


if __name__ == "__main__":
    main()
