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
- **Touched subtree.** A subtree of the graph whose root hash has changed since the last successful curator pass. See Section B (Merkle hypothesis) and Section H.
- **Tenant.** A single customer deployment. v1 is single-tenant per deployment. v2 may be multi-tenant per managed deployment.

---

## H. Competitor Landscape — Mem0, Cognee, Graphiti

Research date: 2026-07-06. All repo stats fetched live from the GitHub REST API; READMEs and source from `raw.githubusercontent.com`. This section is the source of record that supports the sharpened differentiators in `brief.md` §"What Makes This Different" pillars #2–#4.

### H.1 · Identity & positioning

| Project | Canonical repo (verified live) | Primary lang | Stars / forks | License | Latest release |
|---|---|---|---|---|---|
| **Mem0** | `mem0ai/mem0` (note: `membranepartners/mem0` does not host the active repo — the namespace redirected to `mem0ai`) | Python + TypeScript | **60,222 / 6,984** | Apache-2.0 | `ts-v3.0.13` (2026-07-01) |
| **Cognee** | `topoteretes/cognee` | Python (+ TS client `cognee-ts`, Rust client `cognee-rs`) | **27,236 / 2,530** | Apache-2.0 | `v1.2.2` — Truth Subspace & Retrieval Improvements (2026-06-26) |
| **Graphiti** | `getzep/graphiti` | Python (+ TS SDK) | **28,422 / 2,853** | Apache-2.0 | `v0.29.2` — FalkorDB Bug Fixes (2026-06-08) |

> **License correction to an earlier draft of the brief:** Graphiti is **Apache-2.0**, not BSL. Confirmed against the LICENSE file in all three repos. The commercial wrapper is the SaaS product (Zep, Cognee Cloud, Mem0 Platform), not the open-source graph/memory engine.

**One-line descriptions (from each project's own README):**

- **Mem0** — "Universal memory layer for AI Agents" / "Mem0 ('mem-zero') enhances AI assistants and agents with an intelligent memory layer, enabling personalized AI interactions."
- **Cognee** — "Cognee is the open-source AI memory platform for agents… a self-hosted knowledge graph engine." Tagline: "Build Company Brain."
- **Graphiti** — "Build Real-Time Knowledge Graphs for AI Agents." Subtitle: "Build Temporal Context Graphs for AI Agents." Powers Zep's commercial agent context product.

### H.2 · Per-project architecture summary

#### Mem0

- **Storage:** Vector-first — 12+ vector stores (Qdrant, Chroma, pgvector, Milvus, Pinecone, Azure AI Search, Redis/Valkey, Upstash Vector, MongoDB, Databricks, Cloudflare Vectorize, S3 Vectors, in-memory SQLite). Provider registry at `mem0/utils/factory.py:178–191`.
- **Graph store:** **Removed from OSS in v3.0.** External `graph_store` providers (Neo4j / Memgraph / Kuzu / Apache AGE / Neptune) were deleted (~4,000 lines) — see `docs/changelog/sdk.mdx#L240`. Built-in entity graph now lives in a parallel `{collection}_entities` collection inside the chosen vector store.
- **Tiers:** "Multi-Level Memory: User, Session, and Agent state" (`user_id` / `agent_id` / `run_id` / `app_id`).
- **Embeddings:** Default `text-embedding-3-small`; README recommends ≥ Qwen 600M for hybrid search.
- **Async pipeline:** **OSS library is synchronous, in-process.** No broker in the open-source SDK. Hosted platform added SQS for graph memory (2025-06-28). Self-hosted server uses FastAPI.
- **Agent framework:** None directly. Ships LangChain, LangGraph, CrewAI, OpenClaw, Claude Code, Codex integrations.

#### Cognee

- **Storage:** Hybrid by default; **v1.0 path is single Postgres** for graph + vector + cache + metadata. Optional Neo4j / Kuzu / Ladybug / Neptune.
- **Graph store:** Postgres-backed (default 1.0); "Postgres search ran ~10% faster than separate graph+vector" claim.
- **Tiers:** Three-tier — **permanent memory** (`cognee.remember(...)` full pipeline), **session memory** (`session_id="..."` writes to cache then bridges to graph), **datasets** as the primary scoping unit (multi-tenant: user → dataset → data).
- **Embeddings:** Pluggable — OpenAI, Voyage, Sentence-Transformers, Gemini, FastEmbed, LiteLLM, Ollama.
- **Async pipeline:** In-process `run_in_background` flag on `cognee.add` / `cognee.cognify`. FastAPI BackgroundTasks + asyncio pipelines. No external broker. `PipelineRunInfo` rows track status.
- **Agent framework:** None. Ships MCP server, Claude Code plugin, Rust + TS clients, OpenClaw plugin (`@cognee/cognee-openclaw`).
- **Polymodal ingest:** text + PDFs + images (OCR) + audio + video + 3D.
- **COGX format:** portable memory migration between backends (`cognee/modules/migration/cogx.py`).

#### Graphiti

- **Storage:** **Graph-only.** Neo4j 5.26, FalkorDB 1.1.2 (default), Amazon Neptune + OpenSearch Serverless, Kuzu 0.11.2 (**deprecated** in v0.29.2). Embeddings stored as `FLOAT[]` on nodes/edges; no separate vector DB.
- **Tiers:** `group_id` is the partition key. Temporal model with `valid_at` / `invalid_at` on edges; episodes are the lineage anchor. Structurally two-tier (raw `EpisodicNode` vs derived `EntityNode`/`EntityEdge`) plus a third `CommunityNode` tier for clusters.
- **Embeddings:** Default `text-embedding-3-small` (1536 dim, `EMBEDDING_DIM` defaults). OpenAI, Voyage, Sentence-Transformers, Gemini.
- **Async pipeline:** In-process per-group `asyncio.Queue` (`mcp_server/src/services/queue_service.py`). Core uses `SEMAPHORE_LIMIT` env var (default 10) for parallelism. REST server uses single global `AsyncWorker`. **No persistence** — episodes queued at restart are lost.
- **Agent framework:** None in core. README references LangGraph + Graphiti integration. MCP server first-class.
- **Saga support:** `SagaNode` + `saga_previous_episode_uuid` for sequential multi-episode context.
- **Coupling:** Tight coupling to OpenAI structured-output contract; "Graphiti works best with LLM services that support Structured Output (such as OpenAI, Anthropic, and Gemini)."

### H.3 · Differentiators each project advertises

**Mem0** — 91.6 LoCoMo / 94.8 LongMemEval benchmark numbers; multi-signal retrieval fusion (semantic + BM25 + entity matching, post-v3.0); temporal reasoning that ranks the right dated instance for queries about current / past / upcoming; entity linking as built-in; three product tiers (library / self-hosted server / cloud).

**Cognee** — "Build Company Brain" multi-source unification; persistent and learning agents; agentic user/tenant isolation; BEAM benchmark claims (0.79 @ 100K, 0.67 @ 10M tokens); single-Postgres deployment; polymodal ingest; COGX portable migration format.

**Graphiti** — Bi-temporal context graph (`valid_at` / `invalid_at`); superseded facts are *invalidated, not deleted*; every entity traces back to an episode; hybrid retrieval (semantic + BM25 + graph traversal + MMR reranker); pluggable graph backends; custom entity/edge types via Pydantic; saga support; community detection; MCP and REST servers first-class.

### H.4 · Gaps / weaknesses visible in the repos

**Mem0** — OSS v3.0 gutted the external graph store; documented graph-backend support is gone from the OSS SDK. "Multi-Level" tier model is filtering on `user_id`/`agent_id`/`run_id`, not a formal working-vs-long-term type-system. Library is single-process / synchronous (async only via `AsyncMemory` wrapping `asyncio.to_thread`). Major API churn per `docs/changelog/sdk.mdx#L240`.

**Cognee** — Single-process pipeline executor; "agentic user/tenant isolation" via `ENABLE_BACKEND_ACCESS_CONTROL=True` is application-layer, not infra-layer. `examples/demos/pipeline_api_proposal.py` is a literal TODO / rewrite-in-progress. 618 open issues / 27k stars — high issue backlog vs maintenance bandwidth. Hybrid retrieval (`truth_subspace`, `global_context_index`) marked **experimental**. Heavy config surface (`cognee/api/v1/config/config.py` ~500 lines).

**Graphiti** — **Anonymized PostHog telemetry on by default** (`GRAPHITI_TELEMETRY_ENABLED=false` to opt out). Kuzu backend officially deprecated mid-2026. MCP server's per-`group_id` queue has no persistence — process restart drops queued episodes. Tight coupling to OpenAI structured output; smaller / local models fail extraction. 413 open issues; tied to Zep's commercial priorities (README literally says "We're Hiring!" for Zep).

### H.5 · Cross-cutting comparison

| Dimension | Mem0 | Cognee | Graphiti |
|---|---|---|---|
| **Storage backend** | Vector-first (12+ stores) + built-in entity graph in vector DB. **External graph deprecated in OSS.** | Hybrid; v1.0 = **single Postgres** for graph + vector + cache + metadata. Optional Neo4j / Kuzu / Ladybug / Neptune. | **Graph-only** (Neo4j, FalkorDB, Neptune; Kuzu deprecated). No separate vector DB. |
| **Tenant model** | Per-row filtering by `user_id` / `agent_id` / `run_id` / `app_id`. Hosted = multi-tenant; OSS library = single-process per deployment. | Application-layer multi-tenant via `ENABLE_BACKEND_ACCESS_CONTROL=True` (per-user, per-dataset Postgres rows). | `group_id` is the partition key. No tenant hierarchy above group. Per-deployment = single-tenant. |
| **Self-host story** | `pip install mem0ai` (library); `cd server && make bootstrap` (Docker Compose stack with auth + admin wizard). OpenMemory dashboard. | **Docker Compose** with profiles (`ui / mcp / postgres / neo4j`). One-command Railway / Fly / Modal deploys via `distributed/deploy/*.sh`. Cognee Cloud is managed. | Docker Compose (`docker compose up` for Neo4j; `--profile falkordb up` for FalkorDB). MCP server with combined FalkorDB container. Standalone FastAPI REST server. **No Helm chart.** |
| **Async pipeline** | **In-process sync** in OSS. Hosted platform uses SQS for graph memory. | **In-process asyncio** with `run_in_background` flag. No external broker. | **In-process `asyncio.Queue` per `group_id`** in MCP server. Core uses `SEMAPHORE_LIMIT`. REST server uses single global `AsyncWorker`. **No persistence.** |
| **Decision-lineage / supersession** | None formal. Temporal reasoning in v3 is retrieval-time ranking, not graph invalidation. | No explicit "decision" entity. v1.2.2 adds "truth subspace" centroids/slots for *learned feedback* reranking — orthogonal to supersession. | **Best in class.** Every edge has `valid_at` / `invalid_at` / `expired_at` / `invalidated_by_episode`. Supersession is automatic — facts are *invalidated, not deleted*. Full audit chain to source episodes. |
| **Telemetry default** | Analytics hooks in self-hosted server images. | Analytics hooks in self-hosted images. | **PostHog on by default** (opt-out). |
| **License** | Apache-2.0 | Apache-2.0 | Apache-2.0 |
| **Last release / activity** | `ts-v3.0.13` 2026-07-01 (Node SDK); Python SDK follows separate cadence. Pushed 2026-07-06. | `v1.2.2` 2026-06-26. Pushed 2026-07-06. | `v0.29.2` 2026-06-08. Pushed 2026-07-06. ~30 PRs merged into v0.29.2. |

### H.6 · Nuron-relevant lessons

**From Mem0:**
- ✅ **Adopt:** multi-signal retrieval fusion (semantic + BM25 + entity boost); the flat filter-key model (`user_id` / `agent_id` / `app_id` / `run_id`) as a lightweight scoping pattern for v1; the agent-mintable API key UX (e.g. `mem0 init --agent`) as a friendly first-run experience.
- ❌ **Do NOT copy:** Mem0's v3.0 decision to gut graph stores from OSS — for Nuron the graph *is* the product. Avoid the library / self-hosted-server / cloud three-tier split (Nuron is single-tenant self-hosted v1; don't promise a SaaS tier yet).

**From Cognee:**
- ✅ **Adopt:** single-Postgres deployment as a v2 lever (claim: ~10% faster than separate graph+vector; shrinks ops surface); session → permanent bridge pattern (mirrors Nuron's auto-recall-before / auto-capture-after loop); COGX-style portable migration as an answer to lock-in fears for self-hosters (park for v2).
- ❌ **Do NOT copy:** the sprawling pipeline API surface; the multi-tenant "single Postgres with permission filters" model (Nuron v1 is single-tenant); the OpenClaw / plugin-per-everything strategy; the "let users define custom graph schemas at runtime" model (fragments the graph).

**From Graphiti:**
- ✅ **Adopt:** episodes-as-provenance (every entity/edge traces back to a raw episode — this is *exactly* what Nuron's "evidence behind decisions" pillar needs); bi-temporal `valid_at` / `invalid_at` as the cleanest existing implementation of decision supersession; FalkorDB-as-default-with-Neo4j-as-fallback as pragmatic for self-hosters (FalkorDB runs as a single Docker container; Neo4j needs JVM).
- ❌ **Do NOT copy:** tight coupling to OpenAI structured output (breaks on smaller/local models); in-process asyncio queue (lost on restart); opt-out PostHog telemetry (enterprise procurement will flag it); single-vector-DB-as-graph embedding approach (Nuron has property-graph ambitions that exceed that).

### H.7 · Implications for the Nuron brief (recap)

1. **License correction.** All three competitors are Apache-2.0. Self-hosted distribution is not blocked by licensing. The real risk is being tied to a *managed wrapper* vendor's roadmap (Zep / Cognee Cloud / Mem0 Platform) — captured as **Q-G**.
2. **Single-Postgres deployment is table stakes for v2.** Cognee's 1.0 story is a direct answer to Nuron's self-hosted single-tenant positioning. v1 keeps Neo4j as the property graph; v2 should commit to an ops-portable Postgres path as an option.
3. **Sharpen decision-lineage as the moat.** Graphiti's bi-temporal invalidation is the most mature supersession story. Nuron must explicitly model supersession *and contradiction* at write time, with the raw episode as the lineage anchor. (See `brief.md` Pillar #2.)
4. **Tenant scoping follows Mem0's flat filter keys.** For v1 single-tenant, a flat `workspace` (or `team`) filter is sufficient; defer Cognee-style RBAC. (See **Q-H**.)
5. **Async pipeline: RabbitMQ is genuinely differentiated, but in-process default may ship for v1.** None of the three competitors run an external broker. (See `brief.md` Pillar #3 and **Q-I**.)
6. **"No telemetry by default" is a procurement-friendly claim.** Nuron's posture here differentiates from all three. (See `brief.md` Pillar #4.)
7. **Embedding-model guidance should be explicit.** Mem0 says ≥ Qwen 600M; Graphiti default `EMBEDDING_DIM=1024`. (See `brief.md` "Recommended embedding model floor".)
8. **OpenClaw is now crowded.** Mem0 + Cognee both ship OpenClaw plugins. Nuron should NOT prioritize OpenClaw in v1. (See `brief.md` "Explicit non-goals".)
9. **Benchmark methodology must be open if we claim numbers.** Mem0 has open-sourced its evaluation framework (`mem0ai/memory-benchmarks`); Cognee has not. If Nuron claims benchmark numbers, the methodology must be published.

### H.8 · Method / sources

**Repos read (live, via GitHub REST + raw):**

- `github.com/mem0ai/mem0` — README, LICENSE, `mem0/utils/factory.py`, `mem0/vector_stores/{qdrant,redis,valkey,pgvector,neptune_analytics}.py`, `mem0/memory/main.py`, `mem0-ts/src/oss/src/vector_stores/*`, `docs/changelog/sdk.mdx#L240`, `docs/platform/features/graph-memory.mdx`, `docs/migration/oss-v2-to-v3.mdx`, `examples/graph-db-demo/neo4j-example.ipynb`, `AGENTS.md`, `docs/core-concepts/how-it-works.mdx`.
- `github.com/topoteretes/cognee` — README, LICENSE, `CLAUDE.md`, `cognee/api/v1/remember/remember.py`, `cognee/modules/pipelines/tasks/task.py`, `cognee/modules/pipelines/operations/run_tasks.py`, `cognee/infrastructure/databases/vector/{pgvector,embeddings}/*`, `cognee/modules/migration/cogx.py`, `cognee/modules/migration/sources/__init__.py`, `cognee/api/v1/add/add.py`, `cognee/api/v1/cognify/cognify.py`, `cognee/api/v1/cognify/routers/get_cognify_router.py`, `cognee/memify_pipelines/global_context_index.py`, `cognee/modules/truth_subspace/*`, release body for v1.2.2.
- `github.com/getzep/graphiti` — README, LICENSE, `CLAUDE.md`, `graphiti_core/graphiti.py`, `graphiti_core/{driver,llm_client,embedder,search,nodes,edges}/*`, `graphiti_core/helpers.py`, `graphiti_core/utils/content_chunking.py`, `graphiti_core/utils/maintenance/node_operations.py`, `graphiti_core/llm_client/config.py`, `mcp_server/README.md`, `mcp_server/src/services/queue_service.py`, `mcp_server/src/graphiti_mcp_server.py`, `mcp_server/src/config/schema.py`, `server/graph_service/{config,zep_graphiti,routers/ingest,routers/retrieve}.py`, `spec/driver-operations-redesign.md`, release body for v0.29.2.

**Web fetches:** raw `README.md` and `LICENSE` from each repo; `api.github.com/repos/{owner}/{repo}` for star/fork/license/release data.

**Could not verify (gaps, marked as such):**
- Mem0 OSS server multi-tenant isolation strength — README is light; `ENABLE_BACKEND_ACCESS_CONTROL`-equivalent not documented as a single feature flag.
- Cognee's hosted "Cognee Cloud" SLA / scale numbers — README links only to sign-up.
- Helm chart for any of the three — searched, not found. Self-host is Docker Compose / standalone Docker only.
- Mem0's `mem0ai` namespace provenance — verified active, but did not confirm historical reason for move from `membranepartners/mem0`.
- GraphRAG / Mem0 official comparison benchmarks at decision-lineage — not found in any of the three repos. Cognee cites BEAM; Mem0 cites LoCoMo + LongMemEval; Graphiti cites Zep SOTA. No apples-to-apples comparison located.

---

## I. Async Pipeline Trade-offs (companion to brief Pillar #3 + Q-I)

This section captures the durable-async vs in-process-async trade-off the brief leaves open as **Q-I**, so the Architecture phase has the full context without re-deriving it.

### I.1 · What RabbitMQ gives us (the case for opt-in / opt-out external broker)
- **Durability across restarts.** In-flight compiler / curator jobs survive an API or worker process restart. Graphiti's `asyncio.Queue` and Cognee's `run_in_background` tasks both lose queued work on restart.
- **Backpressure decoupling.** A burst of source-system ingestion cannot starve runtime query workers. With in-process, both paths share CPU / memory.
- **Horizontal scale-out.** Multiple compiler workers consume from the same queue; throughput scales linearly with worker count.
- **Operational visibility.** Queue depth, message age, dead-letter inspection — all standard ops signals that come for free with RabbitMQ.

### I.2 · What RabbitMQ costs (the case for in-process default in v1)
- **Another container to operate.** v1 self-hosters must run RabbitMQ alongside Neo4j, Laravel, the Svelte frontend, and the compiler worker. Each additional container is an ops-tax increase that translates to a slower design-partner deployment.
- **Schema migrations touch queue topology.** Adding a new message type requires a new queue + routing key + consumer. In-process pipelines can just call a function.
- **Debugging surface.** "Why is message X stuck?" is a queue question, not a code question.
- **Mismatched scaling story for single-tenant v1.** None of the three competitors run an external broker; the v1 customer base (one company brain per deployment) is unlikely to hit backpressure problems in the first 12 months.

### I.3 · Proposed v1 default (Architecture to confirm)
- **v1 default:** in-process pipeline (mirrors Cognee's `run_in_background` toggle) with explicit "queue mode" config flag.
- **v1 opt-in:** RabbitMQ via the same flag (`NURON_QUEUE_MODE=in-process | rabbitmq`).
- **Documentation:** the v1 README must explicitly call out the durability / scale trade-off so self-hosters choose knowingly.
- **v2:** if multi-tenant managed service ships, RabbitMQ becomes the default (per-tenant queues, dead-letter routing, ops visibility are non-negotiable at multi-tenant scale).

---

## J. Embedding Model Floor — Rationale (companion to brief "Recommended embedding model floor")

Supports the brief's commitment that v1 ships a minimum-quality embedding model class, not a free choice.

### J.1 · Why a floor (not a recommendation)
A free recommendation ("use whatever embedding model you like") produces a fragmented deployment population where some instances have meaningful decision-lineage retrieval and others have noise. The brief's continuity claim depends on retrieval working consistently. A floor trades configurability for a quality guarantee.

### J.2 · Why ≥ 600M parameters / ≥ 1024 dim
- Mem0 README: "we recommend using at least Qwen 600M… for best results with hybrid search."
- Graphiti default: `EMBEDDING_DIM=1024`; their `text-embedding-3-small` default is 1536 dim.
- Working consensus across open-source memory / graph-RAG projects is that sub-600M embedding models degrade sharply on long-form, mixed-register enterprise text.

### J.3 · Why the quality bar matters
Enterprise-shaped text (decision emails, design docs, post-mortems, Confluence pages) is structurally different from short factual sentences (the common embedding-model benchmark domain). A model that benchmarks well on MTEB / BEIR can still fail on enterprise-shaped text in ways that only show up after a few thousand real documents. The v1 quality bar — "must produce semantically meaningful similarity on enterprise-shaped text" — is the gate that prevents shipping a model that looks fine in benchmarks and breaks in production.

### J.4 · Open sub-decisions for Architecture
- Specific model (e.g. `text-embedding-3-large` 3072 dim vs. `voyage-3-large` vs. local `bge-large-en-v1.5`).
- Self-hosted vs. hosted default.
- Whether to support a small "triage" embedder + a large "retrieval" embedder pair (a hypothesis only, not a decision).
- Embedding-model versioning and re-embedding strategy when the model is upgraded.

---

## K. Deployment Topology Notes (companion to brief §"Deployment topology" + Q-J)

Supports the brief's three-container baseline; Architecture to finalise the topology.

### K.1 · Baseline container set (brief-level commitment)
- **`nuron-api`** — Laravel REST API + auth. Internal network only.
- **`nuron-web`** — SvelteKit admin UI. Public-internal edge (customer's reverse proxy / ingress). Calls `nuron-api` over the internal network.
- **Data plane** — Neo4j, RabbitMQ (opt-in per Q-I), compiler workers, cache. Internal network only; not externally exposed.

### K.2 · Profile / composition model
- Mirror Cognee's Docker Compose profile model (`ui / mcp / postgres / neo4j`).
- A `nuron.yml` (or `docker-compose.yml`) with named profiles: `core` (api + web + Neo4j + compiler worker), `queue` (adds RabbitMQ), `observability` (adds Prometheus / OTEL collector — gated behind telemetry opt-in, see `brief.md` Pillar #4).

### K.3 · Volume layout
- Raw landing zone (raw Markdown files before compilation) — separate Docker volume, mounted read-write by the watcher, read-only by the compiler worker.
- Compiled document store — separate volume, mounted read-write by the compiler, read-only by the runtime query path.
- Neo4j data — its own volume per vendor guidance.
 Per-tenant data directory — v1 ships one tenant per deployment, but the data directory layout is per-tenant-shaped from day one (see Section D in this addendum's roadmap).
### K.4 · Network boundaries
- **`nuron-api`** is reachable only from `nuron-web` and from the customer's reverse proxy / API gateway.
- **`nuron-web`** is reachable from the customer's ingress.
- **Data plane** is reachable only from `nuron-api` and from compiler workers.
- No container in the data plane is ever exposed on a public-internal interface.

### K.5 · Open sub-decisions for Architecture
- Exact container image registry / build strategy (multi-stage Dockerfiles; image signing).
- Whether the compiler worker runs as a sidecar to the API container in `core` profile (single-process default) or as its own service (RabbitMQ profile).
- TLS termination strategy (Laravel / Caddy / customer-managed reverse proxy).
- Backup / restore procedure for the Neo4j volume and the landing-zone volume.

---

## L. Why OpenClaw / Agent-Platform Plugins Are Deferred (companion to brief §"Explicit non-goals")

Supports the brief's explicit non-goal on an OpenClaw plugin in v1.

### L.1 · The crowded landscape
- **Mem0** ships `integrations/openclaw/` directly in the main repo.
- **Cognee** ships `@cognee/cognee-openclaw` as a separate package.
- The agent-platform plugin war is in full swing in mid-2026.

### L.2 · Why Nuron does not ship an OpenClaw plugin in v1
- **Scope risk.** v1's spine is the LangGraph compiler + property graph + runtime query path. An OpenClaw plugin adds a fourth top-level surface area (plugin authoring, plugin marketplace / distribution, plugin lifecycle on the host).
- **Wrong seam.** OpenClaw plugins are typically thin wrappers that expose the host's tools. Nuron's value is the *graph + lineage*, not the tool surface — the OpenClaw plugin would be a one-screen dialog for "ask Nuron a question" with most of the product value behind it.
- **Wrong priority.** LangGraph agents (Nuron's own compiler) first, MCP second; OpenClaw later if user demand materialises. Two integration formats is plenty for v1.

### L.3 · When to revisit
- After v1 ships and we have evidence that real customers are running Nuron *alongside* OpenClaw installations.
- After MCP integration has stabilised and we know what the host-tool surface looks like in practice.
- When the OpenClaw plugin specification has stabilised (it has been moving; a v1 plugin shipped now would likely need rewrites).
