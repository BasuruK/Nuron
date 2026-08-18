---
status: accepted
---

# No message broker: Postgres queue for ingestion, synchronous path for query

The PRD (FR-4) requires RabbitMQ as a required data-plane component dispatching ingest, compile,
curate, and reply work as pub/sub events, with a "Default Agent" handling both ingest and reply.
For this slice we removed RabbitMQ entirely. The ingest pipeline blocks on a human at two gates,
which makes it human-paced — broker throughput is moot — and the review queue (a Postgres row
claimed with `SELECT ... FOR UPDATE SKIP LOCKED`) already *is* the work queue; a broker would be a
second copy of the same state that can disagree with the table about what stage a document is in.
Query answering follows the same logic in the other direction: it is synchronous end-to-end
(`web → api → ai → retrieve → generate → stream` over SSE), not dispatched as an async event. This
also resolves the PRD adversarial review's F-2 (SSE streaming vs. async-only RabbitMQ dispatch)
and F-3 (two sections both claiming to answer the same user journey) — "reply" (write-back to
originating sources) is redefined as a separate, deferred v1.1 concept, distinct from query
answering, and the "Default Agent" name is retired for this slice (see `CONTEXT.md`).

## Considered options

- **RabbitMQ as specified in the PRD**, with the review gate and the persisted pipeline-state
  table effectively duplicating the state a broker would also track — rejected: two systems that
  can disagree about pipeline stage is worse than one, and nothing in this slice needs broker
  throughput.
- **RabbitMQ only for the query path**, keeping FR-9's SSE streaming synchronous — rejected: it
  would need a request/reply-with-correlation-ID bridge that no requirement owns, purely to
  satisfy an FR that itself never resolved whether "reply" meant this.

## Consequences

Restart-safety comes from one committed table, not from keeping a broker and a table in sync —
but that claim only holds because the writes to Neo4j and RustFS made from that table's rows are
themselves crash-safe, not because the table alone guarantees it. Concretely: each row is claimed
with an explicit worker lease (`claimed_by`/`lease_until`); a crash mid-external-call expires the
lease and another worker resumes from the persisted state rather than from scratch; RustFS writes
are idempotent/conditional on the content hash so a retried write after an unacked crash cannot
duplicate an object; Neo4j writes go through `plan_delta` (`add | update | unchanged | drop_ref`),
so a retried persist re-applies the same delta rather than double-applying it. Restart-safety is
this combination, not the Postgres row in isolation.

If a future slice needs true async fan-out (e.g. the deferred Curator's scheduled re-curation, or
v1.1 write-back), that is new infrastructure to add deliberately, not a broker to reinstate.
