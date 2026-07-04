# Addendum — Nuron Product Brief

Companion to `brief.md`. Holds rejected alternatives, in-depth rationale, options-considered matrices, parked-roadmap context, and technical constraints that don't belong in the brief itself. Audit and override information stays in `.decision-log.md` and never moves here.

---

## A. Rejected Alternatives

### A.1 · Generic vector-only RAG
- **Why considered:** lowest engineering cost; works against raw documents without a curated layer.
- **Why rejected:** does not deliver the continuity claim. Vector RAG over raw text is sensitive to phrasing, doesn't surface decision lineage, and degrades quickly as the corpus grows. The LLM-Wiki compiler exists precisely to avoid this failure mode.
- **Residual use:** vector retrieval is still a *component* of the hybrid retrieval path inside the property graph — just not the whole system.

### A.2 · Real-time stream ingestion only (no landing zone)
- **Why considered:** simpler data flow, no two-stage pipeline.
- **Why rejected:** couples the messy extraction to the runtime response. A single bad source event would degrade live query quality. The two-stage pipeline (raw landing zone → async compiler → graph) decouples the two concerns and is the architectural foundation of "the brain only ever reads pristine data."

### A.3 · Freeform agent authoring in v1
- **Why considered:** matches the trend of "agent builder" SaaS; lets customers adapt Nuron to their exact process.
- **Why rejected:** scope risk. Building a safe, observable, auditable agent authoring surface is its own product. v1 commits to one Nuron-maintained default agent plus Nuron-supplied templates. v2 may revisit.
- **Note:** this is also why templates are a first-class concept in v1, not an afterthought — the template library is the *product surface* where agent customisation lives.

### A.4 · Self-hosted *with* a control plane
- **Why considered:** even for self-hosted single-tenant v1, a thin control plane for updates / observability could be useful.
- **Why rejected:** every piece of the control plane becomes a privacy and ops surface area. v1 ships self-contained Docker / compose; the customer updates on their schedule. Multi-tenant managed service in v2 is the place where a control plane enters the picture.

### A.5 · Source-connector-first v1
- **Why considered:** proves the value claim against real source systems instead of seed Markdown.
- **Why rejected:** connectors are the longest-pole work and the highest integration risk. Shipping the pipeline first gives us a testable, demoable system; connectors in v1.1 ride on top of a validated spine. Sequence matters: prove the brain works against curated input, then expand what the brain listens to.

---

## B. The Merkle-Style Subtree Curation Hypothesis

This was raised during brief creation and is parked here for `[CA]` to pressure-test.

### B.1 · The problem
Snapshot + diff curation (strategy D-006 in the decision log) re-curates the whole subgraph on each pass. As the graph grows — which it must, or the brain has no value — the cost grows linearly. At 100k nodes, a full re-curation pass per day is impractical; at 1M it is impossible.

### B.2 · The hypothesis
Maintain a hash tree over the curator's input boundaries. Each internal node's hash is the function of its children's hashes plus its own content. When a new evidence node arrives, propagate the hash change up the tree. A curator pass only re-visits subtrees whose root hash has changed since the last successful pass. Subtrees that have not been touched cost nothing to "re-curate" because their hash hasn't changed and their prior output is still valid by definition.

### B.3 · Why this might work
- Hash propagation is O(log n) per change.
- Curator pass cost is proportional to *changed* subtrees, not total graph size.
- Determinism: if the input subtree hasn't changed, the curator output for it hasn't either.

### B.4 · Why this might not work
- Curator outputs may not be deterministic — small upstream changes can produce semantically meaningful but hash-distinct curator output (LLM non-determinism even at temperature 0 in some pipelines, ordering effects, etc.). Hash-trees require determinism. **Mitigation:** pin curator LLM temperature and order inputs; treat non-determinism as a bug to fix, not a property to design around.
- The hash tree is over input boundaries, but the curator may need *context* from outside its subtree to produce correct output (e.g., a decision that depends on a prior decision that lives in a sibling subtree). **Mitigation:** explicitly define the curator's context window per subtree; if a subtree needs sibling context, its hash must include the *hashes* of the sibling subtrees it depends on, not their content. This is the standard Merkle-DAG trick.
- Write amplification: every evidence insert touches O(log n) hash nodes. Cheap individually, but the brain is meant to be high-throughput.

### B.5 · Status
Hypothesis. Validate or invalidate during `[CA]`. If invalidated, fall back to: (a) full re-curation on a longer cadence (e.g., nightly), (b) heuristic "recently touched" windowing without the hash-tree formal structure, (c) incremental single-node curator output with eventual consistency.

---

## C. v1.1 Source Connector Roadmap (parked)

Parked here so the brief can stay focused on v1. Order is a sequencing hypothesis, not a commitment.

1. **Confluence page export → Markdown** (read-only, batch). Highest-value, lowest-risk first connector. Many customers already export their Confluence space as a backup.
2. **Internal forum post ingestion** (read + write-back for default agent replies). Higher value because it closes the read-and-respond loop. Requires Q-C to be resolved (does the agent post back?).
3. **Jira issue ingestion** (read-only initially). Common enterprise source but messy (comment threads, transitions, custom fields). Worth doing well, not worth doing fast.

Each connector is a separate epic. Each is gated on the v1 pipeline being validated against seed Markdown.

---

## D. v2 Multi-Tenant Primitive Roadmap (parked)

v1 must include the *primitives* so v2 doesn't require a rewrite. This is the minimum list:

- **Tenant ID column** in every persistent record (graph nodes, edges, queue messages, audit log entries).
- **Per-tenant data directory** in the self-hosted filesystem layout. Even single-tenant v1 should not write to a shared root.
- **Per-tenant auth scope.** Auth tokens carry tenant_id; API requests are scoped server-side regardless of what's in the request body.
- **Per-tenant rate limits** in the API layer. Trivially implementable now; painful to retrofit.
- **Tenant-aware audit log.** Every audit entry is scoped. Even if no v1 UI consumes per-tenant audit, the data shape must be there.

What v1 does **not** include: tenant management UI, billing, metering, sign-up flows, managed control plane. These are v2 product features; the primitives above ensure v2 product features can be built without re-architecting the data layer.

---

## E. Technical Constraints Captured

Items that the Architecture phase must address but do not belong in the brief:

- **Neo4j version compatibility.** `PropertyGraphIndex` and `Cypher` syntax evolve. Pin the version in `[CA]` and document upgrade path.
- **RabbitMQ topology.** Single queue, multiple queues, routing keys per request type? v1 likely wants two queues (ingest vs reply) with a single default-agent consumer; user-configured agents subscribe to a templated subset.
- **LLM choice.** Pin for v1 (one model for compilation, possibly a different model for query). Cost / latency trade-off to be resolved in Architecture. **Open:** whether the same model is used for both paths or specialised models per path.
- **Embedder choice.** Vector store cost vs retrieval quality. Architecture may recommend two embedders (small for triage, large for retrieval) — but this is a hypothesis, not a decision.
- **LLM-Wiki schema finalisation.** The seed document in the user's brief sketch is a starting point. The exact field set, required vs optional, and validation rules belong in Architecture.
- **Audit log retention.** Per-tenant policy. See Open Question Q-D.

---

## F. Personas (in-depth, parked from brief)

The brief names three primary users. In-depth personas are parked here for the PRD to consume if needed.

### F.1 · The Functional Specialist (the feeder)
- 5–25 years tenure in role.
- Owns significant undocumented institutional knowledge.
- Produces content incidentally, not as a primary activity (notes, emails, comments, ticket updates).
- Is not the primary consumer of the brain — but their departure is the primary *trigger* for the brain's value becoming visible.
- Success signal: their successor can answer questions the specialist would have answered, with evidence the specialist would have cited.

### F.2 · The Successor / New Hire (the asker)
- Joined within the last 12 months.
- Asks the same questions the specialist would have answered.
- Cannot easily route their questions to the right person (especially after the specialist has left).
- Success signal: time-to-answer on institutional questions drops from "ask a colleague, wait days, get a partial answer" to "ask Nuron, get a grounded answer with citations."

### F.3 · The Admin / Platform Owner (the operator)
- Ops engineer or platform owner inside the customer company.
- Deploys Nuron, manages users, configures sources, enables/disables agents from the template library, watches the graph.
- Is the only person with admin privileges.
- Success signal: day-2 operations (source onboarding, agent enablement, user provisioning) require no engineering work from us.

### F.4 · The Internal Automation Agent (the programmatic consumer)
- Another agent in the customer's environment that consumes the Nuron API to retrieve grounded context.
- Has no UI; interacts only via the API.
- Success signal: stable, documented, versionable API contract with no breaking changes during v1.

---

## G. Glossary (parked)

Terms used in the brief with definitions that downstream docs should keep stable.

- **Brain.** The full Nuron system — ingest pipeline, compiler, graph, runtime agents, audit log, API surface.
- **LLM-Wiki.** The standardised Markdown document format that the compiler emits. Has at minimum: Executive Summary, Core Entities & Relationships, Known Issues & Verified Solutions, Cross-References.
- **Compiled document.** A document that has been through the compiler and is now in LLM-Wiki form. The brain only ever reads compiled documents.
- **Raw document.** A document in the landing zone, untouched. The brain never reads raw documents.
- **Decision.** A human commitment captured in a compiled document, with explicit author, timestamp, evidence, and supersession chain.
- **Lineage.** The chain of supersession from one decision to its successors. A → B → C means A was superseded by B, which was superseded by C.
- **Property graph.** A graph database where nodes and edges both carry typed properties. Used here to store decisions, entities, relationships, and lineage edges with full provenance.
- **Default agent.** The Nuron-maintained agent that ships with the product. Handles ingest and reply actions.
- **User-configured agent.** An agent created from a Nuron-supplied template. Scoped to a subset of the graph or a specific query pattern.
- **Curator.** A background agent that rewrites the curated subgraph from the latest compiled evidence. Runs on a schedule, scoped to touched subtrees.
- **Touched subtree.** A subtree of the graph whose root hash has changed since the last successful curator pass. See Section B.
- **Tenant.** A single customer deployment. v1 is single-tenant per deployment. v2 may be multi-tenant per managed deployment.
