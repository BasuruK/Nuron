---
title: "Nuron PRD — Adversarial Review"
status: review
reviewed: 2026-07-13
reviewer: Technical Adversary
---

# Adversarial Review: Nuron PRD (post 2026-07-13 updates)

## Executive Summary: The Retrieval Story Got Sharper; The Service Boundary Got Blurrier The three-source hybrid retrieval clarification (HNSW + BM25/Tags + Neo4j GraphRAG → RRF → LLMRerank) is a genuine improvement — FR-8, the Glossary, UJ-2, §5.3, and SM-C2 all now say the same thing in the same words, and no stale "vector + structural" two-source language survives anywhere in the document. That part of the 2026-07-13 reconciliation was executed cleanly.

The `nuron-ai` / `nuron-api` service split is a different story. Splitting AI processing out from the API surface is architecturally sound, but the PRD introduces a hard architectural claim — `nuron-api` is reachable "only on the internal Docker network" (§5.4) — that flatly contradicts three other passages (§2.1's JTBD, UJ-2's entry state, FR-16's rate-limit language) which all assume external callers hit `nuron-api` directly. Layered on top of that, the document now has two candidate "answer machines" — a "synchronous query agent" (§4.4) and the "Default Agent" handling "reply" over RabbitMQ (§4.5/FR-10) — and never states whether they are the same thing. Both sections claim to realize the same user journey. This is exactly the kind of gap the earlier LangGraph/LlamaIndex episode should have taught the team to catch immediately and track as an Open Question; instead, the 2026-07-13 changes added zero new Open Questions despite touching more architectural surface than the entire 07-08 reviewer gate did.

None of the ten-plus findings below are cosmetic. Several block Architecture from producing a coherent container/network topology (Open Question #6/Q-J) without first resolving which of two contradictory access models is correct.

---

## Tier-1 Findings (Blocking)

### F-1: `nuron-api`'s "Internal Docker Network Only" Claim Contradicts Direct External API Access Promised Elsewhere

**Finding:** §5.4 states: *"`nuron-api` and `nuron-ai` are reachable only on the internal Docker network. `nuron-web` is the only container exposed to the customer's reverse proxy/ingress and does not call `nuron-ai` directly."* This says `nuron-api` has no path from outside the Docker network at all. Yet:
- §2.1 JTBD: *"Other internal agents (secondary): 'When my automation needs grounded organisational context, I want to query Nuron's API directly...'"*
- UJ-2 Entry state: *"Authenticated on `nuron-web` (or calling the API directly from his own tooling)."*
- FR-16: *"A caller (including an internal automation agent) exceeding the configured per-tenant limit receives a rate-limit error response..."* — rate limiting a service that no external caller can reach is pointless.
- §5.5: Apache APISIX is floated as a *future edge gateway* for "untrusted/high-volume traffic" — implying `nuron-api` is expected to face traffic that needs gatekeeping, which is incompatible with it being Docker-internal-only today.

**Why this matters:** This is not a wording nit — it's the difference between two entirely different network topologies. If `nuron-api` really is Docker-internal-only, three other sections (a JTBD, a user journey, and an FR) describe a capability that literally cannot be exercised. If `nuron-api` is in fact reachable by external callers (which the preponderance of the text suggests), §5.4's NFR is wrong and will mislead Architecture into over-isolating the one component the product contract depends on.

**Recommendation:** Pick one topology and make every section agree with it. Most likely fix: `nuron-api` is exposed to the customer's ingress (directly or via `nuron-web`'s reverse proxy) for both `nuron-web` and direct API/automation callers; only `nuron-ai` is Docker-internal-only. Update §5.4 accordingly.

---

### F-2: FR-9 (Synchronous SSE Streaming) Contradicts FR-10 (Async-Only RabbitMQ Dispatch) for What May Be the Same Action

**Finding:** §4.4's description says the query agent's *"selected graph-grounded context is streamed through `nuron-api` over SSE"* as it's generated — a synchronous, live, in-request flow. FR-10, describing the Default Agent's "reply" action, requires: *"Ingest and reply requests are dispatched and consumed as RabbitMQ pub/sub events (per FR-4), **not handled synchronously in-process**."* If "reply" (FR-10) and "answering a query" (FR-8/FR-9) are the same action — which the shared vocabulary ("reply," "answer," "query") strongly implies — the PRD requires that action to be both live-streamed to a waiting HTTP client and dispatched asynchronously through a message broker with no in-process path, with no bridge between the two ever described.

**Why this matters:** These aren't independently minor constraints — an SSE stream needs a live process holding the connection open and pushing tokens as they're produced; a pure pub/sub dispatch model has no built-in mechanism for that without a defined callback/streaming-relay layer, which no FR, NFR, or Open Question currently owns.

**Recommendation:** Either (a) confirm "reply" and "query/answer" are genuinely two different actions and rename one of them to stop the vocabulary collision, or (b) if they're the same action, add an explicit requirement (or Open Question) for how synchronous SSE streaming is reconciled with async RabbitMQ dispatch — e.g., a request/reply pattern over RabbitMQ with a correlation ID and a streaming relay in `nuron-api`.

---

### F-3: Two Sections Both Claim to "Realize UJ-2" Without Reconciling Who Actually Answers the Question

**Finding:** §4.4 (Query & Retrieval) ends its description with *"Realizes UJ-2"* and attributes the answer to *"a synchronous query agent."* §4.5 (Agents) also ends its description with *"Realizes UJ-2"* and attributes it to *"the Nuron-maintained Default Agent... handles ingest and reply actions."* §5.3 lists **both** "Default Agent" and "runtime query agent" as distinct items `nuron-ai` hosts. The PRD never states whether these are the same agent wearing two names, two cooperating agents, or two competing/redundant implementations of the same capability.

**Why this matters:** UJ-2 is one of only two v1 user journeys and the PRD's primary vehicle for the decision-lineage differentiator. A reader (or an Architecture-phase engineer) cannot tell from this document how many agents actually answer a query, which is a foundational fact for sizing the pipeline, the RabbitMQ topology, and the SSE relay.

**Recommendation:** Name the query-answering path exactly once, in exactly one section, and have the other section reference it rather than re-claim ownership of the same journey.

---

## Tier-2 Findings (High Risk, Architecture-Blocking)

### F-4: "Neo4j GraphRAG" Naming Is Ambiguous — Literal Product/Library or Descriptive Term?

**Finding:** Neo4j ships an actual, separately-branded "Neo4j GraphRAG" package (`neo4j-graphrag-python`), distinct from LlamaIndex's own `PropertyGraphIndex` retrievers. The PRD's Glossary defines "Neo4j GraphRAG" generically as *"first-class retrieval against the Neo4j property graph that returns ranked candidates using graph structure"* — without saying whether this literally means integrating Neo4j's own GraphRAG library alongside LlamaIndex, or is just descriptive language for graph retrieval implemented entirely through LlamaIndex's `PropertyGraphIndex` (which §5.3 and the Vision both frame as the sole graph-access layer). Open Question #17 lists everything Architecture must pin for retrieval (index params, tokenizer, ranking, RRF constant, reranker) but never asks Architecture to resolve *which library or product implements the GraphRAG source*.

**Why this matters:** This is structurally the same category of risk as the earlier LangGraph-vs-LlamaIndex ambiguity that got its own dedicated Open Question (#1) — two frameworks with overlapping capability and no reconciliation — except this time it wasn't caught and isn't tracked anywhere.

**Recommendation:** Add an explicit line to Open Question #17 (or a new Open Question) asking Architecture to confirm whether "Neo4j GraphRAG" retrieval is implemented via LlamaIndex's own graph retrievers or Neo4j's dedicated GraphRAG package, and update the Glossary entry once resolved.

---

### F-5: Admin/System Configuration Crossing the New `nuron-ai` / `nuron-api` Boundary Has No Defined Handoff

**Finding:** §5.4 is explicit that *query* delegation between `nuron-api` and `nuron-ai` exists (transport deferred to Architecture). But FR-1 (directory-based Markdown ingestion) and FR-13 (admin can configure ingestion sources / MCP-style connection settings "through `nuron-web` or directly via REST, with no code changes required") both describe configuration that must ultimately reach `nuron-ai`'s structuring/Compiler pipeline — and `nuron-ai` has no REST surface an admin (or `nuron-api`) can call, since it's internal-network-only and its API contract is undefined. There is no statement that `nuron-api` forwards configuration changes to `nuron-ai`, only that it forwards queries.

**Why this matters:** Before the `nuron-ai`/`nuron-api` split, "the system reads config, no code changes required" was a single-service promise. Post-split, FR-13's testable consequence ("a configuration change... takes effect without a service restart") now spans two services and an unspecified inter-service contract that isn't mentioned anywhere the way query delegation is.

**Recommendation:** Extend §5.4 (or Open Question Q-J) to explicitly cover the config-propagation path from `nuron-api` to `nuron-ai`, not just the query path.

---

### F-6: "Runtime Query Agent" Is Load-Bearing Throughout the PRD but Has No Glossary Entry

**Finding:** The phrase appears in the Vision (§1: *"...and runtime query agent as a LlamaIndex-based agentic pipeline..."*), §4.4's description ("a synchronous query agent"), §5.3 (listed as a distinct dependency alongside the Default Agent), and is the implicit actor behind FR-8. Every other named agent — Compiler, Curator, Default Agent — has a formal §3 Glossary definition. The query agent does not.

**Why this matters:** Given F-3 above (ambiguous relationship to the Default Agent), the absence of a Glossary entry is not a stylistic gap — it's the missing definition that would have forced the PRD to state clearly whether this is a fourth distinct agent or another name for the Default Agent.

**Recommendation:** Add a Glossary entry for the query agent (name it consistently — "runtime query agent" vs. "query agent" vs. "synchronous query agent" are currently used interchangeably) and use it to resolve F-3.

---

## Tier-3 Findings (Medium Risk, Clarifying)

### F-7: SM-C2 vs. FR-8's Degraded-Path Clause Draw a Fuzzy Line Between "Forbidden" and "Required" Source-Dropping

**Finding:** SM-C2 (counter-metric) states retrieval latency *"should never be optimized by dropping any of the HNSW, BM25, or Neo4j GraphRAG candidate sources."* FR-8 itself says: *"If HNSW, BM25, Neo4j GraphRAG, or LLMRerank times out or errors, the response records the degraded path and the remaining retrieval sources may continue rather than blocking the query."* These aren't technically contradictory (one bans deliberate optimization; the other permits fault-tolerant degradation) but the line between "a source that's slow enough to look like it's being deliberately dropped for latency" and "a source that legitimately timed out" is not defined anywhere, and an implementer under latency pressure has an obvious incentive to blur it.

**Why this matters:** Without a concrete timeout threshold or a distinction between "hard error" and "slow," SM-C2 is unenforceable as a counter-metric — there's no way to audit after the fact whether a missing GraphRAG contribution was a genuine failure or a latency-motivated skip.

**Recommendation:** Pin an explicit per-source timeout (even a placeholder value marked `[ASSUMPTION]`) so "timed out" is a bright line, and have SM-C2 reference it.

---

### F-8: Zero New Open Questions Were Captured From the 2026-07-13 Changes Despite Substantial New Architecture Surface

**Finding:** The 07-08 reviewer gate added ten Open Questions (#7–17 minus #1–6) for gaps it found. The two 2026-07-13 changes — splitting out `nuron-ai` as a service and re-scoping FR-8 to a three-source hybrid — touch at least as much architecture (service boundary, network topology, a new named agent, a new candidate-source library choice) yet added **no** new Open Questions. F-1, F-2, F-3, F-4, and F-5 above are all gaps that a disciplined reviewer pass should have caught and logged at the time.

**Why this matters:** The decision log shows the 07-08 review process working as intended (catch gaps, log them explicitly). The 07-13 changes bypassed that same rigor — they were reconciled against Notion comments and a retrieval clarification, but not re-run through an adversarial or gap-finding pass before being marked final. This finding is itself evidence of that gap.

**Recommendation:** Route future PRD changes of this scope (new service, new architectural component) through the same reviewer-gate discipline as the initial draft, even when the change originates from a "simple" reconciliation task.

---

### F-9: FR-17 Audit Log Captures Cited Nodes, Not Which of the Three Retrieval Sources Produced Them

**Finding:** FR-17 requires: *"Given any query response, an admin can retrieve the specific Decision/Entity node(s) it cited."* Now that retrieval is a three-source hybrid (HNSW/BM25/GraphRAG) fused via RRF and reordered by LLMRerank, the audit log as specified captures only the final cited nodes — not which source(s) surfaced each one, nor the fusion/rerank path that selected it over other candidates.

**Why this matters:** Part of the value case for a three-source hybrid (as opposed to a single retriever) is that GraphRAG's structural/lineage retrieval is supposed to find things HNSW/BM25 alone would miss. Without per-source provenance in the audit trail, there's no way to demonstrate — or debug — that GraphRAG is pulling its weight versus HNSW/BM25 doing all the real work.

**Recommendation:** Extend FR-17's testable consequences to require recording which retrieval source(s) contributed each cited node, or explicitly scope this out as a v1.1 observability improvement (tying into Open Question #11).

---

### F-10: §4.3's Description Conflates Initial Graph Persistence (FR-5) With Scheduled Re-Curation (FR-7) Under a Single "Curator" Actor

**Finding:** §4.3's Description reads: *"The Curator in `nuron-ai` persists compiled LLM-Wiki documents into a LlamaIndex `PropertyGraphIndex` over Neo4j... It re-curates the graph on a schedule, touching only subtrees changed since the last pass."* But FR-5's actor is `nuron-ai` generally (*"`nuron-ai` can persist an LLM-Wiki document's entities..."*), while the Glossary defines Curator narrowly as the agent that *"re-curates the property graph **on a schedule**"* — i.e., a periodic maintenance pass, not the initial write path.

**Why this matters:** If the Curator is both the first-write path (FR-5) and the scheduled re-curation pass (FR-7), that's a meaningful design fact (one agent, two triggers) that should be stated as such. If they're different actors (e.g., a graph-writer step distinct from the Curator), FR-5's attribution to generic "`nuron-ai`" needs to name the actual actor, consistent with how every other FR names its owning agent explicitly.

**Recommendation:** State explicitly in §4.3 and FR-5 whether the Curator performs both initial persistence and scheduled re-curation, or split the responsibility and name each actor.

---

## Tier-4 Findings (Lower Risk, Philosophical)

### F-11: Vision's "Commodity Graph Technique" Framing Sits Awkwardly Against the Bespoke Three-Source Retrieval Investment

**Finding:** §1 Vision argues the moat is "not the embeddings, the LLM choice, or the graph technique, all of which are commodity by the time v1 ships." Yet the PRD now specifies, in real detail, a custom three-source hybrid (HNSW + BM25/Tags + Neo4j GraphRAG) fused via RRF and reordered by LLMRerank — a materially more sophisticated and harder-to-replicate retrieval design than "commodity" implies.

**Why this matters:** This doesn't block Architecture, but it's worth naming: if the retrieval design really is commodity-equivalent, the level of specification investment in FR-8 is disproportionate; if it isn't commodity, the Vision's moat argument should probably credit retrieval quality alongside write-time supersession and restart-safety, rather than explicitly disclaiming it.

**Recommendation:** No action required for v1; consider revisiting this framing if retrieval quality turns out to be a genuine differentiator in practice.

---

## Summary Table: Finding Severity & Action

| ID | Title | Tier | Action |
|---|---|---|---|
| F-1 | `nuron-api` network-exposure contradiction | T1 | Pick one topology; fix §5.4 or the three conflicting passages |
| F-2 | FR-9 sync SSE vs. FR-10 async-only dispatch | T1 | Clarify if "reply" = "query"; define the streaming/dispatch bridge |
| F-3 | Dual "Realizes UJ-2" ownership | T1 | Name the query-answering path once; stop the double claim |
| F-4 | "Neo4j GraphRAG" naming ambiguity | T2 | Add to Open Question #17: which library implements GraphRAG retrieval |
| F-5 | Config handoff across `nuron-ai`/`nuron-api` undefined | T2 | Extend §5.4/Q-J to cover config propagation, not just query delegation |
| F-6 | "Runtime query agent" missing from Glossary | T2 | Add Glossary entry; standardize the name |
| F-7 | SM-C2 vs. FR-8 degraded-path fuzzy boundary | T3 | Pin an explicit per-source timeout threshold |
| F-8 | No new Open Questions from 07-13 changes | T3 | Re-run reviewer-gate discipline on future architecture-touching changes |
| F-9 | Audit log lacks per-source retrieval provenance | T3 | Extend FR-17 to record contributing source(s) per citation |
| F-10 | Curator role conflates persist vs. re-curate actor | T3 | State explicitly whether Curator owns both, or split and rename |
| F-11 | "Commodity graph technique" vs. bespoke retrieval investment | T4 | No v1 action; revisit framing later |

---

## Verdict

Three Tier-1 findings are genuine, citable contradictions in the current document — not stylistic quibbles — and each would send an Architecture-phase engineer down the wrong path if worked from literally. All three trace back to the same root cause: the `nuron-ai`/`nuron-api` split and the elevation of a "synchronous query agent" were reconciled against specific Notion comments and a retrieval clarification, but the resulting text was never checked against the rest of the document (JTBDs, other feature sections, NFRs) the way the 07-08 reviewer gate checked the original draft. The three-source retrieval story itself is internally consistent and cleanly propagated — no stale two-source language survives — which shows the reconciliation process *can* work; it just didn't get applied here with the same rigor.

*Review completed 2026-07-13.*
