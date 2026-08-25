#!/bin/sh
# Postgres readiness: reachable, and schema.sql ran to completion.
# Marker text lives only in schema.sql (COMMENT ON SCHEMA nuron_ai).
set -eu

schema_sql="${SCHEMA_SQL:-}"
if [ -z "$schema_sql" ]; then
  if [ -f /docker-entrypoint-initdb.d/01-schema.sql ]; then
    schema_sql=/docker-entrypoint-initdb.d/01-schema.sql
  else
    schema_sql=$(CDPATH= cd -- "$(dirname "$0")/../schema" && pwd)/schema.sql
  fi
fi

expected=$(awk -F "'" '/^COMMENT ON SCHEMA nuron_ai IS / { print $2; exit }' "$schema_sql")
if [ -z "$expected" ]; then
  echo 'FAIL: COMMENT ON SCHEMA nuron_ai not found in schema.sql' >&2
  exit 1
fi

if [ "${1:-}" = --self-test ]; then
  compose=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)/docker-compose.yml
  if grep -F "$expected" "$compose" >/dev/null; then
    echo 'FAIL: schema marker is hardcoded in docker-compose.yml' >&2
    exit 1
  fi
  echo 'OK: schema marker not duplicated in docker-compose.yml'
  exit 0
fi

if ! psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c 'SELECT 1' >/dev/null; then
  echo 'postgres connection/auth/database failed' >&2
  exit 1
fi

got=$(psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc \
  "SELECT COALESCE(obj_description(oid, 'pg_namespace'), '') FROM pg_namespace WHERE nspname = 'nuron_ai'")

if [ "$got" != "$expected" ]; then
  cat >&2 <<'EOF'
schema incomplete (init skipped or partial). From host: docker compose exec postgres sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f /docker-entrypoint-initdb.d/01-schema.sql'  or wipe ALL volumes (postgres, neo4j, rustfs): docker compose down -v
EOF
  exit 1
fi
