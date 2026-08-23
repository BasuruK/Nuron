-- Nuron Postgres schema (NU-001).
--
-- Two schemas, two login roles, no cross-schema grants: nuron_ai owns the ingest
-- pipeline and its state (docs/tracer-bullet-01.md); nuron_api owns the user-facing
-- surface and gets its own tables in a later story. Isolation is enforced by never
-- granting nuron_api_svc anything on nuron_ai (or vice versa) -- not by convention.
--
-- Runs once, on a fresh volume, via docker-entrypoint-initdb.d (see docker-compose.yml).
-- Role passwords come from the container's own environment via psql \getenv, so no
-- secret is ever committed here.

\getenv nuron_ai_password NURON_AI_DB_PASSWORD
\getenv nuron_api_password NURON_API_DB_PASSWORD

-- A completely unset env var already makes \getenv leave the variable undefined, which
-- turns the CREATE ROLE below into a hard syntax error -- caught by ON_ERROR_STOP=1
-- (the entrypoint's own default) without any help from us. But docker compose turns a
-- password missing from .env into an empty string, not an unset variable, and an empty
-- string is a value :'var' substitutes just fine -- silently creating a service role
-- with no password. Catch that case explicitly instead of assuming the env is populated.
SELECT CASE WHEN :'nuron_ai_password' = '' OR :'nuron_api_password' = ''
            THEN 'true' ELSE 'false' END AS empty_password \gset
\if :empty_password
DO $$
BEGIN
    RAISE EXCEPTION 'NURON_AI_DB_PASSWORD and NURON_API_DB_PASSWORD must both be set to a non-empty value (see .env.example) -- refusing to create service roles with an empty password';
END $$;
\endif

CREATE ROLE nuron_ai_svc LOGIN PASSWORD :'nuron_ai_password';
CREATE ROLE nuron_api_svc LOGIN PASSWORD :'nuron_api_password';

-- Schemas are owned by their service role from creation, so everything built inside
-- them below (as that role, via SET ROLE) is owned by it too -- no separate GRANT
-- statements needed, and nothing to accidentally grant to the other role.
CREATE SCHEMA nuron_ai AUTHORIZATION nuron_ai_svc;
CREATE SCHEMA nuron_api AUTHORIZATION nuron_api_svc;

SET ROLE nuron_ai_svc;

-- Enums ----------------------------------------------------------------------

-- Ingest pipeline state machine (docs/tracer-bullet-01.md, "Pipeline state machine").
-- `failed` is a terminal state reached from any automated transition that exhausts
-- its retry budget -- not just the tail of the happy-path chain.
CREATE TYPE nuron_ai.pipeline_state AS ENUM (
    'landed',
    'extracted',
    'parsed',
    'awaiting_review',
    'content_approved',
    'compiled',
    'awaiting_merge_confirm',
    'persisted',
    'failed'
);

-- How much a document's `author` value can be trusted (CONTEXT.md "author_source").
CREATE TYPE nuron_ai.author_source AS ENUM (
    'extracted',
    'default',
    'unknown'
);

-- Which ingestion path first landed this document (tracer-bullet-01.md assertion A12).
CREATE TYPE nuron_ai.entry_point AS ENUM (
    'watched_directory',
    'upload'
);

-- Tables -----------------------------------------------------------------------

-- Landing Zone + pipeline queue. This table *is* the work queue (ADR-0001, "no
-- message broker") -- a worker claims a row with SELECT ... FOR UPDATE SKIP LOCKED.
CREATE TABLE nuron_ai.documents (
    content_hash        TEXT PRIMARY KEY CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    entry_point         nuron_ai.entry_point NOT NULL,
    original_filename   TEXT NOT NULL,
    state               nuron_ai.pipeline_state NOT NULL DEFAULT 'landed',

    -- Worker claim / lease (tracer-bullet-01.md "Worker claim / lease").
    claimed_by          TEXT,
    lease_until         TIMESTAMPTZ,
    lease_token         BIGINT NOT NULL DEFAULT 0,

    -- Backoff bookkeeping for the transition currently in flight (tracer-bullet-01.md
    -- "Attempts"). Reset to 0 / NULL whenever the row advances to a new state.
    attempt_count       INTEGER NOT NULL DEFAULT 0,
    next_attempt_at     TIMESTAMPTZ,

    -- Content Header + body: filled by the deterministic parser on landed -> parsed,
    -- then freely editable by a reviewer up to approval (CONTEXT.md "Reviewed Source").
    title                TEXT,
    author               TEXT,
    author_source        nuron_ai.author_source,
    document_date        DATE,
    tags                 TEXT[] NOT NULL DEFAULT '{}',
    body                 TEXT,

    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE nuron_ai.documents IS
    'Landing Zone + pipeline queue. One row per distinct content_hash; the state machine is the queue, not a separate broker.';
COMMENT ON COLUMN nuron_ai.documents.content_hash IS
    'sha256(bytes), hex-encoded. Document identity and the RustFS object key {hash[0:2]}/{hash} (ADR-0005) -- never a UUID.';
COMMENT ON COLUMN nuron_ai.documents.lease_token IS
    'Incremented on every claim. The state-transition UPDATE is conditioned on (claimed_by, lease_token) so a reclaimed lease cannot advance the row out from under its new holder.';

-- Immutable, versioned snapshot frozen at review-gate-1 approval. This, not the raw
-- file, is the Evidence root that evidence_span offsets resolve into (CONTEXT.md).
CREATE TABLE nuron_ai.reviewed_sources (
    id                   BIGSERIAL PRIMARY KEY,
    content_hash         TEXT NOT NULL REFERENCES nuron_ai.documents (content_hash),
    version              INTEGER NOT NULL CHECK (version > 0),
    title                TEXT,
    author               TEXT,
    author_source        nuron_ai.author_source,
    document_date        DATE,
    tags                 TEXT[] NOT NULL DEFAULT '{}',
    body                 TEXT NOT NULL,
    approved_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (content_hash, version)
);

COMMENT ON TABLE nuron_ai.reviewed_sources IS
    'Frozen per-version snapshot produced at review gate 1. Evidence root: citations resolve here, never into the raw file.';

-- Human-confirmed aliases from merge gate 2 (ADR "merges-not-links"). Written before
-- the Neo4j rewrite-and-delete, never after -- resolve_key checks this table first.
CREATE TABLE nuron_ai.entity_aliases (
    alias_key            TEXT NOT NULL,
    label                TEXT NOT NULL,
    survivor_key         TEXT NOT NULL,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (alias_key, label)
);

COMMENT ON TABLE nuron_ai.entity_aliases IS
    'Confirmed merges: normalize(alias name) + label -> the surviving natural key. No typed links live here -- link creation is deferred (ADR merges-not-links).';

-- Refcounted provenance: one row per (node, document) ref. A node's refcount is the
-- number of rows for its node_key; re-approving a document replaces only its own rows.
CREATE TABLE nuron_ai.node_provenance (
    node_key             TEXT NOT NULL,
    content_hash         TEXT NOT NULL REFERENCES nuron_ai.documents (content_hash) ON DELETE CASCADE,
    reviewed_source_id   BIGINT NOT NULL REFERENCES nuron_ai.reviewed_sources (id),
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (node_key, content_hash)
);

COMMENT ON TABLE nuron_ai.node_provenance IS
    'Refcounted provenance set per graph node. A node is deleted only when its last ref (row) here is released.';

-- Indexes ------------------------------------------------------------------

-- Worker claim query filters on state + lease/backoff (tracer-bullet-01.md "Worker
-- claim / lease"); dropping the backoff columns from this index would make that scan
-- a full table scan once the queue has more than a handful of rows.
CREATE INDEX documents_claim_idx ON nuron_ai.documents (state, lease_until, next_attempt_at);

-- "Release all refs for this document" (re-approve, or document deletion) filters by
-- content_hash alone, which the (node_key, content_hash) primary key doesn't serve.
CREATE INDEX node_provenance_content_hash_idx ON nuron_ai.node_provenance (content_hash);

RESET ROLE;

-- nuron_api owns no tables yet -- its data model is a later story (NU-012). The
-- `CREATE SCHEMA nuron_api AUTHORIZATION nuron_api_svc` above already makes the schema
-- exist, owned by its service role, so the role/isolation boundary is real from day
-- one without waiting on that later story.
