#!/usr/bin/env bash
# Verifies nuron_api_svc cannot read nuron_ai.documents (NU-001 definition of done).
# Run against a stack already up: docker compose up -d postgres
set -euo pipefail

: "${NURON_API_DB_PASSWORD:?set NURON_API_DB_PASSWORD (see .env)}"
: "${POSTGRES_DB:?set POSTGRES_DB (see .env)}"
: "${POSTGRES_USER:?set POSTGRES_USER (see .env)}"

# Wait for schema to exist before running isolation checks.
# Bound each probe to the remaining deadline so a hung exec cannot stall forever.
deadline=$((SECONDS + 60))
until
    remaining=$((deadline - SECONDS))
    [ "$remaining" -gt 0 ] &&
        timeout "$remaining" docker compose exec -T postgres \
            psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At \
            -c "SELECT 1 FROM information_schema.schemata WHERE schema_name = 'nuron_ai'" \
            2>/dev/null | grep -q 1
do
    if [ "$SECONDS" -ge "$deadline" ]; then
        echo "FAIL: nuron_ai schema did not appear within 60s" >&2
        exit 1
    fi
    sleep 1
done

output=$(docker compose exec -T \
    -e PGPASSWORD="$NURON_API_DB_PASSWORD" \
    postgres \
    psql -U nuron_api_svc -d "$POSTGRES_DB" -At \
    -c "SELECT 1 FROM nuron_ai.documents LIMIT 1;" 2>&1) && status=0 || status=$?

if [ "$status" -eq 0 ]; then
    echo "FAIL: nuron_api_svc was able to read nuron_ai.documents" >&2
    echo "$output" >&2
    exit 1
fi

if ! grep -qi "permission denied" <<< "$output"; then
    echo "FAIL: query was rejected, but not by a permission check:" >&2
    echo "$output" >&2
    exit 1
fi

echo "OK: nuron_api_svc cannot read nuron_ai.documents ($output)"
