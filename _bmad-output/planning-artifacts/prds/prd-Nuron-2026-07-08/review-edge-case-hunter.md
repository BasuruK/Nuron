---
title: "Nuron PRD — Edge Case Hunter Review"
created: 2026-07-13
reviewed_doc: prd-Nuron-2026-07-08/prd.md (updated 2026-07-13)
reviewer_methodology: Exhaustive edge-case/boundary-condition walk across FRs 1-17 + cross-cutting requirements, fresh pass following the 2026-07-08 reviewer gate, the Notion-comment reconciliation, and the Neo4j GraphRAG retrieval clarification
status: Draft for Architecture phase validation
---

# Edge Case Hunter Review — Nuron PRD (2026-07-13 pass)

Exhaustive path walk of `prd.md` in its current (2026-07-13) state. The 2026-07-08 reviewer gate's Critical findings (tenant-ID trust boundary, admin bootstrap/lockout, RTBF scope enumeration, curator determinism/Merkle fallback, supersession DAG modeling, near-duplicate threshold, hybrid-retrieval degraded path, Markdown format assumption, rate-limit buckets) are now reflected in the document text and are **not re-flagged here**. Items batched into Open Questions #7–17 (message ordering/idempotency/DLQ, schema versioning, audit retention/tamper-resistance, generic Neo4j degradation, observability, API versioning, SM-1 corpus, FR-10 benchmark params, Evidence/Episode terminology, MCP-style-settings terminology, retrieval parameterization) remain open by the PRD's own admission and are **not re-flagged** unless the 2026-07-13 changes created a materially new variant.

This pass focuses new analysis on the two 2026-07-13 changes: the `nuron-ai`/`nuron-api` service split, and FR-8's three-source hybrid retrieval fusion (HNSW + BM25/Tags + Neo4j GraphRAG → RRF → LLMRerank).

**Severity:** Critical (data loss/continuity break), High (silent failure/inconsistency), Medium (limits/gaps), Low (clarification).

---

## 1. Ingestion & Structuring (FR-1, FR-2)

### EC-1.1 [Low] — No canonical tag vocabulary for the Raw Ingest Agreement's Tags block
FR-2 lets the structuring agent freely enumerate functional-area tags per file, with no normalization or controlled vocabulary. Since FR-8 now names BM25/Tags as one of three first-class fusion sources, tag drift (e.g. "user onboarding" vs. "onboarding" vs. "new-hire onboarding" across files) silently degrades that source's consistency over time, independent of any single ingest failure. *Recommendation:* define a canonical tag taxonomy (or a normalization/synonym-folding step) at structuring time, before Tags are persisted.

---

## 2. Property Graph & Curation — Supersession & Lineage (FR-6, FR-15)

### EC-2.1 [High] — Supersession fan-out has no reconciliation rule
FR-6 explicitly handles fan-in (multiple predecessors merging into one successor via concurrent independent sources) but not fan-out: one predecessor independently superseded by two or more successors that are never reconciled with each other. Since FR-8 requires traversing "its Supersession chain to the present," a fork with two unreconciled "current" heads has no defined behavior — which head is "the present" is unspecified. *Recommendation:* extend FR-6's contradiction-reconciliation logic to also trigger when a Curator pass detects multiple un-merged successors of the same predecessor, and define what a lineage traversal returns when it hits an unresolved fork.

### EC-2.2 [High] — RTBF deletion of a mid-chain Decision doesn't specify Supersession-edge cleanup
FR-15 enumerates deletion scope (data directory, graph nodes/edges, queue messages, audit entries) but doesn't say what happens to Supersession edges pointing to/from a deleted mid-chain Decision node. Since FR-8 now treats lineage traversal as a required GraphRAG signal (not just citation text), a query traversing "to the present" through a deleted node could hit a dangling edge or an untraceable gap with no defined fallback (skip-and-continue vs. truncate-and-flag). *Recommendation:* define whether edge deletion collapses the chain (bridging predecessor→successor around the gap) or truncates traversal with an explicit "lineage incomplete past this point" marker.

---

## 3. Query & Retrieval — Three-Source Hybrid Fusion (FR-8)

### EC-3.1 [Critical] — Neo4j GraphRAG's candidate-list independence assumes seed entry points it may not have
FR-8 states each of HNSW, BM25/Tags, and Neo4j GraphRAG "produce a ranked candidate list for every query unless that source is unavailable" — implying three independently-failing sources. But graph traversal typically requires seed/entry nodes to expand from, and the PRD doesn't specify how GraphRAG obtains them independent of vector or lexical matching. If GraphRAG's seeds are themselves derived from HNSW/BM25 output, then "GraphRAG survives while HNSW+BM25 are down" isn't an achievable degraded state as written, undermining the three-independent-sources framing FR-8 and SM-C2 rely on. *Recommendation:* Architecture must specify GraphRAG's seed-selection mechanism and state explicitly whether it can run with zero vector/lexical input.

### EC-3.2 [High] — Vector/lexical-only degradation can silently drop Supersession lineage from an answer
If Neo4j GraphRAG is the source that's unavailable, FR-8 says "the remaining retrieval sources may continue" and the response "records the degraded path" — but doesn't require flagging that lineage/structural context (the product's core differentiator) was specifically the piece missing. A degraded answer could present a Decision as current without checking whether it was later superseded, with only a generic degraded-path marker rather than a lineage-specific warning. *Recommendation:* require the degraded-path record to explicitly flag when Supersession-lineage retrieval was skipped, not just that "a source" was unavailable.

### EC-3.3 [High] — No cross-source deduplication rule before RRF fusion
HNSW (semantic similarity) and Neo4j GraphRAG (structural neighborhood expansion) can plausibly surface the *same* graph node independently for a given query. FR-8 doesn't say whether identical candidates across the three ranked lists are deduplicated (by node ID) before RRF, or treated as separate entries. Without a stated rule, a node could either double-count its rank contribution or have its second occurrence silently dropped, both of which change fused ranking non-deterministically. *Recommendation:* define dedup-by-node-ID as a pre-fusion step, with the rule for which source's rank position "wins" for a deduplicated entry.

### EC-3.4 [High] — No defined behavior for simultaneous multi-source failure
FR-8 covers the single-source-down case ("if HNSW, BM25, Neo4j GraphRAG, or LLMRerank times out or errors ... the remaining retrieval sources may continue"). It does not address two-of-three, or all three, failing/returning empty simultaneously. It's unclear whether the system returns a total-failure response, falls through to the existing "no matching decision" response, or attempts RRF/LLMRerank over a single or empty candidate set. *Recommendation:* define a minimum-surviving-sources threshold below which the query fails explicitly rather than answering on a degenerate candidate set.

### EC-3.5 [Medium] — No RRF tie-breaking rule
FR-8 specifies RRF fuses the three ranked lists and LLMRerank orders the result, but not how ties in fused RRF score are broken (e.g., two candidates with identical fused rank contributions). *Recommendation:* pin a deterministic tie-break (e.g., recency, node type priority) alongside the RRF constant already deferred to Open Question #17.

---

## 4. Agents (FR-10)

### EC-4.1 [Medium] — "Reply" (FR-10, async) vs. "query" (FR-8, synchronous) path ambiguity
FR-10's Default Agent processes ingest and **reply** actions, "dispatched and consumed as RabbitMQ pub/sub events (per FR-4)" — i.e., asynchronously. Section 4.4 and FR-8 describe "a synchronous query agent" answering questions via hybrid retrieval. The PRD doesn't clarify whether a user-facing question is answered via the async "reply" path, the synchronous "query" path, or whether these are two names for mechanisms that must stay behaviorally identical. As written, they could diverge (e.g., different retrieval logic, different error handling) without anyone noticing. *Recommendation:* state explicitly whether FR-10's "reply" action and FR-8's "query" are the same logical operation invoked via different transports, or genuinely distinct.

---

## 5. `nuron-ai` / `nuron-api` Service Split — Cross-Service Boundaries

### EC-5.1 [Critical] — Tenant-ID trust boundary is defined only for the public API, not the internal service call
FR-14 requires the tenant ID be derived solely from "the caller's verified auth token/session — never a tenant identifier supplied in the request body" for API requests to `nuron-api`. But the actual tenant-scoped graph query executes inside `nuron-ai`, reached only via an internal nuron-api→nuron-ai call whose transport is explicitly deferred to Architecture (§5.4). No equivalent trust-boundary rule is stated for that internal call — if `nuron-ai` accepts a tenant identifier as a plain passed parameter without independently re-verifying it, a bug or compromise in `nuron-api` becomes a cross-tenant data leak at the one point where isolation matters most. *Recommendation:* extend FR-14's verified-token-only rule to the nuron-api→nuron-ai boundary, or require nuron-ai to independently validate the tenant claim rather than trusting nuron-api's forwarded value.

### EC-5.2 [High] — nuron-ai crash/restart mid-query is not covered by any stated contract
FR-9 defines behavior for a *client-side* dropped SSE connection ("the caller can re-query safely") but not for a `nuron-ai` crash or restart while generating a response. The restart-safety NFR (§5.1) is explicitly scoped to "the pipeline (FR-4)" — i.e., the async ingest/compile/curate/reply stages — leaving the synchronous query path's behavior on backend restart undefined (hung SSE stream vs. clean error vs. silent partial answer). *Recommendation:* extend restart-safety guarantees (or define a distinct contract) to cover the synchronous query/SSE path.

### EC-5.3 [Medium] — Cross-service config propagation for nuron-ai-side settings is unspecified
FR-13 guarantees a configuration change "takes effect without a service restart," but with `nuron-ai` and `nuron-api` now separate services (transport undecided), it's unclear whether settings that actually govern nuron-ai's behavior (retrieval parameters, LLM/embedding provider config) are pushed from nuron-api to nuron-ai, polled, or configured directly in nuron-ai out of band. *Recommendation:* clarify in Architecture which service owns which config surface and how changes propagate across the boundary.

### EC-5.4 [Medium] — Per-tenant rate limiting doesn't protect nuron-ai's shared capacity
FR-16 enforces rate limits per tenant inside `nuron-api` (Laravel). The expensive work — LLM calls, retrieval, reranking — now runs in the separate `nuron-ai` service. Many tenants each individually within their own quota could still collectively saturate nuron-ai's shared compute, since the limiting mechanism has no aggregate/global throttle on the service that actually bears the cost. *Recommendation:* consider whether nuron-ai needs its own concurrency/queue-depth cap independent of nuron-api's per-tenant limiter.

### EC-5.5 [High] — RTBF cascade has no defined behavior if nuron-ai is unreachable during deletion
FR-15's deletion enumerates the data directory, graph nodes/edges, queue messages, and audit entries as scope. The graph nodes/edges now live behind the internal nuron-ai service. If a deletion request from nuron-api can't reach nuron-ai at the moment it's issued (service down, network partition), there's no stated retry/queuing contract — a plausible outcome is nuron-api-side audit/config data purged while nuron-ai's graph data survives, violating the "no query returns content scoped to the deleted tenant" guarantee. *Recommendation:* require the deletion operation to durably queue the nuron-ai-side cascade and confirm completion before considering the deletion done, not fire-and-forget.

### EC-5.6 [Low] — Audit trail doesn't specify per-source provenance across the three retrieval sources
FR-17 requires every query response be traceable to the graph node(s) it was grounded in, but doesn't state whether the audit entry records *which* of HNSW, BM25/Tags, or Neo4j GraphRAG surfaced each cited node. Given the fusion cluster above (dedup, tie-breaking, degraded paths), losing per-source attribution weakens the ability to debug or audit which retrieval mechanism actually justified a given citation. *Recommendation:* include per-source contribution (which list(s) surfaced each cited node, pre- and post-fusion rank) in the audit record.

---

## Summary

| Severity | Count |
|---|---|
| Critical | 2 |
| High | 7 |
| Medium | 4 |
| Low | 2 |
| **Total** | **15** |
