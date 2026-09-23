"""Postgres connection for nuron_ai -- always as nuron_ai_svc (schema/schema.sql).

Host is published on the compose stack only for local dev (see docker-compose.yml's
NU-005 note) -- nuron-ai isn't containerized yet.
"""

import math
import os
from typing import Any

import psycopg
from psycopg import sql


def from_env() -> psycopg.Connection:
    """Connects as nuron_ai_svc using NURON_AI_DB_HOST/PORT, POSTGRES_DB, NURON_AI_DB_PASSWORD."""
    return psycopg.connect(
        host=os.environ["NURON_AI_DB_HOST"],
        port=os.environ["NURON_AI_DB_PORT"],
        dbname=os.environ["POSTGRES_DB"],
        user="nuron_ai_svc",
        password=os.environ["NURON_AI_DB_PASSWORD"],
    )


def claim(
    conn: psycopg.Connection,
    worker_id: str,
    state: str,
    *,
    lease_seconds: float,
) -> tuple[Any, ...] | None:
    """Claims one claimable row in `state`, bumping the lease -- None when nothing to take.

    Shared by every pipeline stage (docs/tracer-bullet-01.md "Worker claim / lease"): the
    review queue reuses the exact same SKIP LOCKED contract as the automated workers.
    """
    if not math.isfinite(lease_seconds) or lease_seconds <= 0:
        raise ValueError("lease_seconds must be finite and positive")
    # RETURNING is a fixed column list in this statement. Composing it from a caller
    # fragment (`sql.SQL(...) + returning` or .format()) is what Opengrep/Bandit flag
    # as SQL injection, even though the fragment was always a hardcoded literal.
    try:
        claimed = conn.execute(
            sql.SQL(
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
                RETURNING content_hash, original_filename, title, author, author_source,
                          document_date, tags, body, lease_token
                """
            ),
            {"worker_id": worker_id, "lease_seconds": lease_seconds, "state": state},
        ).fetchone()
        conn.commit()
        return claimed
    except psycopg.Error as err:
        try:
            conn.rollback()
        except Exception as rollback_err:
            err.add_note(
                "rollback after claim failure also failed: "
                f"{type(rollback_err).__name__}: {rollback_err}"
            )
        raise


def release(
    conn: psycopg.Connection,
    digest: str,
    worker_id: str,
    lease_token: int,
    set_sql: sql.Composable,
    params: dict[str, Any],
) -> None:
    """Updates and releases a claimed row only while this worker still holds its lease.

    Shared by every pipeline stage built on `claim()` above that needs the same
    lease-still-held guard on its release UPDATE (docs/tracer-bullet-01.md "Worker claim / lease").
    `set_sql` selects the caller's own static SET fragment for the outcome; only document
    data enters `params` as bound values.
    """
    try:
        cursor = conn.execute(  # nosemgrep
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
            ).format(set_sql),
            {**params, "content_hash": digest, "worker_id": worker_id, "lease_token": lease_token},
        )
    except psycopg.Error as err:
        try:
            conn.rollback()
        except Exception as rollback_err:
            err.add_note(
                "rollback after release failure also failed: "
                f"{type(rollback_err).__name__}: {rollback_err}"
            )
        raise
    if cursor.rowcount != 1:
        lost_lease = RuntimeError(f"lost lease while releasing {digest}")
        try:
            conn.rollback()
        except Exception as rollback_err:
            lost_lease.add_note(
                "rollback after lost lease also failed: "
                f"{type(rollback_err).__name__}: {rollback_err}"
            )
        raise lost_lease
    try:
        conn.commit()
    except psycopg.Error as err:
        try:
            conn.rollback()
        except Exception as rollback_err:
            err.add_note(
                "rollback after release failure also failed: "
                f"{type(rollback_err).__name__}: {rollback_err}"
            )
        raise
