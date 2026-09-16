"""Postgres connection for nuron_ai -- always as nuron_ai_svc (schema/schema.sql).

Host is published on the compose stack only for local dev (see docker-compose.yml's
NU-005 note) -- nuron-ai isn't containerized yet.
"""

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
    returning: sql.Composable,
    *,
    lease_seconds: float,
) -> tuple[Any, ...] | None:
    """Claims one claimable row in `state`, bumping the lease -- None when nothing to take.

    Shared by every pipeline stage (docs/tracer-bullet-01.md "Worker claim / lease"): the
    review queue reuses the exact same SKIP LOCKED contract as the automated workers.
    """
    # `returning` is always a hardcoded sql.SQL literal from a trusted call site (never
    # user input) -- same shape as extraction.py's _release, just for a dynamic RETURNING list.
    claimed = conn.execute(  # nosec B608
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
            RETURNING {returning}
            """
        ).format(returning=returning),
        {"worker_id": worker_id, "lease_seconds": lease_seconds, "state": state},
    ).fetchone()
    conn.commit()
    return claimed
