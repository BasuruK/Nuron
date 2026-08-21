---
title: "Nuron — PRD"
status: final
created: 2026-07-08
updated: 2026-07-13
project: Nuron
author: Basuruk
facilitator: bmad-prd
---

# PRD: Nuron

## 0. Document Purpose

This PRD converts the Nuron product brief (`brief-Nuron-2026-06-19/brief.md` + `addendum.md`) into acceptance-criteria-grade requirements for the Architecture, UX, and Epics/Stories phases that follow it. Features are grouped with functional requirements (FRs) nested underneath and numbered globally (FR-1…FR-N) for stable downstream references. Domain vocabulary is fixed in the Glossary (§3) and used verbatim throughout. Inline `[ASSUMPTION]` tags mark inferences not yet confirmed with the user; they are indexed in §10.

## 1. Vision

Nuron is a self-hosted "company brain": it ingests the scattered textual record of how an organisation actually works — across domains, and within each domain across flows, people, and systems — and turns it into a continuously-evolving graph of Decisions, the Entities and knowledge they touch, and the evidence lineage connecting them. That knowledge is deliberately broader than product docs: it includes code documentation, business-flow knowledge domain experts carry but rarely write down, operational knowledge (release processes, on-call rotations, vendor relationships), and people-handling knowledge (how decisions about teams, hires, and reorganisations were reached). Decisions are first-class and carry explicit supersession edges to what they replaced, modeled at write time, not inferred at query time — this is Nuron's central differentiator against generic RAG and against competitors like Mem0 (retrieval-time temporal ranking, no graph invalidation) and Cognee (no explicit decision entity). The graph is not decisions alone: the surrounding knowledge model is stored as first-class graph content in its own right, because that is what a successor actually needs to act, not just to know what was decided.

The system exists so that when a person who has carried years of undocumented context in their head leaves, changes teams, or stops paying attention, the next person can start from where they stopped instead of from zero. It gets more accurate the longer it runs: every new Decision documented adds evidence and lineage rather than displacing what came before. This compounding effect, together with a self-hosted posture that ships **no telemetry by default** and a RabbitMQ-backed pipeline that survives a process restart without losing in-flight work (unlike competitors' in-process, restart-fragile queues), is the moat: not the embeddings, the LLM choice, or the graph technique, all of which are commodity by the time v1 ships.

v1 proves the full ingest → structure → compile → graph → query pipeline against seed Markdown content in a single-tenant, self-hosted deployment (Docker/Compose). `nuron-ai` contains the AI processing logic and RAG path: it hosts the structuring agent, Compiler, Curator, Default Agent, and runtime query agent as a **LlamaIndex-based agentic pipeline** over a LlamaIndex `PropertyGraphIndex` on Neo4j. `nuron-api` (Laravel, latest version) exposes the REST + SSE API and auth surface as the actual product contract and delegates AI processing and retrieval to `nuron-ai`. `nuron-web` (SvelteKit + Bits UI) is shipped as a replaceable reference admin UI — not the product itself.

## 2. Target User

### 2.1 Jobs To Be Done

- **The functional specialist (feeder):** "When I've spent years accumulating knowledge — product know-how, code history, business-flow judgment — that only lives in my head and scattered notes, I want to drop it into a system with no extra authoring effort, so it survives me leaving."
- **The new hire / successor (asker):** "When I inherit a role or a decision area, I want to ask why something was decided and see the evidence, the related product/code/process knowledge, and the chain of what superseded it, so I don't have to track down people who've moved on or left."
- **The admin / platform owner:** "When I run Nuron for my org, I want to configure sources, users, and system settings without engineering involvement, so the system stays operable by ops, not by us."
- **Other internal agents (secondary):** "When my automation needs grounded organisational context, I want to query Nuron's API directly, so I don't re-implement retrieval myself."

### 2.2 Non-Users (v1)

- End-consumers / public-facing users.
- Multi-tenant SaaS customers of a managed Nuron (that's the v2 audience; v1 is single-tenant self-hosted).
- Anyone wanting freeform, customer-authored agent graphs (explicit v1 non-goal).

### 2.3 Key User Journeys

- **UJ-1. Devika drops twenty years of notes and moves on.**
  - **Persona + context:** Devika, a senior functional specialist retiring in a year, has decades of scattered notes, emails, and text files describing decisions, processes, and tests she's run.
  - **Entry state:** Authenticated as a standard user on `nuron-web`; has a folder of Markdown files ready.
  - **Path:** She drops the Markdown files into her configured ingest directory (or uploads via the admin UI's source setup screen). The structuring agent in `nuron-ai` normalises each file into a Raw Ingest Agreement. Its Compiler picks the document up asynchronously and emits an LLM-Wiki document; its Curator folds the new content into the graph.
  - **Climax:** She (or anyone) can query the system and get back an answer citing her notes as evidence — without her having to structure, tag, or cross-reference anything herself.
  - **Resolution:** She moves on to the next batch of notes, trusting the system surfaces them when someone needs them.
  - **Edge case:** if a dropped file conflicts with a previously ingested Decision, the Curator pass surfaces it as a reconciled supersession, not a silent overwrite.

- **UJ-2. Kian asks why the auth model handles refresh tokens this way.**
  - **Persona + context:** Kian, six months into a role his predecessor vacated, hits a design decision he doesn't understand while extending the auth model.
  - **Entry state:** Authenticated on `nuron-web` (or calling the API directly from his own tooling).
  - **Path:** He asks the query UI (or API) "why does the auth model handle refresh tokens this way?" The runtime query agent in `nuron-ai` retrieves candidates through HNSW vector search, BM25 over Tags, and Neo4j GraphRAG. Reciprocal Rank Fusion (RRF) combines all three ranked result sets, LLMRerank orders the fused candidates, and the answer is generated from the selected graph-grounded context, including structural neighborhoods and Supersession lineage.
  - **Climax:** The response names the Decision, its author, its date, the evidence cited, the related code/product documentation nodes, and the chain of any decisions that superseded it.
  - **Resolution:** Kian makes his change with full context, and the system logs his own resulting decision as new evidence rather than losing it.
  - **Edge case:** if no Decision node matches, the system says so explicitly rather than fabricating an answer, and cites the closest related Entities it does have.

### 2.4 Deferred v2 User Journey

- **UJ-3 [v2]. Priya scopes an Onboarding Q&A agent to one domain.**
  - **Persona + context:** Priya, the platform admin, wants a narrower assistant for new hires in one product domain without exposing the entire graph.
  - **Entry state:** Authenticated as admin on `nuron-web`.
  - **Path:** She opens agent setup, selects the "Onboarding Q&A" template from the Nuron-supplied library, scopes it to a subset of the graph (one domain), and enables it.
  - **Climax:** The scoped agent is live and answering, configured entirely through the admin UI/REST — no engineering involvement from Nuron.
  - **Resolution:** New hires in that domain use the scoped agent from day one.
  - **Edge case:** if the selected scope has too little compiled content to be useful, the admin UI warns her before enabling.

## 3. Glossary

- **Landing Zone** — untouched storage for raw ingested content before structuring.
- **Raw Ingest Agreement** — the fixed schema (Content Header, Content, Key Discoveries, Tags) a structuring agent normalises every raw Markdown file into before it reaches the compiler. The Tags block feeds BM25/keyword retrieval.
- **LLM-Wiki** — the dense, standardised Markdown document (Executive Summary, Core Entities & Relationships, Known Issues & Verified Solutions, Cross-References) the compiler emits. The only form the graph ever ingests.
- **Compiler (agent)** — the LlamaIndex-based agent in `nuron-ai` that turns a Raw Ingest Agreement document into an LLM-Wiki document, asynchronously, event-driven via RabbitMQ.
- **Decision** — a first-class graph node representing a choice made, with author, timestamp, and cited Evidence. Carries an explicit Supersession edge to the Decision it replaces, if any.
- **Entity / Knowledge Node** — a first-class graph node representing product documentation, code documentation, or tacit business-flow knowledge that a Decision relates to or cites. Distinct from a Decision (which encodes a choice + lineage) and from Evidence (the raw source it traces back to).
- **Supersession / Decision Lineage** — an explicit edge from a Decision to the prior Decision it replaces, modeled at write time by the Curator, not inferred at query time.
- **Evidence / Episode** — the raw source content a Decision or Entity traces back to.
- **Curator (agent)** — the LlamaIndex-based agent in `nuron-ai` that re-curates the property graph on a schedule, touching only subtrees changed since the last pass.
- **Default Agent** — the Nuron-maintained agent in `nuron-ai` that handles ingest and reply actions, one action per request, dispatched over RabbitMQ pub/sub.
- **Agent Template [v2]** — a Nuron-supplied, admin-configurable agent definition such as Onboarding Q&A or Decision Lineage Reporter. Enablement and scoping are deferred to v2.
- **HNSW** — Hierarchical Navigable Small World, the vector index/search algorithm used to retrieve graph candidates from node embeddings.
- **Neo4j GraphRAG** — first-class retrieval against the Neo4j property graph that returns ranked candidates using graph structure, including Entity/Decision neighborhoods, relationships, paths, and Supersession lineage.
- **Reciprocal Rank Fusion (RRF)** — the rank-fusion step that combines the HNSW, BM25, and Neo4j GraphRAG candidate lists before reranking.
- **LLMRerank** — the LLM-based reranking step applied after RRF to order the fused retrieval candidates.
- **Tenant / Workspace** — the v1 scoping primitive isolating data directory, auth, and audit per deployment (single-tenant in v1, foundation for v2 multi-tenant).
- **`nuron-ai`** — the internal service containing Nuron's AI processing logic and RAG path, including structuring, compilation, curation, Default Agent processing, and runtime retrieval.
- **`nuron-api`** — the Laravel (latest version) REST API + auth surface. Internal-network only; delegates AI processing and RAG to `nuron-ai`.
- **`nuron-web`** — the SvelteKit (Bits UI) reference admin UI. Thin client over `nuron-api`.

## 4. Features

### 4.1 Ingestion & Structuring

**Description:** Raw Markdown content enters Nuron through a configured, recursively-scanned directory and lands untouched in the Landing Zone. The structuring agent in `nuron-ai` normalises each file into the fixed Raw Ingest Agreement schema before anything reaches the Compiler. Realizes UJ-1.

#### FR-1: Markdown directory ingestion

The system can ingest Markdown files from an admin-configured directory on a configurable schedule.

**Consequences (testable):**
- Files added to the configured directory (including nested subdirectories) are picked up within one scheduled scan interval without manual triggering.
- Raw file content is written to the Landing Zone unmodified before any structuring or compilation occurs.
- A file that fails to ingest (unreadable, non-UTF-8, empty) is logged and does not block ingestion of other files in the same scan.
- Files must be UTF-8 plain Markdown with optional YAML frontmatter; there is no required frontmatter schema in v1 — the structuring agent (FR-2) extracts or infers the Content Header from whatever the file contains. A file mid-write at scan time is not read until its modification timestamp has been stable for one scan interval, to avoid ingesting partial content.

**Out of Scope:** Non-Markdown formats; source connectors (Confluence, Jira, forums) — deferred to v1.1.

#### FR-2: Raw Ingest Agreement normalisation

The structuring agent in `nuron-ai` can transform any landed Markdown file into a document matching the Raw Ingest Agreement v1 schema (Content Header: Subject/Reason/Date & Time; Content; Key Discoveries; Tags).

**Consequences (testable):**
- Every file that reaches the Compiler has all four Raw Ingest Agreement sections populated (Tags may be an empty list if no area applies, but the section must be present).
- The Tags block enumerates the functional areas the raw doc touches (e.g. development, testing, user onboarding) and is available to downstream BM25/keyword retrieval.
- A file that cannot be structured (e.g. content too sparse to extract a Subject) is flagged for admin review, with the original file retained and visible in the admin UI, rather than silently dropped or force-compiled.
- **[ASSUMPTION]** The structuring agent runs at a pinned, near-zero temperature so that re-structuring the same raw file is stable; non-deterministic structuring output is treated as a defect (it would otherwise break the Curator's touched-subtree determinism in FR-7).

### 4.2 Compilation

**Description:** The Compiler in `nuron-ai` reads Raw Ingest Agreement documents and emits the standardised LLM-Wiki form the graph ever ingests. Pipeline work is event-driven over RabbitMQ pub/sub, so in-flight ingestion survives a process restart. Realizes UJ-1.

#### FR-3: LLM-Wiki compilation

The Compiler in `nuron-ai` can transform a Raw Ingest Agreement document into an LLM-Wiki document (Executive Summary, Core Entities & Relationships, Known Issues & Verified Solutions, Cross-References).

**Consequences (testable):**
- Every successfully structured document produces exactly one LLM-Wiki document with all four sections present.
- The Compiler runs asynchronously; ingestion throughput is not blocked waiting for compilation to finish.
- Duplicate or near-duplicate raw content (e.g. the same decision restated in two threads) compiles to a single Core Entity, not two. **[ASSUMPTION]** Near-duplicate is determined by an entity-similarity check (e.g. embedding cosine similarity above a pinned threshold) performed at graph-persistence time (FR-5), against existing Core Entities, before a new node is created; candidate merges below full confidence are queued for admin review rather than auto-merged silently.

#### FR-4: Event-driven, restart-safe pipeline dispatch

Workers in `nuron-ai` can dispatch and consume structure, compile, curate, and reply work as RabbitMQ pub/sub events.

**Consequences (testable):**
- A RabbitMQ or worker-process restart does not lose in-flight ingestion, compilation, curation, or reply events; unacknowledged messages are redelivered.
- No pipeline stage has an in-process-only fallback path in v1 — RabbitMQ is a required data-plane component, not optional.

**Feature-specific NFRs:**
- RabbitMQ runs self-hosted inside the customer's deployment, internal network only, with no telemetry or outbound traffic beyond the customer's configured LLM/embedding providers.

### 4.3 Property Graph & Curation

**Description:** The Curator in `nuron-ai` persists compiled LLM-Wiki documents into a LlamaIndex `PropertyGraphIndex` over Neo4j, where Decisions, Entities/Knowledge Nodes, and Supersession edges become first-class graph content. It re-curates the graph on a schedule, touching only subtrees changed since the last pass. Realizes UJ-1, UJ-2.

#### FR-5: Persist compiled content into the property graph

`nuron-ai` can persist an LLM-Wiki document's entities, relationships, and Decisions into the property graph.

**Consequences (testable):**
- Every Core Entity in a compiled document maps to a graph node; every Cross-Reference maps to a graph edge.
- Every Decision node carries author, timestamp, and at least one Evidence citation back to its source raw content.
- Persistence is idempotent — re-ingesting the same compiled document does not create duplicate nodes.

#### FR-6: Decision Supersession modeled at write time

The Curator in `nuron-ai` can create an explicit Supersession edge from a new Decision to the prior Decision it replaces when the evidence indicates a change.

**Consequences (testable):**
- When new evidence contradicts an existing Decision, the Curator pass creates a Supersession edge rather than overwriting or deleting the prior Decision node. "Contradicts" means the new Evidence either explicitly states that a prior Decision was reversed/replaced, or its extracted conclusion conflicts with a prior Decision's core claim about the same named subject (per the Raw Ingest Agreement's Subject field). Candidate contradictions the Curator is not confident about are queued for admin confirmation rather than auto-superseded.
- A Decision's lineage is modeled as a directed graph, not strictly a linear chain — a Decision may have more than one predecessor when independent sources supersede it concurrently; the full set of predecessors is traversable from any point to the present.
- Contradiction reconciliation is a first-class Curator outcome, not a silent averaging of conflicting evidence. Realizes UJ-1's edge case.

#### FR-7: Touched-subtree-only curation

The Curator in `nuron-ai` can re-curate only the subtrees of the graph that have changed since its last successful pass.

**Consequences (testable):**
- A curation pass that follows a change to one branch does not re-process unrelated, unchanged branches.
- Curator output for an unchanged subtree is byte-identical to its prior output (determinism is enforced — pinned temperature, ordered inputs).
- **This FR is conditional on the Merkle-style subtree indexing hypothesis (brief addendum §B, Open Question Q-E) being validated in Architecture.** If curator output cannot be made sufficiently deterministic, or the hash-tree approach doesn't hold up under sibling-context dependencies, the fallback is a heuristic "recently touched" windowing pass (re-curate everything touched in the last N hours) or full periodic re-curation on a longer cadence — not silent abandonment of the performance requirement.

**Feature-specific NFRs:**
- **[ASSUMPTION]** Conditional on Merkle validation: a single-pass re-curation of a 10k-node graph with 1% of subtrees touched completes in under 10 minutes on modest hardware (defined as an 8-core/32GB single-server Docker/Compose host running Neo4j, RabbitMQ, and the compiler/curator workers together). If the Merkle approach is invalidated, the fallback target is a full re-curation of the same 10k-node graph completing in under 2 hours on the same hardware.

### 4.4 Query & Retrieval

**Description:** A synchronous query agent in `nuron-ai` answers questions through hybrid retrieval with three first-class candidate sources: HNSW vector search, BM25 over Tags, and Neo4j GraphRAG. RRF fuses all three ranked result sets, LLMRerank orders the fused candidates, and the selected graph-grounded context is streamed through `nuron-api` over SSE. The GraphRAG source includes structural neighborhoods, relationships, paths, and Supersession-lineage traversal. Realizes UJ-2.

#### FR-8: Hybrid retrieval query

The query agent in `nuron-ai` can answer a natural-language query using HNSW vector retrieval, BM25/Tag keyword retrieval, and Neo4j GraphRAG retrieval as first-class candidate sources, followed by RRF fusion and LLMRerank.

**Consequences (testable):**
- A query about a Decision returns the Decision node, its author, timestamp, cited Evidence, related Entities/Knowledge Nodes, and its Supersession chain to the present, when such a Decision exists.
- A query with no matching Decision returns an explicit "no matching decision" response citing the closest related Entities, rather than a fabricated answer.
- HNSW, BM25/Tags, and Neo4j GraphRAG each produce a ranked candidate list for every query unless that source is unavailable or returns no candidates.
- RRF combines all available candidate lists from the three retrieval sources; LLMRerank orders the fused candidates before answer generation.
- Neo4j GraphRAG retrieves graph-native context using Entity/Decision neighborhoods, relationships, paths, and Supersession lineage; it is not merely a post-ranking expansion step.
- The query API does not require the caller to select a retrieval source. If HNSW, BM25, Neo4j GraphRAG, or LLMRerank times out or errors, the response records the degraded path and the remaining retrieval sources may continue rather than blocking the query.
- If a cited Decision's Evidence has since been removed via right-to-be-forgotten (FR-15) or per-tenant retention, the response marks that citation as unavailable rather than silently omitting or fabricating it.

#### FR-9: Streaming responses over SSE

`nuron-api` can stream a query response to the caller over Server-Sent Events as it is generated.

**Consequences (testable):**
- A client connecting via SSE receives incremental response tokens/segments rather than waiting for the full response.
- A dropped SSE connection does not leave the underlying query in an inconsistent state; the caller can re-query safely.

**Out of Scope:** Write-back to originating sources (no reply-posting loop in v1 — deferred to v1.1, gated on the forum connector).

### 4.5 Agents

**Description:** The Nuron-maintained Default Agent in `nuron-ai` handles ingest and reply actions, one action per request, dispatched over RabbitMQ. Admin-configurable Agent Templates and graph scoping are deferred to v2. Realizes UJ-2; UJ-3 is deferred.

#### FR-10: Default Agent ingest/reply handling

The Default Agent in `nuron-ai` can process a stream of mixed ingest and reply requests, taking exactly one action per request.

**Consequences (testable):**
- Under a benchmark mixed-load stream, every request results in exactly one action (ingest or reply) — no request is processed twice, and no request is silently dropped.
- Ingest and reply requests are dispatched and consumed as RabbitMQ pub/sub events (per FR-4), not handled synchronously in-process.

#### FR-11: Agent Template enablement and scoping — deferred to v2

Agent Template enablement, disablement, and graph scoping are deferred to v2. The FR-11 identifier is retained as the downstream anchor; it is not a v1 acceptance requirement. See UJ-3, SM-4, and §5.5.

**Out of Scope (v1):** Nuron-supplied Agent Templates and freeform/customer-authored agent graphs.

### 4.6 Admin, Access & Tenant Governance

**Description:** A built-in user store with admin-provisioned accounts (admin + standard roles) governs access. Admins configure sources, connection settings, and users through `nuron-web` or REST. Tenant-scoping primitives, a right-to-be-forgotten deletion path, per-tenant rate limits, and an audit log are in place from v1 so v2's multi-tenant service does not require a rewrite.

#### FR-12: Built-in user store and roles

The system can authenticate users against a built-in, admin-provisioned user store with admin and standard roles.

**Consequences (testable):**
- An admin can create, disable, and role-assign standard-user accounts; standard users cannot self-register.
- Standard users cannot access admin-only configuration surfaces (source setup, connection settings, user management).
- A fresh deployment with an empty user store presents a one-time setup flow to create the first admin account (not a locked login screen with no path forward).
- The system prevents disabling or demoting the last remaining admin account, so the deployment cannot be locked out of its own administration surface.

**Out of Scope:** OIDC/SAML/Entra SSO — non-goal for v1.

#### FR-13: Source and system configuration via admin UI/REST

An admin can configure ingestion sources, MCP-style connection settings, and other v1 system settings through `nuron-web` or directly via REST, with no code changes required.

**Consequences (testable):**
- Every configuration action available in `nuron-web` has an equivalent REST endpoint (API-first; frontend is replaceable).
- A configuration change (e.g. new ingest directory or connection setting) takes effect without a service restart.

#### FR-14: Tenant scoping primitives

Every persistent record (graph node, edge, queue message, audit entry) can carry a tenant/workspace identifier, and each tenant's data lives in an isolated data directory.

**Consequences (testable):**
- No two tenants' records share a data directory, even when co-located on the same host.
- API requests are scoped server-side to the caller's tenant using the tenant identifier embedded in the caller's verified auth token/session — never a tenant identifier supplied in the request body or query parameters. A request whose token carries no valid tenant claim is rejected rather than defaulted to any tenant.

#### FR-15: Right-to-be-forgotten deletion

An admin can trigger a tenant/workspace-scoped deletion that removes that tenant's data directory, graph nodes/edges, queue messages, and audit entries.

**Consequences (testable):**
- After a deletion request completes, no query against the system returns content that was scoped to the deleted tenant.
- Deletion is scoped strictly to the requested tenant/workspace; other tenants' data is unaffected.
- Deletion scope is explicit and covers: the tenant's data directory, graph nodes/edges, in-flight and queued RabbitMQ messages tagged with that tenant, audit log entries, and any cache. If backups are enabled, the tenant's data is purged from backups on the next backup cycle at latest — deletion is not considered complete while a plain-text backup of deleted data still exists beyond that window.
- Default retention is indefinite until a deletion is explicitly requested; retention is configurable per tenant.

#### FR-16: Per-tenant rate limiting

`nuron-api` can enforce a configurable request-rate limit keyed on tenant identifier, implemented in Laravel (no additional edge-gateway dependency in v1).

**Consequences (testable):**
- A caller (including an internal automation agent) exceeding the configured per-tenant limit receives a rate-limit error response rather than degrading service for other tenants.
- The rate limit is enforced consistently across every `nuron-api` endpoint, not opt-in per endpoint.
- Interactive human users and registered internal automation agents (§2.1) are rate-limited in separate buckets within the same tenant, so a high-volume automation agent cannot exhaust the quota human users depend on.

#### FR-17: Audit log

Every query response can be traced to the graph node(s) it was grounded in, and every node can be traced back to its source Evidence.

**Consequences (testable):**
- Given any query response, an admin can retrieve the specific Decision/Entity node(s) it cited.
- Given any graph node, an admin can retrieve the raw Evidence/Episode it derived from.
- Audit entries are tenant-scoped (per FR-14) even though v1 ships no dedicated per-tenant audit UI.

## 5. Cross-Cutting Requirements

### 5.1 Non-Functional Requirements

- **Deployment:** self-hosted, single-tenant, Docker/Compose stack running on whatever infrastructure the customer has.
- **No telemetry by default:** no usage analytics, no error reporting, no model-call traces leave the customer's network. The only outbound traffic is whatever the customer explicitly configures (LLM provider, embedding provider).
- **Restart-safety:** the pipeline (FR-4) survives a process or broker restart without losing in-flight work.
- **API-first:** every capability `nuron-web` exposes has a REST equivalent (FR-13); the frontend is replaceable without touching `nuron-api`.

### 5.2 Constraints & Guardrails

- **Privacy:** no real customer PII is processed until the right-to-be-forgotten primitive (FR-15) is in place. Tenant isolation (FR-14) is the default posture, not an opt-in.
- **Embedding model floor:** minimum a general-purpose embedding model in the ≥600M-parameter class with ≥1024-dim output. Default for v1 is an OpenAI-class hosted embedding model (e.g. `text-embedding-3-large`, 3072-dim), with a local-model option when the customer also self-hosts their LLM. The exact model/dimensions are pinned in Architecture; this PRD commits only to the floor.
- **Cost:** the only paid dependencies a customer takes on are their chosen LLM/embedding providers; every other component (Neo4j, RabbitMQ, `nuron-api`, `nuron-web`) is self-hosted and open-core.

### 5.3 Integration & Dependencies

- **Neo4j** — property graph store and first-class GraphRAG retrieval source, accessed via LlamaIndex `PropertyGraphIndex` for graph-native candidate retrieval across neighborhoods, relationships, paths, and Decision lineage.
- **RabbitMQ** — required async backbone (pub/sub) for every pipeline stage (FR-4); self-hosted inside the customer's deployment, internal network only.
- **`nuron-ai`** — internal AI processing and RAG service hosting the structuring agent, Compiler, Curator, Default Agent, runtime query agent, and LlamaIndex `PropertyGraphIndex` integration.
- **LlamaIndex** — agentic framework used inside `nuron-ai` for structuring, compilation, curation, Default Agent processing, retrieval, and the `PropertyGraphIndex`.
- **Laravel (`nuron-api`) / SvelteKit + Bits UI (`nuron-web`)** — the API/auth surface and the reference admin client, respectively, as separate containers.
- **LLM / embedding providers** — customer-configured; the only external network calls the system makes.

### 5.4 API Contract (high-level)

- `nuron-api` exposes REST endpoints for source and system configuration, user/role management, tenant deletion (FR-15), and query — plus an SSE stream for query responses (FR-9). Full request/response schemas are an Architecture deliverable.
- `nuron-api` delegates AI processing and RAG to `nuron-ai`; the transport between them is an Architecture decision.
- `nuron-api` and `nuron-ai` are reachable only on the internal Docker network. `nuron-web` is the only container exposed to the customer's reverse proxy/ingress and does not call `nuron-ai` directly.

### 5.5 Rollout & Phasing

- **v1 (this PRD):** the pipeline proven against seed Markdown, single-tenant self-hosted, Default Agent, and tenant-scoping primitives in place but no v2 UI on top of them.
- **v1.1:** Confluence page export, internal forum ingestion (unlocks write-back once Q-C-adjacent connector work lands), Jira issue ingestion. Apache APISIX (or equivalent edge gateway) is a parked candidate for edge-level rate limiting/auth enforcement if untrusted/high-volume traffic materialises — not committed for v1, where per-tenant rate limiting is handled in Laravel (FR-16).
- **v2:** managed multi-tenant Nuron-as-a-service plus admin-configurable Agent Template enablement and graph scoping (FR-11/UJ-3/SM-4), built on the v1 tenant-scoping primitives (FR-14) without a rewrite. Cloud platform and pricing are explicitly open, not decided in this PRD. APISIX (or equivalent) becomes more likely here once a single external chokepoint across tenants is worth the ops cost.

## 6. Non-Goals (Explicit)

- Admin-configurable Agent Template enablement and graph scoping (FR-11) — v2.
- Freeform, customer-authored agent graphs — not part of v1; the future authoring posture is undecided.
- Source connectors beyond Markdown file ingestion (Confluence, Jira, forums) — v1.1.
- OIDC / SAML / Entra SSO — built-in user store only (FR-12).
- Multi-tenant managed Nuron-as-a-service, tenant management UI, billing, metering — v2.
- Non-textual content (images, diagrams, video, audio transcripts) — text only in v1.
- Write-back to originating sources (no reply-posting loop) — v1.1, gated on the forum connector.
- OpenClaw / agent-platform plugin — ships only if user demand materialises post-v1.

## 7. MVP Scope

### 7.1 In Scope

- Self-hosted, single-tenant deployment via Docker/Compose (§5.1).
- Built-in user store with admin-provisioned accounts, admin + standard roles (FR-12).
- Markdown ingestion from a configured, recursively-scanned directory (FR-1) and Raw Ingest Agreement normalisation (FR-2).
- `nuron-ai` hosting the LlamaIndex-based Compiler and Curator, emitting LLM-Wiki documents (FR-3) and processing pipeline work over RabbitMQ (FR-4).
- LlamaIndex `PropertyGraphIndex` over Neo4j (FR-5) with three-source hybrid retrieval: HNSW vector search, BM25/Tags, and Neo4j GraphRAG; RRF fusion and LLMRerank follow candidate retrieval (FR-8).
- Default Agent handling ingest and reply, one action per request (FR-10).
- Curator re-curating only touched subtrees (FR-7), with Supersession modeled at write time (FR-6).
- `nuron-web` (SvelteKit + Bits UI): source setup, MCP-style connection configuration, graph view, and query UI (FR-13).
- REST API + SSE streaming (FR-9), tenant-scoping primitives (FR-14), right-to-be-forgotten (FR-15), per-tenant rate limiting (FR-16), audit log (FR-17).

### 7.2 Out of Scope for MVP

Everything listed in §6 Non-Goals. FR-11, UJ-3, and SM-4 are explicitly deferred to v2. `[NOTE FOR PM]` the write-back non-goal is emotionally load-bearing for the "closing the read-and-respond loop" narrative in the brief — revisit as soon as the forum connector is scheduled.

## 8. Success Metrics

A non-quantitative signal we watch for regardless of the metrics below: in the first design-partner deployment, people who have been at the company over a year **return to Nuron voluntarily**, not because they were told to. That is the continuity claim's first real test.

**Primary**
- **SM-1**: Decision lineage answerable — for any Decision in the graph, a query returns the Decision, its author, timestamp, cited Evidence, and its Supersession chain to the present. End-to-end test passes on the demo seed dataset. Validates FR-6, FR-8.
- **SM-2**: Pipeline proven end-to-end — a folder of Markdown files dropped into the configured location results in a graph update and a grounded, cited query response, with no human intervention between ingest and query. Validates FR-1, FR-2, FR-3, FR-5, FR-8.
- **SM-3**: Default Agent handles mixed load correctly — a benchmark stream of mixed ingest + reply requests is processed without loss or duplication, one action per request. Validates FR-10.

**Secondary**
- **SM-5**: **[ASSUMPTION]** Curator touched-subtree performance — conditional on the Merkle-style subtree hypothesis (Q-E) being validated: a single-pass re-curation of a 10k-node graph with 1% of subtrees touched completes in under 10 minutes on modest hardware. If Merkle validation fails, the fallback target is a full re-curation of the same graph in under 2 hours. Validates FR-7.

**Deferred to v2**
- **SM-4 [v2]**: Admin-configured Agent Template runs unassisted — a template-derived agent runs in the customer's environment via admin UI/REST configuration only, no engineering involvement from Nuron. Validates FR-11 and is not a v1 release criterion.

**Counter-metrics (do not optimize)**
- **SM-C1**: Raw ingestion throughput should never be optimized by skipping or weakening Raw Ingest Agreement validation (FR-2) — a fast but unstructured ingest degrades everything downstream. Counterbalances SM-2.
- **SM-C2**: Query latency should never be optimized by dropping any of the HNSW, BM25, or Neo4j GraphRAG candidate sources (FR-8) — a fast but incomplete retrieval path defeats the decision-lineage claim. Counterbalances SM-1.

## 9. Open Questions

**Carried from the brief (deferred to Architecture):**

1. **Agentic framework reconciliation.** This PRD names LlamaIndex (per user correction) as the agentic backend hosted in `nuron-ai` for the structuring agent, Compiler, Curator, Default Agent, and runtime query agent; the source brief's narrative names LangGraph in several places. Architecture phase must reconcile and update the brief/ADRs accordingly.
2. **Q-A · Cloud platform for v2 managed service.** GCP vs. Azure vs. other — resolve during v2 planning, not v1.
3. **Q-E · Merkle-style subtree indexing feasibility.** Hypothesis underlying FR-7's touched-subtree curation (brief addendum §B). Validate or invalidate during Architecture.
4. **Q-G · Managed-wrapper / single-vendor risk** for LlamaIndex-adjacent or Neo4j-adjacent managed offerings. Decide in Architecture whether the risk warrants self-hosting dependencies or wrapping instead.
5. **Q-H · Tenant scoping key(s) for v1.** Confirm in Architecture whether v1 needs more than a single `workspace`/`team` scoping key (FR-14), given v1 is single-tenant per deployment.
6. **Q-J · Container/compose topology.** Confirm the full topology in Architecture, including `nuron-web`, `nuron-api`, `nuron-ai`, Neo4j, and RabbitMQ placement; define the `nuron-api` ↔ `nuron-ai` transport and the exchange/queue/routing-key layout for the pub/sub pipeline (FR-4).

**Raised during PRD review (2026-07-08, reviewer gate):**

7. **Message ordering, redelivery idempotency, and dead-letter handling.** FR-4 commits to at-least-once, restart-safe delivery, but per-document ordering guarantees, deduplication on redelivery, and dead-letter/poison-message handling for the compile/curate/reply stages are not specified. Architecture must define these.
8. **Schema versioning for Raw Ingest Agreement, LLM-Wiki, and graph nodes.** None of FR-2, FR-3, or FR-5 carry a version field; a future schema change (v1.1+) has no defined migration path for already-ingested content. Architecture must define a versioning and migration strategy.
9. **Audit log retention, access control, and tamper-resistance.** FR-17 defines traceability but not retention policy, who can read the audit log, or protection against manual tampering/deletion of audit rows. Needs a decision before the audit log is treated as a compliance artifact.
10. **Graceful degradation when Neo4j is unavailable.** The PRD does not specify fallback behavior (fail fast vs. cached results) if the graph store is temporarily unreachable. Architecture should decide.
11. **Observability requirements.** No logging/metrics/tracing requirements are specified for the pipeline or query path; needed for production support once v1 ships to a design partner.
12. **`nuron-api` versioning policy.** No API versioning scheme (e.g. `/v1/...`) or deprecation policy is specified; needed before customer automation depends on the API.
13. **SM-1 validation corpus.** SM-1 currently validates against "the demo seed dataset." Consider whether a design-partner deployment should also validate decision-lineage accuracy against a larger, messier, real-world-shaped corpus rather than a hand-curated seed set alone.
14. **FR-10 benchmark parameters.** "A benchmark mixed-load stream" (FR-10) is not parameterized (request rate, ingest/reply ratio). Needs a concrete definition before it's testable as an acceptance criterion.
15. **Evidence vs. Episode terminology.** The Glossary's "Evidence / Episode" entry conflates two possibly-distinct concepts (a single source unit vs. a temporal sequence of source units). Clarify or drop "Episode" during Architecture/UX terminology pass.
16. **"MCP-style connection settings" (FR-13) terminology.** Confirm whether this refers to Anthropic's Model Context Protocol or a Nuron-specific convention, and define it precisely before UX designs the source and connection screens.
17. **Retrieval parameterization.** The three-source hybrid sequence is confirmed as HNSW vector retrieval + BM25/Tags + Neo4j GraphRAG → RRF across all available candidate lists → LLMRerank. Architecture must pin HNSW index/search parameters, BM25 tokenizer/configuration, GraphRAG query/traversal strategy and ranking, the RRF constant, the LLMRerank model and candidate limits, and per-source degraded-path behavior.

## 10. Assumptions Index

- Inline assumption from §4.1 FR-2 — the structuring agent's determinism (near-zero temperature) is assumed sufficient to keep Curator hashing stable; not yet empirically validated against the chosen LLM.
- Inline assumption from §4.2 FR-3 — near-duplicate Core Entity detection is assumed to work via an embedding-similarity threshold; the exact threshold is not yet pinned.
- Inline assumption from §4.3 FR-7 NFR / §8 SM-5 — curator touched-subtree performance budget (10k-node graph, 1% touched, under 10 minutes on modest hardware) is conditional on the Merkle-style subtree hypothesis (Q-E) being validated, with a full-re-curation fallback target (under 2 hours) if it is not.
