---
title: "Nuron — Product Brief"
status: draft
created: 2026-06-19
updated: 2026-07-07
project: Nuron
author: Basuruk
facilitator: bmad-product-brief
---

# Product Brief: Nuron

## Executive Summary

Nuron is a **self-hosted company brain** — an agentic system that ingests the scattered textual record of how an organisation actually works (notes, decisions, conversations, process documents) and turns it into a continuously-evolving graph of decisions, their lineage, and the evidence behind them. Its distinguishing claim is not better RAG. It is **institutional continuity**: when a person who carried twenty years of decisions in their head leaves the office, the next person can start where they stopped, not from zero.

The v1 product is a single-tenant, self-hosted application that exposes an API + auth surface, with a **Svelte** presentation layer (built on a ShadCN-style primitives library — see "Frontend Stack Decision" below) as one possible client. The system ingests Markdown files in v1 and produces a queryable Graph RAG over a property graph (Neo4j), fronted by a LangGraph-compiled default agent that listens for events and takes one action per request — ingest raw content into the compiled LLM-Wiki form, or reply from the graph. Customers can spawn additional agents from templates we ship; freeform agent authoring is explicitly out of v1 scope.

A managed, multi-tenant Nuron-as-a-service is a v2 possibility. v1 includes the architectural primitives (tenant scoping, isolated data directories) so v2 does not require a rewrite.

## The Problem

> Assume I am a functional specialist working in a software development firm. I am well trained with over 20 years of experience. And for those 20 years, everything I've learned, I have recorded in text books, papers, or emails. I have enough content to teach someone of everything I have done, the processes, the business flows, the tests I perform in the software product. But they are scattered in my notes and contents.
>
> And if I were to teach someone, on the top of my brain I know what decisions got made, what got changed over the years. I can tell them the whole journey. But what if I leave the office? Can another person go through all my 20 years worth of content and explain or teach someone new? Or learn by themselves?

That gap is what Nuron exists to close. Modern companies do try to close it — Confluence pages, Obsidian vaults, Notion workspaces, decision logs in Jira. The tools exist; the *connection* doesn't. One specialist's notes never meet another specialist's notes unless they both operate in a common documented place. And even when they do, the record is frozen: a Confluence page from 2022 doesn't tell you that the decision it documents was superseded in 2024 by a different one made in a different tool by a different person.

**Mental model.** Enterprise knowledge does not live in one place — it lives across **domains** (products, processes, business functions), and within each domain across **flows** (the way work actually moves), **people** (the institutional memory in their heads), and **systems** (the tools each flow touches — Confluence, Jira, forums, wikis, inboxes). Information is scattered across all three axes, not just across tools.

```text
     Domain A                          Domain B
    ┌─────────┐                       ┌─────────┐
    │ People  │                       │ People  │
    │ Systems │                       │ Systems │
    └────┬────┘                       └────┬────┘
         │ flows                            │ flows
         ▼                                  ▼
              ┌──────────────────┐
              │     Scattered    │   <-- knowledge has to be
              │  across domains, │       re-learned across
              │  flows, people,  │       sessions, tools,
              │  and systems     │       and people
              └────────┬─────────┘
                       ▼
         Knowledge is fragmented by structure,
         not just by content volume.
```

**The cost of this gap is not "we can't search."** It is **decision loss**, and it has three visible symptoms:

- **People touch non-specified areas just to learn.** When a new hire (or anyone moving between products / processes) has to come up to speed, they don't learn only their domain — they constantly need to touch *adjacent, often undocumented* areas to make sense of the work in front of them. The fragmentation is felt as time spent, not as missing data.
- **Cross-functional knowledge lives in people, not in systems.** Decisions and context that span domains (e.g. "why does the auth model handle refresh tokens this way?") are usually held by individuals who have been around long enough to know. When those individuals change roles or leave, the knowledge leaves with them unless it was deliberately written down *and* cross-linked.
- **Operational and people-handling knowledge is treated as out of scope.** Enterprise knowledge does not stop at products and services. It includes operational knowledge (how the company actually runs: release processes, on-call rotations, vendor relationships) and *people-handling* knowledge (how decisions about teams, hires, and reorganisations were reached and evolved). Most enterprise "knowledge" tools treat these as out of scope, which is why the brain never sees them.

Every time a person leaves, every time a tool is replaced, every time a project ends without its knowledge being threaded into the next one, the organisation pays a tax in re-discovery, in repeated mistakes, in slow onboarding, and in decisions being made without the evidence that already exists somewhere in the company's own history. At scale — hundreds of emails, thousands of Confluence pages, thousands of changes per week — the tax becomes structural.

## The Solution

Nuron runs *inside* the customer organisation as a self-hosted service. It does three things, repeatedly:

1. **Ingest** raw text — v1: Markdown files dropped into a configured location; v1.1: connectors to Confluence, Jira, internal forums. The raw content goes to a landing zone untouched.
2. **Structure** the raw content via a structuring agent (LLM) that normalises every ingested Markdown file into the fixed **Raw Ingest Agreement** schema (Content Header, Content, Key Discoveries, Tags — see Q-B). This middle-man stage runs on ingest and produces consistently-shaped input for the compiler; the Tags block feeds BM25 / keyword retrieval downstream.
3. **Compile** the structured content via a LangGraph agent that runs asynchronously. The compiler reads the noise (signatures, ticket transitions, duplicated threads) and emits a dense, standardised **LLM-Wiki** Markdown document — Executive Summary, Core Entities & Relationships, Known Issues & Verified Solutions, Cross-References. The compiled document is the only thing the brain ever sees. **All stages are event-driven on RabbitMQ (pub/sub) — see Q-I**; this does not change the ingest → structure → compile → graph → query contract.
3. **Persist** the compiled document into a property graph (LlamaIndex `PropertyGraphIndex` over Neo4j) where entities, relationships, decisions, and decision-lineage edges become first-class nodes. A separate curator agent rewrites a curated subgraph from the latest evidence on a schedule — touching only the branches that have changed since the last pass — so the graph stays current without unbounded write amplification.

The runtime query path is a separate, fast synchronous agent: a question arrives, the graph is queried with hybrid retrieval (vector + structural), the response is grounded in the decision graph and returned over the API or via SSE.

The default agent is Nuron-maintained and ships with the product. Customers can spawn additional agents from templates we ship — for example, an "Onboarding Q&A" agent that scopes its answers to a subset of the graph, or a "Decision lineage reporter" agent that emits supersession chains on demand. **Customers cannot author agents freely in v1** — that is a deliberate boundary.

A minimal **Svelte** presentation layer (ShadCN-style primitives — see "Frontend Stack Decision" below) ships with v1 so an admin can connect sources, view the graph, and configure agents without building a frontend. Customers are explicitly invited to replace it: Nuron's contract is its API and auth surface, not its UI.

The compiler treats the corpus uniformly — product docs, design docs, **testing flows**, operational runbooks, even forum threads where decisions were hashed out — and emits the same LLM-Wiki form. The brain does not distinguish "product knowledge" from "operational knowledge" or "people-handling knowledge" at the data-model level; it distinguishes *raw vs compiled* and *with vs without decision lineage*. This is deliberate: a tool that only ingests product docs will silently exclude the operational and people-handling knowledge that is most at risk when a long-tenured person leaves.

## What Makes This Different

Four pillars, in order of how much they matter. Pillars #2–#4 are sharpened by competitor research captured in the addendum (Section H: Mem0 / Cognee / Graphiti).

1. **Continuity across human turnover.** This is the moat. Nuron is built around the assumption that the people feeding it will leave, change teams, or stop paying attention, and that the system must still answer questions accurately years later. That assumption shapes the data model (decisions and their lineage are first-class; raw documents are second-class), the evolution strategy (append-mostly with explicit contradiction handling), and the audit posture (every response is traceable to a node, every node is traceable to evidence). It also shapes the *scope* of what counts as knowledge: product, operational, and people-handling knowledge are all in scope, because that is what is lost when a long-tenured person leaves.

2. **Decision lineage, not generic RAG — modelled at write time.** Most enterprise "knowledge graph" products store facts and entities. Nuron stores **decisions with explicit supersession chains**. The question the system is built to answer is not "what does the company know about X?" but "what did the company decide about X, when, on what evidence, and how has that decision evolved?" Critically: supersession is modelled **at write time, not just at retrieval time**. Every decision node carries an explicit supersession edge to its predecessor; every derived fact traces back to a raw episode (the "evidence" source); contradictions between new evidence and prior decisions are reconciled as a first-class outcome of the curator pass, not silently averaged away. This is the explicit differentiator vs Mem0 (whose temporal reasoning is retrieval-time ranking, not graph invalidation) and Cognee (which has no explicit decision entity). It is the closest to Graphiti's bi-temporal `valid_at` / `invalid_at` model, but lifted into the *decision* semantic rather than the fact semantic. See addendum §H for the per-competitor analysis.

3. **Symbiotic evolution — with an external async pipeline as a real durability story.** The system is designed to get *more accurate over time* as more of the organisation uses it. New evidence updates or contradicts prior nodes; curator passes rewrite only the touched subtrees (Merkle-style indexing hypothesis, see addendum §B and §I); confidence compounds. This is what makes the word "brain" earn its place — a static knowledge base is not a brain. **None of Mem0 / Cognee / Graphiti run an external broker** for their async pipeline; all three are in-process (Mem0 OSS is fully synchronous, Cognee uses `run_in_background` async tasks, Graphiti uses per-`group_id` `asyncio.Queue`s that are lost on restart). Nuron's RabbitMQ-driven compiler pipeline is therefore a real durability and scaling story — the compiler survives a process restart without losing in-flight ingestion, and the queue decouples source-system load spikes from runtime query latency. v1 adopts RabbitMQ as the **default** async backbone across all pipeline stages (pub/sub model — see **Q-I**), so this durability story is the baseline, not an opt-in.

4. **Open-core, no telemetry by default.** Competitor research surfaced that Graphiti ships with PostHog telemetry on by default (opt-out via env var), and both Mem0 and Cognee include analytics hooks in their self-hosted server images. Enterprise procurement will flag all three. Nuron ships with **no telemetry by default** — no usage analytics, no error reporting, no model-call traces leaving the customer's network — and the only outbound network traffic is whatever the customer explicitly configures (e.g. their chosen LLM provider, embedding provider). This is a cheap, durable claim and a procurement-friendly differentiator. It also matches the brief's "self-hosted, customer-controlled" posture.

### Recommended embedding model floor

Decision-lineage retrieval depends on entity / decision similarity working well enough that supersession candidates actually surface. The brief therefore commits to a **recommended embedding model floor** that the Architecture phase will pin:

- **Minimum:** a general-purpose embedding model in the ≥ 600M-parameter class with ≥ 1024-dim output (the working consensus across Mem0's "≥ Qwen 600M" guidance and Graphiti's `EMBEDDING_DIM=1024` default).
- **Default for v1:** an OpenAI-class hosted embedding model (e.g. `text-embedding-3-large`, 3072 dim), with the option for a local model on the customer's hardware when self-hosted LLMs are also in use.
- **Quality bar:** the embedding model must produce semantically meaningful similarity on enterprise-shaped text (long-form, mixed register, decision-style prose), not just short factual sentences. We will not ship an embedding model that fails this bar on a representative seed corpus.

The exact embedding model, dimensions, and self-hosted-vs-hosted default are Architecture decisions; this section commits only to the floor.

### What is *not* a moat

We are honest about what is not a moat: the embeddings, the LLM choice, the Graph RAG technique, the storage backend. Those are all commodity by the time v1 ships. The moat is the data model (decision-as-first-class, lineage at write time) and the operational discipline of running it well on a self-hosted stack.

## Who This Serves

**Primary users (v1):**

- **The functional specialist with twenty years of scattered notes.** They are not the person asking the questions — they are the person feeding the brain. They want to dump raw content into a folder and trust that the system will surface what they once knew, when someone else needs it, without further effort on their part.
- **The new hire or successor.** They are the person asking the questions. They want to ask "how did we decide on the auth model?" and get an answer that names the decision, the date, the people involved, the evidence cited, and the subsequent superseding decisions.
- **The admin / platform owner inside the customer company.** They deploy Nuron, manage users, configure which sources are ingested, decide which agents from the Nuron template library are enabled, and watch the graph. In v1 they are an ops engineer; in v2 they may be a managed-service customer of ours.

**Secondary users (v1):**

- Other agents (internal automation) consuming the Nuron API to retrieve grounded context for their own workflows.

**Out of scope (v1):**

- End-consumer / public-facing deployments.
- Multi-tenant managed service — v2.

## Success Criteria

How we know Nuron v1 is working:

- **[ASSUMPTION]** **Decision lineage is answerable.** For any decision in the graph, an API call returns the decision, its author, its timestamp, the evidence cited, and its chain of supersession to the present. End-to-end test must pass on the demo seed dataset.
- **The pipeline is proven end to end.** Drop a folder of Markdown files into the configured location; the compiler picks them up, the graph is updated, a query returns a grounded response with citations. No human intervention required between ingest and query.
- **[ASSUMPTION] The curator pass works on touched subtrees only.** A change to one branch does not cause a full re-curation. Performance budget: **[ASSUMPTION]** a single-pass re-curation of a 10k-node graph with 1% of subtrees touched completes in under 10 minutes on modest hardware.
- **The default agent handles ingest and reply correctly under load.** A benchmark message stream of mixed ingest + reply requests is processed without loss or duplication, with one action per request.
- **A user-configured agent created from a Nuron template runs in the customer's environment without engineering involvement from us.** Configuration is admin UI + REST, no code changes required.

A non-quantitative success signal we will watch for in the first design-partner deployment: people who have been at the company for over a year **return to Nuron voluntarily**, not because they were told to. That is the continuity claim's first real test.

## Scope

### In v1

- Self-hosted, single-tenant deployment. Docker / compose stack; runs on whatever infra the customer has.
- Built-in user store with admin-provisioned accounts. Admin role + standard user role.
- Markdown file ingestion from a configured directory. Recursive scan, configurable schedule.
- LangGraph compiler that emits LLM-Wiki Markdown from raw input.
- LlamaIndex `PropertyGraphIndex` over Neo4j. Hybrid retrieval (vector + structural).
- One Nuron-maintained default agent that handles ingest and reply actions. Asynchronous, one action per request; all actions are dispatched as RabbitMQ pub/sub events (RabbitMQ is the default async backbone — see Q-I).
- User-configured agents created from Nuron-supplied templates. Admin UI to enable / disable / scope templates.
- Curator agent that re-curates only touched subtrees.
- Svelte presentation layer (Bits UI or shadcn-svelte — see "Frontend Stack Decision") with the minimum functionality: source setup, agent setup, MCP-style connection configuration, graph view, query UI.
- REST API + SSE streaming responses. API-first; frontend is replaceable.
- Audit log: every response traceable to a node, every node traceable to evidence.

### Explicit non-goals (v1)

- **Freeform agent authoring / customer-built LangGraph graphs.** Agents come from Nuron-supplied templates only.
- **Source connectors** beyond Markdown file ingestion. Confluence / Jira / Forum are v1.1.
- **OIDC / SAML / Entra SSO.** Built-in user store only in v1.
- **Multi-tenant managed Nuron-as-a-service.** v2.
- **Non-textual content** (images, diagrams, video, audio transcripts). Text only in v1.
- **Tenant management UI / billing / metering.** v2.
- **OpenClaw / agent-platform plugin in v1.** Both Mem0 and Cognee ship OpenClaw plugins; the agent-platform plugin layer is now crowded. Nuron's v1 priorities are the LangGraph compiler first, MCP second; an OpenClaw plugin only ships if user demand materialises post-v1.

### Boundary clarifications

- **Frontend is not the product.** Svelte + a ShadCN-style primitives library (Bits UI or shadcn-svelte — see "Frontend Stack Decision" below) is shipped as a convenience and a reference implementation. The contract is the API and auth surface.
- **The pipeline is the spine, Markdown is the proof.** v1 proves the ingest → compile → graph → query loop. The product value (the company brain) emerges fully when v1.1 connectors feed real source systems. The brief treats these as separable: the pipeline can ship and be validated against Markdown seed data before any connector work is needed.

## Vision

If Nuron succeeds in the way we hope, two things become true in 2–3 years:

1. **Departures stop being catastrophic.** When a long-tenured specialist leaves a customer company, the new person in the role opens Nuron, asks the questions they need to ask, and gets answers grounded in the prior person's actual decisions — with lineage, with evidence, with the full A → B → C chain of how things got the way they are. The knowledge doesn't leave with the person.
2. **The brain compounds.** Every employee who joins and uses Nuron adds evidence to the graph. Every decision they make and document adds lineage. The system becomes more accurate, more useful, and harder to replace the longer it runs. Customers who have run Nuron for two years will not switch off it; the switching cost is the graph itself.

The v2 business possibility — a managed multi-tenant Nuron we operate — exists for companies that don't want to run the infrastructure themselves. The architectural primitives (tenant scoping, isolated data directories) ship in v1 so that v2 doesn't require a rewrite. The pricing and platform choice (GCP vs Azure vs other) for v2 is **Open** and is explicitly not committed in this brief.

---

## Frontend Stack Decision

> **Stack split:** **Laravel** is the backend (REST API + auth surface). **Svelte** is the frontend (reference admin UI). This section is about the **frontend** side only — Laravel is not in scope for replacement.

### Deployment topology (brief-level)

v1 ships the API and the Web as **separate containers** in the self-hosted Docker / compose stack. At minimum:

- **`nuron-api`** — Laravel REST API + auth surface. Reachable only on an internal Docker network.
- **`nuron-web`** — SvelteKit frontend (admin UI). Reachable on the public-internal edge (the customer's reverse proxy / ingress). Calls `nuron-api` over the internal network.
- **Data plane** — Neo4j, RabbitMQ (required async backbone, pub/sub — see Q-I), compiler workers, any cache. Internal network only; not externally exposed.

This shape (a) keeps Laravel purely on the backend side, (b) lets the frontend be replaced wholesale without touching the API container, and (c) gives an ops-portable baseline that mirrors the Docker Compose profile model used by Cognee (`ui / mcp / postgres / neo4j` profiles). The exact container set, profiles, network boundaries, and volume layout are an Architecture decision — see **Q-J**.

### Styling posture

Svelte's built-in scoped CSS is the **default styling layer**. Tailwind CSS is an opt-in, and is *required* only if the primitives choice lands on shadcn-svelte. This is not a separate decision — it follows from Q-F.

The v1 frontend was originally committed as **Laravel + ShadCN** (with ShadCN's React components served out of the Laravel stack). That pairing is being replaced because ShadCN is a React ecosystem; for the frontend in particular, the brief is moving to **Svelte**, with the ShadCN-style primitives layer still **Open** between two Svelte-ecosystem candidates: **Bits UI** and **shadcn-svelte**. This section captures the comparison at brief-finalize time so downstream phases (PRD, Architecture) can make a binding choice with full context.

### What stays, what changes

| Layer | v1 (this brief) | Notes |
|---|---|---|
| Backend / API | **Laravel** | Unchanged. REST API + auth surface per the brief. |
| Frontend framework | **Svelte** (SvelteKit) | Replaces Laravel-served React + Blade templates. |
| Frontend primitives | **Open** — Bits UI *or* shadcn-svelte | This is **Q-F**. Lock in PRD / Architecture. |
| Frontend styling | Svelte's built-in scoped CSS (default) or Tailwind CSS (opt-in) | **Not a top-level decision** — follows the primitives choice. Required by shadcn-svelte; *optional* with Bits UI (you bring the styles). |

### Why Svelte for the frontend

The previous choice was Laravel serving ShadCN's React components — that conflates the backend framework with the frontend framework in a way the brief now wants to clean up. The reasons to switch the *frontend* to Svelte:

- **Clean API-first seam.** The brief already commits that the contract is the API and auth surface, not the UI. SvelteKit keeps the frontend as a thin client over the Laravel REST API; Laravel stays purely on the backend side. No more mixed Laravel + React templates in one app.
- **Component portability.** Svelte components are closer to "annotated HTML" than to a framework abstraction. Customers who want to replace the reference UI can lift individual Svelte components wholesale.
- **Build / ship ergonomics.** SvelteKit produces a static or SSR SPA with first-class server endpoints. The frontend no longer needs Laravel as a hosting surface; it can deploy to any static host or Node runtime.
- **Lower concept count.** Svelte 5 runes + a primitives library is a smaller surface area than Laravel + Blade + Inertia + a React-style component model — and it keeps Laravel where it belongs (the backend).

### Candidates under comparison

Both candidates are Svelte-native and aim to give us the same outcome as the original Laravel + ShadCN choice — accessible, themeable, copy-paste-able component primitives — but they differ significantly in *how much they own*, and they differ on the styling question: **shadcn-svelte requires Tailwind CSS**; **Bits UI does not** (it's headless; you bring the styles or use Svelte's built-in scoped CSS). The styling choice therefore follows the primitives choice, not the other way around.

| Dimension | Bits UI | shadcn-svelte |
|---|---|---|
| **What it is** | Headless primitives library (the Svelte port of Radix Bits). Ships unstyled, fully accessible component logic. | Styled component library that *is* the Svelte port of ShadCN. Ships Tailwind-styled components, copied into your repo. |
| **Style ownership** | You bring the styles (Tailwind, Skeleton, plain CSS). Bits UI owns behavior, a11y, keyboard nav, focus management. | shadcn-svelte owns styles + behavior; you customize via `tailwind.config` and `components.json`. |
| **A11y posture** | First-class (it's the entire reason Bits UI exists). ARIA, focus traps, roving tabindex, etc., are tested. | Good, but inherits whatever Bits UI / Melt UI it composes from under the hood. |
| **Theming** | Bring your own design system. Maximum flexibility, maximum work. | ShadCN theming model out of the box (CSS variables, `hsl(var(--...))`). Fastest path to a coherent look. |
| **Bundle / vendor lock-in** | Tiny runtime. No copy-pasted components in your repo — you depend on the package. | Components are copied into your repo. You own them, you maintain them, you can rewrite any of them. |
| **Release cadence / maturity** | Younger in the Svelte ecosystem. Tracks Bits (Radix) closely. | Mature, large community, well-documented, active. |
| **Fit with "frontend is not the product"** | Lower switching cost to a different primitives lib later (you control styling). | Higher up-front velocity, but every styled component is in your repo to migrate if you change direction. |

### Recommendation at brief-finalize time

**Default recommendation: Bits UI** for v1, because:

1. **Dependency-count matches the brief's posture.** The brief already commits that the frontend is *not* the product and is replaceable. Bits UI is a small headless package; styling is left to Svelte's built-in scoped CSS or to a Tailwind opt-in. shadcn-svelte requires Tailwind and copies styled components into the repo — both add surface area that has to be justified against an admin UI customers are expected to replace.
2. **First-party a11y is the entire reason Bits UI exists.** ARIA, focus traps, keyboard nav, roving tabindex are tested at the library level — we inherit them without doing the work ourselves. The admin UI is reference, not branded; we want the boring correct outcome.
3. **Svelte 5 idioms over React conventions.** Bits UI is built on Melt UI / Bits (Radix) patterns ported to Svelte; it stays close to Svelte 5 runes. shadcn-svelte is a Svelte *port* of a React component line, which occasionally shows.
4. **Lower switching cost if we revisit.** Switching primitives later is harder when styled components have been copied into the repo. With Bits UI we own the styling layer, so swapping in a different primitives library later is a library change, not a migration of in-repo styled components.

**When to revisit toward shadcn-svelte instead:**

- If visual velocity (a coherent default look with zero styling work) becomes more important than dependency count — e.g. a design partner wants the admin UI to look polished on day 1 without bespoke styling.
- If the team has strong prior ShadCN muscle memory and treats the port as a free win.
- If we conclude that the admin UI will live longer than expected and the styling customisation tax becomes recurring.

### Decision (Q-F resolved at brief level)

**Bits UI is locked for v1.** The brief's brief-finalize default is adopted as the decision; the PRD / Architecture phases carry it forward as a locked choice. shadcn-svelte remains the documented fallback if visual velocity later outweighs dependency-count posture (see comparison above). The frontend epic may now be broken down without waiting on this question.

---

## Open Questions

These were unresolved at brief-finalize time. **Q-B, Q-C, Q-D, and Q-F have been resolved at the brief level** (see "Resolved at Brief Level" below) and are carried into the PRD as locked decisions. The remaining items are explicitly deferred to the Architecture phase (`[CA]`) or v2 planning and must not be locked before then.

### Resolved at brief level

- **Q-B · Source coverage in v1 demo / first deployment — RESOLVED.** v1 ships with **seed Markdown content only** (a thin Markdown-export path from one real source is out of v1 scope, lands in v1.1 per addendum §C). But ingestion is no longer "raw file → compiler." v1 introduces a **structuring middle-man** between raw ingest and the LLM-Wiki compiler: every ingested Markdown file is first normalised into a fixed **Raw Ingest Agreement** structure by an agent (LLM) before it reaches the compiler. This gives the downstream graph RAG extra, consistently-shaped signal to draw from — most importantly a **Tags** block (areas the raw doc touches: development, testing, user onboarding, etc.) that enables cheap **BM25 / keyword retrieval** alongside vector + structural hybrid search. The Raw Ingest Agreement v1 schema is:

  ```markdown
  ## Raw Ingest Agreement v1
  ## Content Header
  - Subject:
  - Reason:
  - Date & Time:
  ## Content
  - Raw content extracted with details pre-processed by an AGENT (LLM model).
  ## Key Discoveries
  ## Tags (areas the raw doc touches: e.g. development, testing, user onboarding)
  ```

  Rationale: the brief already rejects source-connector-first sequencing (addendum §A.5) — prove the brain against curated input, then expand what it listens to. The structuring stage operates on that curated Markdown and is backend-agnostic, so it survives the v1.1 connector expansion unchanged. The exact field set / validation is pinned in Architecture (see addendum §E "LLM-Wiki schema finalisation" — now extended to cover the Raw Ingest Agreement schema).
- **Q-C · Default agent reply channel — RESOLVED.** In v1 the default agent **returns replies via the API / SSE only**; it does **not** post back to the originating source (no write loop). Write-back is deferred to v1.1 and is gated on the forum connector (addendum §C.2), which is where the auth-model and data-flow implications actually bite. This keeps the v1 auth model and data-flow diagram source-agnostic.
- **Q-D · Data retention, GDPR, right-to-be-forgotten — RESOLVED (posture).** v1 ships with **per-tenant data isolation** as the foundation (per addendum §D: tenant ID on every record, per-tenant data directory, tenant-scoped auth and audit). On top of that, v1 includes a **right-to-be-forgotten primitive**: deletion is scoped by tenant/workspace and removes the data directory, all graph nodes/edges, queue messages, and audit entries for that tenant. **Retention policy is per-tenant and configurable**; default retention is indefinite until a deletion is requested. No real customer PII is processed until this primitive is in place. (Exact retention defaults and any jurisdiction-specific handling are pinned in Architecture / PRD acceptance criteria.)
- **Q-F · Frontend primitive library — RESOLVED.** **Bits UI is locked for v1.** The brief's brief-finalize default (Bits UI) is adopted as the decision. Rationale: headless primitives keep the styling layer owned by us, match the "frontend is not the product / replaceable" posture, and give first-party a11y with the smallest dependency surface. shadcn-svelte remains the documented fallback if visual velocity becomes the priority (see "Frontend Stack Decision"). Styling therefore uses Svelte's built-in scoped CSS by default; Tailwind is an opt-in that only becomes relevant if a future decision flips the primitives choice.

### Still open (deferred)

- **Q-A · Cloud platform for v2 managed service.** GCP and Azure have both been mentioned. Pricing is volatile; architectural primitives matter more than platform choice for v1. Resolve during v2 planning, not v1.
- **Q-E · Merkle-style subtree indexing feasibility.** Hypothesis from this brief's decision-log. Validate or invalidate during Architecture.
- **Q-G · Managed-wrapper / single-vendor risk (Graphiti → Zep, Cognee → Cognee Cloud, Mem0 → Mem0 Platform).** Decide in Architecture whether the risk warrants self-hosting dependencies or wrapping instead.
- **Q-H · Tenant scoping key(s) for v1.** Confirm in Architecture whether v1 needs more than a single `workspace` (or `team`) scoping key, given v1 is single-tenant per deployment.
- **Q-I · Async pipeline default mode — in-process vs RabbitMQ — RESOLVED.** v1 ships **RabbitMQ as the default** async backbone. All pipeline events (ingest, compile, curate, reply) flow through a **pub/sub model** on RabbitMQ; there is no in-process-only default path. Rationale: a pub/sub broker makes every stage durable and restart-safe (in-flight ingestion survives a process restart, unlike the in-process `asyncio.Queue`s used by Graphiti — see §"What Makes This Different" pillar #3), and decouples source-system load spikes from runtime query latency. The earlier "ops tax, make in-process default" caveat is overridden by the decision that v1 is event-driven by design. The in-process path is dropped from v1 scope; RabbitMQ is a required data-plane component (see Deployment Topology and Q-J).
  - **Broker self-hosting — CONFIRMED acceptable.** RabbitMQ is **self-hosted inside the customer's deployment** (a container in the Docker / compose stack, internal network only — see Deployment Topology). This is consistent with the brief's "self-hosted, customer-controlled, no telemetry by default" posture (pillar #4): the broker ships with the stack, carries no usage analytics, and emits no outbound traffic except to the customer's own configured LLM / embedding providers. No managed/RabbitMQ-as-a-service dependency is introduced; the broker is an ops component the customer already owns the lifecycle for, not a new vendor relationship. (Operational specifics — HA/quorum queues, durability/persistent messages, resource sizing, and failure-mode behaviour — are pinned in Architecture; see Q-J and addendum §E "RabbitMQ topology".)
- **Q-J · Container / compose topology.** Confirm the full topology in Architecture, including the RabbitMQ placement (required data-plane container, internal-only network) and exchange/queue/routing-key layout for the pub/sub pipeline.

## Next Steps

The brief feeds the BMM required pipeline:

1. **[PRD]** `bmad-prd` — convert this brief into acceptance-criteria-grade requirements.
2. **[CA]** `bmad-create-architecture` — formalise the LangGraph compiler / LlamaIndex Property Graph / Neo4j / RabbitMQ / Merkle-curation decisions as architectural decision records. This is where the open questions above get answered or formally parked.
3. **[CU]** `bmad-ux` — design the Svelte admin UI's MVP surface (Laravel stays the API backend).
4. **[CE]** `bmad-create-epics-and-stories` — break the system into buildable units.
5. **[IR]** `bmad-check-implementation-readiness` — final alignment gate before sprint planning.

The addendum holds rejected alternatives, the Merkle-curation hypothesis in detail, source-connector roadmap, multi-tenant primitives roadmap, and any technical constraints that don't belong in the brief.
