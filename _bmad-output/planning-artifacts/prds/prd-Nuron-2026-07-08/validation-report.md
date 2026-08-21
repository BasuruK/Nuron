# Validation Report — Nuron PRD

- **PRD:** `_bmad-output/planning-artifacts/prds/prd-Nuron-2026-07-08/prd.md`
- **Rubric:** `.agents/skills/bmad-prd/assets/prd-validation-checklist.md`
- **Run at:** 2026-07-13T00:00:00Z
- **Grade:** Poor

## Overall verdict

On the quality rubric alone, this PRD holds up well: it has a genuine thesis (write-time Decision Supersession lineage as the differentiator against Mem0/Cognee), FRs carry testable consequences almost without exception, and the 2026-07-13 Notion-comment reconciliation (`nuron-ai` split, three-source hybrid retrieval, FR-1/FR-6 confirmations, FR-11 v2 deferral) threads cleanly through every section the rubric dimensions check, leaving only two low-severity mechanical gaps (an undefined "Core Entity" glossary term, and an unlisted audit-retrieval endpoint in §5.4).

The adversarial and edge-case-hunter passes, which stress-test cross-section consistency and boundary conditions rather than dimension maturity, tell a different story about the same 2026-07-13 changes: the `nuron-ai`/`nuron-api` service split and the FR-8 retrieval re-scope were reconciled against specific Notion comments and a clarification request, but never checked against the rest of the document the way the original 2026-07-08 draft was. That produced five genuine Critical findings — three blocking contradictions (an "internal Docker network only" claim for `nuron-api` that conflicts with external API access promised in three other passages; a synchronous-SSE-vs-async-only-RabbitMQ-dispatch conflict between what may be the same "reply"/"query" action; two sections both claiming to "Realize UJ-2" without reconciling whether the query agent and Default Agent are the same actor) plus two independently-found structural gaps (Neo4j GraphRAG's candidate list likely depends on seed entry points from HNSW/BM25, undermining the "three independent sources" framing; the tenant-ID trust boundary is defined only for the public API, not the internal `nuron-api`→`nuron-ai` call where the actual tenant-scoped query executes). None of this is cosmetic, and none of it should surprise Architecture if resolved now — but it is exactly the class of gap a reviewer-gate pass is supposed to catch before a PRD is marked final.

## Dimension verdicts

- Decision-readiness — strong
- Substance over theater — strong
- Strategic coherence — strong
- Done-ness clarity — adequate
- Scope honesty — strong
- Downstream usability — adequate
- Shape fit — strong

## Findings by severity

### Critical (5)

**[Adversarial]** `nuron-api`'s "internal Docker network only" claim contradicts direct external API access promised elsewhere (§5.4 vs §2.1, UJ-2, FR-16)
§5.4 states `nuron-api` and `nuron-ai` are reachable "only on the internal Docker network," yet the JTBD (§2.1), UJ-2's entry state, and FR-16's rate-limiting language all assume external/automation callers hit `nuron-api` directly.
Fix: pick one topology — most likely `nuron-api` is exposed to the customer's ingress (directly or via `nuron-web`) while only `nuron-ai` stays Docker-internal — and make every section agree.

**[Adversarial]** FR-9's synchronous SSE streaming contradicts FR-10's async-only RabbitMQ dispatch for what may be the same action
§4.4 describes graph-grounded context streamed live over SSE; FR-10 requires "reply" actions be dispatched via RabbitMQ pub/sub, "not handled synchronously in-process." If "reply" and "query/answer" are the same action, no bridge between the two models is described anywhere.
Fix: confirm whether "reply" and "query" are the same action; if so, define the streaming/dispatch bridge (e.g., correlation-ID request/reply pattern with a streaming relay).

**[Adversarial]** Two sections both claim to "Realize UJ-2" without reconciling agent identity (§4.4 vs §4.5)
§4.4 attributes UJ-2 to "a synchronous query agent"; §4.5 attributes the same journey to the "Default Agent." §5.3 lists both as distinct `nuron-ai` components with no stated relationship.
Fix: name the query-answering path exactly once, in exactly one section; have the other section reference it rather than re-claim ownership.

**[Edge Case Hunter]** EC-3.1 — Neo4j GraphRAG's candidate independence assumes seed entry points it may not have
FR-8 frames HNSW, BM25/Tags, and Neo4j GraphRAG as three independently-failing sources, but graph traversal typically needs seed/entry nodes. If GraphRAG's seeds derive from HNSW/BM25 output, "GraphRAG survives while HNSW+BM25 are down" isn't achievable as written.
Fix: Architecture must specify GraphRAG's seed-selection mechanism and state whether it can run with zero vector/lexical input.

**[Edge Case Hunter]** EC-5.1 — Tenant-ID trust boundary is defined only for the public API, not the internal service call
FR-14 requires the tenant ID come only from the caller's verified token for requests to `nuron-api`, but the tenant-scoped graph query actually executes inside `nuron-ai`, reached via an internal call whose transport (and trust model) is undefined.
Fix: extend FR-14's verified-token-only rule to the `nuron-api`→`nuron-ai` boundary, or require `nuron-ai` to independently re-validate the tenant claim.

### High (10)

**[Edge Case Hunter]** EC-2.1 — Supersession fan-out has no reconciliation rule (one predecessor, two+ unreconciled successors; "the present" lineage head undefined).
Fix: extend FR-6's contradiction logic to trigger on unreconciled fan-out; define traversal behavior at an unresolved fork.

**[Edge Case Hunter]** EC-2.2 — RTBF deletion of a mid-chain Decision doesn't specify Supersession-edge cleanup, risking dangling edges in lineage traversal.
Fix: define whether deletion bridges the chain around the gap or truncates traversal with an explicit "lineage incomplete" marker.

**[Edge Case Hunter]** EC-3.2 — Vector/lexical-only degradation (GraphRAG down) can silently drop Supersession-lineage context with only a generic degraded-path marker.
Fix: require the degraded-path record to explicitly flag when lineage retrieval specifically was skipped.

**[Edge Case Hunter]** EC-3.3 — No cross-source deduplication rule before RRF fusion when HNSW and GraphRAG surface the same node independently.
Fix: define dedup-by-node-ID as a pre-fusion step with a stated rank-precedence rule.

**[Edge Case Hunter]** EC-3.4 — No defined behavior for two-of-three or all-three retrieval sources failing simultaneously.
Fix: define a minimum-surviving-sources threshold below which the query fails explicitly.

**[Edge Case Hunter]** EC-5.2 — `nuron-ai` crash/restart mid-query isn't covered by the restart-safety NFR, which is scoped only to the async pipeline (FR-4).
Fix: extend restart-safety guarantees (or define a distinct contract) to the synchronous query/SSE path.

**[Edge Case Hunter]** EC-5.5 — RTBF cascade has no defined behavior if `nuron-ai` is unreachable during deletion, risking partial deletion across the service split.
Fix: require the deletion operation to durably queue the `nuron-ai`-side cascade and confirm completion before considering deletion done.

**[Adversarial]** "Neo4j GraphRAG" naming is ambiguous — literal library/product vs. descriptive term — and unlike the earlier LangGraph/LlamaIndex ambiguity, isn't tracked as an Open Question.
Fix: add an explicit line to Open Question #17 (or a new one) asking Architecture to confirm which library implements GraphRAG retrieval.

**[Adversarial]** Admin/config handoff across the new `nuron-ai`/`nuron-api` boundary is undefined — only query delegation is specified, not configuration propagation (FR-1, FR-13).
Fix: extend §5.4 (or Open Question Q-J) to explicitly cover the config-propagation path.

**[Adversarial]** "Runtime query agent" is load-bearing throughout the PRD (Vision, §4.4, §5.3) but has no Glossary entry, unlike every other named agent.
Fix: add a Glossary entry and standardize the name ("query agent" / "synchronous query agent" / "runtime query agent" currently used interchangeably).

### Medium (8)

**[Edge Case Hunter]** EC-3.5 — No RRF tie-breaking rule for identical fused rank scores. Fix: pin a deterministic tie-break alongside the RRF constant already deferred to Open Question #17.

**[Edge Case Hunter]** EC-4.1 — "Reply" (FR-10, async) vs. "query" (FR-8, synchronous) path ambiguity could let the two mechanisms diverge unnoticed. Fix: state explicitly whether they're the same logical operation via different transports.

**[Edge Case Hunter]** EC-5.3 — Cross-service config propagation for `nuron-ai`-side settings (retrieval params, LLM/embedding config) is unspecified. Fix: clarify in Architecture which service owns which config surface.

**[Edge Case Hunter]** EC-5.4 — Per-tenant rate limiting in `nuron-api` doesn't protect `nuron-ai`'s shared compute capacity from aggregate saturation. Fix: consider a concurrency/queue-depth cap independent of the per-tenant limiter.

**[Adversarial]** SM-C2 (never drop a retrieval source) vs. FR-8's degraded-path clause (sources "may continue" on timeout/error) draws a fuzzy, unenforceable line between "forbidden" and "required" source-dropping. Fix: pin an explicit per-source timeout threshold so "timed out" is a bright line.

**[Adversarial]** Zero new Open Questions were captured from the 2026-07-13 changes despite substantial new architecture surface (service split, new agent, new library choice). Fix: route future architecture-touching changes through the same reviewer-gate discipline as the initial draft.

**[Adversarial]** FR-17's audit log captures cited nodes but not which of the three retrieval sources produced them, weakening the ability to demonstrate GraphRAG is pulling its weight. Fix: extend FR-17 to record contributing source(s) per citation, or explicitly scope out as v1.1.

**[Adversarial]** §4.3 conflates initial graph persistence (FR-5) with scheduled re-curation (FR-7) under a single "Curator" actor without stating whether it's one agent with two triggers or two actors. Fix: state explicitly in §4.3/FR-5 which is the case.

### Low (5)

**[Rubric]** API contract in §5.4 omits an audit-retrieval endpoint despite FR-17 requiring admin retrieval of cited nodes and source evidence. Fix: add a one-line audit/traceability endpoint bullet to §5.4, or note the omission is deliberate (UI-only in v1).

**[Rubric]** "Core Entity" (used in FR-3, FR-5, §10) is never defined in the Glossary, which only defines "Entity / Knowledge Node." Fix: add a "Core Entity" Glossary entry distinguishing it from "Entity / Knowledge Node," or normalize FR-3/FR-5 to the Glossary term.

**[Edge Case Hunter]** EC-1.1 — No canonical tag vocabulary for the Tags block; tag drift silently degrades the BM25/Tags fusion source over time. Fix: define a canonical tag taxonomy or synonym-folding step at structuring time.

**[Edge Case Hunter]** EC-5.6 — Audit trail doesn't specify per-source provenance across the three retrieval sources (duplicate of the adversarial FR-17 finding above, found independently). Fix: include per-source contribution in the audit record.

**[Adversarial]** Vision's "commodity graph technique" framing sits awkwardly against the bespoke three-source hybrid retrieval investment now specified in detail. Fix: no v1 action required; revisit the framing if retrieval quality proves a genuine differentiator.

## Mechanical notes

- 2026-07-13 reconciliation integrity (rubric): verified clean — no stale two-source retrieval language, no lingering "LangGraph" outside the tracked Open Question #1, no orphaned Agent-Template v1 references.
- Assumptions Index roundtrip: clean — exactly 3 inline `[ASSUMPTION]` tags (FR-2, FR-3, FR-7/SM-5), all indexed in §10, no extras either direction.
- ID continuity: FR-1…FR-17 contiguous (FR-11 intentionally retained-but-deferred, documented). UJ-1…UJ-3 and SM-1…SM-5 (+ SM-C1/C2) contiguous with `[v2]` deferrals clearly labeled.
- Both the FR-17 per-source-provenance gap (adversarial F-9) and the audit per-source gap (edge case EC-5.6) were found independently by two reviewers — a convergent signal worth treating as one fix.

## Reviewer files

- `review-rubric.md`
- `review-adversarial-general.md`
- `review-edge-case-hunter.md`
