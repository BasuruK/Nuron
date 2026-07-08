---
title: "Nuron PRD — Edge Case Hunter Review"
created: 2026-07-08
reviewed_doc: prd-Nuron-2026-07-08/prd.md
reviewer_methodology: Adversarial edge-case analysis across FRs 1-17 + cross-cutting requirements
status: Draft for Architecture phase validation
---

# Edge Case Hunter Review — Nuron PRD

Adversarial edge-case review of prd.md, focusing on boundary conditions, failure modes, race conditions, and unvalidated assumptions that could cause silent data loss, inconsistency, or decision-continuity breaks.

**Scope:** FRs 1-17 + §5 Cross-Cutting Requirements. **Severity:** Critical (data loss/continuity break), High (silent failure/inconsistency), Medium (limits/gaps), Low (clarification).

---

## 1. Ingest & Structuring (FR-1, FR-2)

### EC-1.1 [Critical] — File scan concurrency and partial writes
A file being appended-to during a scan interval can be read mid-write, causing silent content loss or duplication on re-scan. FR-1 doesn't specify atomicity. *Recommendation:* checksum/archive on successful ingest; require atomic (move-based) drops, or a dirty-bit hysteresis window before reading.

### EC-1.2 [High] — File encoding / non-UTF-8 graceful failure
FR-1/FR-2 say bad files are logged and flagged, but don't say whether the original is retained for admin review. *Recommendation:* quarantine directory + timestamped error log + admin UI retry.

### EC-1.3 [High] — File larger than LLM context window
No max-file-size threshold is specified; an oversized file could silently fail structuring. *Recommendation:* define a size threshold and a chunking strategy with `parent_file_id` linkage.

### EC-1.4 [Medium] — Empty Content Header fields and extraction ambiguity
No explicit Subject/Reason in a raw file forces LLM inference, which risks non-determinism that undermines the Curator's hash-tree model (FR-7). *Recommendation:* pin temperature=0, document the extraction prompt, maintain a canonical regression test suite.

---

## 2. Pipeline & Event Reliability (FR-3, FR-4)

### EC-2.1 [Critical] — Message ordering through multi-stage pipeline
No per-document ordering guarantee is specified; out-of-order compilation could invert Supersession direction. *Recommendation:* enforce per-document sequencing or rely on curator-side reconciliation using timestamps.

### EC-2.2 [Critical] — Redelivery semantics and exactly-once vs. at-least-once
FR-4 guarantees at-least-once delivery but not idempotency; redelivery + LLM non-determinism could produce duplicate/divergent LLM-Wiki outputs for the same input. *Recommendation:* deterministic message IDs (content hash) + a de-duplication table.

### EC-2.3 [Critical] — Dead-letter queue and poison-pill handling
No DLQ is specified for messages that repeatedly fail (e.g., malformed content). Risk of silent message loss or a hung queue. *Recommendation:* configure a Dead Letter Exchange, low max-retry, and an admin DLQ inspection panel.

### EC-2.4 [High] — Compiler failure mid-generation
A crash mid-stream-generation followed by redelivery could produce two divergent compilations of the same source. *Recommendation:* atomic-write-on-completion + content-hash/source-ID tagging for duplicate detection at the graph layer.

---

## 3. Graph Persistence & Curation (FR-5, FR-6, FR-7)

### EC-3.1 [Critical] — Decision Supersession with N-way conflicts
FR-6 implicitly assumes a linear chain; the PRD doesn't address three-plus independent sources creating simultaneous candidate decisions about the same question. *Recommendation:* model Supersession as a DAG, not a strict chain, with an explicit conflict-resolution strategy.

### EC-3.2 [Critical] — Entity deduplication and merging across documents
FR-3/FR-5 assume near-duplicate content compiles to one Core Entity, but don't describe merge logic when the same real-world decision is named differently across two LLM-Wiki documents. *Recommendation:* curator-driven entity-similarity scan + admin-reviewed merge queue, with a documented similarity threshold.

### EC-3.3 [High] — Touched-subtree curation assumption validation
The SM-5 10-minute/10k-node/1%-touched budget could be violated if a single change has a large blast radius (many downstream dependents). *Recommendation:* validate empirically in Architecture on a representative seed corpus before treating SM-5 as committed.

### EC-3.4 [Medium] — Curator crash recovery and partial curation
No mechanism is specified for resuming/rolling back a curator pass interrupted mid-way. *Recommendation:* a `curator_pass` tracking table with per-node pass IDs and a `status=failed` restart-clean semantics.

### EC-3.5 [Medium] — Persistence idempotency key specification
FR-5's idempotency claim doesn't define what makes two compiled documents "the same" across a schema version change. *Recommendation:* define idempotency key as `(source_file_id, schema_version)` and a migration path for version bumps.

---

## 4. Query & Retrieval (FR-8, FR-9)

### EC-4.1 [High] — Conflicting results from hybrid retrieval
No conflict-resolution rule when vector/structural/BM25 disagree on the top result. *Recommendation:* weighted combination with a "near-tie" disclosure mode (return top-N with a note) rather than a forced single answer.

### EC-4.2 [High] — Query response grounding when cited Evidence has since been deleted (FR-15)
A Decision could cite Evidence that's later removed via right-to-be-forgotten, orphaning the citation. *Recommendation:* soft-delete Evidence nodes with a `status` field (`available`/`deleted`/`archived`) surfaced in query responses.

### EC-4.3 [High] — SSE connection dropout and query state consistency
FR-9's "re-query safely" isn't defined precisely — does a re-query reuse a partially-completed background result or start over? *Recommendation:* deterministic query IDs, cache only complete results, return cached result on re-query rather than re-invoking the LLM.

### EC-4.4 [Medium] — Query context window limits and truncation
No `max_results`/pagination behavior is defined for queries matching very large result sets. *Recommendation:* default/max result caps with pagination or a summary-first response mode.

---

## 5. Agents (FR-10, FR-11)

### EC-5.1 [High] — Default Agent request deduplication under load
Client-side timeout-and-retry could cause the same logical request to be processed twice. *Recommendation:* require a client-supplied deterministic `request_id` and a dedup window on the Default Agent.

### EC-5.2 [High] — Agent Template scope being empty or too permissive
UJ-3 mentions a warning for "too little content," but no minimum threshold is defined, risking a silently-useless enabled agent. *Recommendation:* define a minimum node-count threshold before enabling, surfaced pre-enablement in the admin UI.

### EC-5.3 [Medium] — Agent Template version management and breaking changes
No versioning is defined for template definitions across Nuron releases, risking incompatibility when an existing agent instance's template schema changes underneath it. *Recommendation:* version every template; check compatibility on upgrade, auto-migrate or disable incompatible instances.

---

## 6. Access Control & Governance (FR-12–FR-17)

### EC-6.1 [Critical] — No admins in the system (bootstrap problem)
FR-12 doesn't specify how the very first admin account is created on a fresh deployment. *Recommendation:* first-launch setup wizard or a `nuron-setup-admin` CLI requiring local shell access.

### EC-6.2 [Critical] — Admin self-disable and lockout
No safeguard against the last remaining admin disabling their own account. *Recommendation:* block disabling the sole remaining admin; provide a CLI break-glass path.

### EC-6.3 [Critical] — Tenant ID trust boundary and request-scope verification
FR-14 says requests are scoped server-side "regardless of what's in the request body," but doesn't specify how the tenant ID is derived from the authenticated session — a gap that, if implemented incorrectly, is a cross-tenant data leak. *Recommendation:* tenant ID must be embedded and verified from the auth token only; request-body tenant IDs are ignored entirely; missing/invalid token tenant claims reject with 401.

### EC-6.4 [High] — Rate-limit bypass / noisy-neighbor via internal agent
FR-16's per-tenant rate limit doesn't distinguish a legitimate high-volume internal automation agent from a runaway one — one bad actor could exhaust a tenant's whole quota for human users. *Recommendation:* separate rate-limit buckets for interactive users vs. registered agents.

### EC-6.5 [High] — Right-to-be-forgotten partial deletion and data fragmentation
FR-15 doesn't enumerate exactly what "deletion" covers (backups? queue-in-flight messages? caches/logs?), risking partial deletion that fails a right-to-be-forgotten guarantee. *Recommendation:* explicitly enumerate deletion scope (graph, edges, audit, config, queue, backups, cache, logs) and cascade via a `tenant_deleted_at` marker.

### EC-6.6 [Medium] — Audit log completeness and tamper resistance
FR-17 doesn't address tamper-evidence (e.g., manual deletion of audit rows breaking traceability). *Recommendation:* append-only audit storage, optionally hash-chained entries.

---

## 7. Cross-Cutting Concerns

### EC-7.1 [Critical] — Schema evolution and backward compatibility
No `schema_version` concept exists for LLM-Wiki documents or graph nodes; a future required-field addition could break queries against older nodes. *Recommendation:* version every persisted document/node and support lazy or eager migration.

### EC-7.2 [Critical] — Graceful degradation under Neo4j unavailability
No fallback is specified if Neo4j is temporarily unreachable — queries could fail completely rather than degrade. *Recommendation:* a caching layer for recent results plus a circuit breaker for fast, clear failure.

### EC-7.3 [Medium] — Multi-tenant backward compatibility (v1 → v2 path)
v1's tenant ID scheme needs to be guaranteed present (even if fixed/single-value) so v2's multi-tenant migration isn't ambiguous. *Recommendation:* require an explicit (even if constant) `tenant_id` on every v1 record from day one.

### EC-7.4 [Medium] — Observability and debugging hooks for support
No logging/metrics/tracing requirements are specified, which will make production support difficult. *Recommendation:* structured logging of every pipeline stage transition + Prometheus-style latency/count metrics.

### EC-7.5 [Low] — API versioning and breaking changes
No API versioning scheme (e.g., `/v1/...`) is specified for `nuron-api`, risking breakage for customer automation on upgrade. *Recommendation:* version all endpoints and document a deprecation policy.
