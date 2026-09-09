"""Postgres connection for nuron_ai -- always as nuron_ai_svc (schema/schema.sql).

Host is published on the compose stack only for local dev (see docker-compose.yml's
NU-005 note) -- nuron-ai isn't containerized yet.
"""

import os

import psycopg


def from_env() -> psycopg.Connection:
    """Connects as nuron_ai_svc using NURON_AI_DB_HOST/PORT, POSTGRES_DB, NURON_AI_DB_PASSWORD."""
    return psycopg.connect(
        host=os.environ["NURON_AI_DB_HOST"],
        port=os.environ["NURON_AI_DB_PORT"],
        dbname=os.environ["POSTGRES_DB"],
        user="nuron_ai_svc",
        password=os.environ["NURON_AI_DB_PASSWORD"],
    )
