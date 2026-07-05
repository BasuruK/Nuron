---
title: "Nuron — Product Brief"
status: draft
created: 2026-06-19
updated: 2026-07-05
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

The cost of this gap is not "we can't search." It is **decision loss**: every time a person leaves, every time a tool is replaced, every time a project ends without its knowledge being threaded into the next one, the organisation pays a tax in re-discovery, in repeated mistakes, in slow onboarding, and in decisions being made without the evidence that already exists somewhere in the company's own history. At scale — hundreds of emails, thousands of Confluence pages, thousands of changes per week — the tax becomes structural.

## The Solution

Nuron runs *inside* the customer organisation as a self-hosted service. It does three things, repeatedly:

1. **Ingest** raw text — v1: Markdown files dropped into a configured location; v1.1: connectors to Confluence, Jira, internal forums. The raw content goes to a landing zone untouched.
2. **Compile** the raw content via a LangGraph agent that runs asynchronously off RabbitMQ. The compiler reads the noise (signatures, ticket transitions, duplicated threads) and emits a dense, standardised **LLM-Wiki** Markdown document — Executive Summary, Core Entities & Relationships, Known Issues & Verified Solutions, Cross-References. The compiled document is the only thing the brain ever sees.
3. **Persist** the compiled document into a property graph (LlamaIndex `PropertyGraphIndex` over Neo4j) where entities, relationships, decisions, and decision-lineage edges become first-class nodes. A separate curator agent rewrites a curated subgraph from the latest evidence on a schedule — touching only the branches that have changed since the last pass — so the graph stays current without unbounded write amplification.

The runtime query path is a separate, fast synchronous agent: a question arrives, the graph is queried with hybrid retrieval (vector + structural), the response is grounded in the decision graph and returned over the API or via SSE.

The default agent is Nuron-maintained and ships with the product. Customers can spawn additional agents from templates we ship — for example, an "Onboarding Q&A" agent that scopes its answers to a subset of the graph, or a "Decision lineage reporter" agent that emits supersession chains on demand. **Customers cannot author agents freely in v1** — that is a deliberate boundary.

A minimal **Svelte** presentation layer (ShadCN-style primitives — see "Frontend Stack Decision" below) ships with v1 so an admin can connect sources, view the graph, and configure agents without building a frontend. Customers are explicitly invited to replace it: Nuron's contract is its API and auth surface, not its UI.

## What Makes This Different

Three pillars, in order of how much they matter:

1. **Continuity across human turnover.** This is the moat. Nuron is built around the assumption that the people feeding it will leave, change teams, or stop paying attention, and that the system must still answer questions accurately years later. That assumption shapes the data model (decisions and their lineage are first-class; raw documents are second-class), the evolution strategy (append-mostly with explicit contradiction handling), and the audit posture (every response is traceable to a node, every node is traceable to evidence).
2. **Decision lineage, not generic RAG.** Most enterprise "knowledge graph" products store facts and entities. Nuron stores **decisions with explicit supersession chains**. The question the system is built to answer is not "what does the company know about X?" but "what did the company decide about X, when, on what evidence, and how has that decision evolved?" That is a different graph, a different retrieval path, and a different product category.
3. **Symbiotic evolution.** The system is designed to get *more accurate over time* as more of the organisation uses it. New evidence updates or contradicts prior nodes; curator passes rewrite only the touched subtrees (Merkle-style indexing hypothesis, see addendum); confidence compounds. This is what makes the word "brain" earn its place — a static knowledge base is not a brain.

We are honest about what is *not* a moat: the embeddings, the LLM choice, the Graph RAG technique. Those are all commodity by the time v1 ships. The moat is the data model and the operational discipline of running it well.

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
- **The curator pass works on touched subtrees only.** A change to one branch does not cause a full re-curation. Performance budget: **[ASSUMPTION]** a single-pass re-curation of a 10k-node graph with 1% of subtrees touched completes in under 10 minutes on modest hardware.
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
- One Nuron-maintained default agent that handles ingest and reply actions. Asynchronous, RabbitMQ-driven, one action per request.
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

The v1 frontend was originally committed as **Laravel + ShadCN** (with ShadCN's React components served out of the Laravel stack). That pairing is being replaced because ShadCN is a React ecosystem; for the frontend in particular, the brief is moving to **Svelte**, with the ShadCN-style primitives layer still **Open** between two Svelte-ecosystem candidates: **Bits UI** and **shadcn-svelte**. This section captures the comparison at brief-finalize time so downstream phases (PRD, Architecture) can make a binding choice with full context.

### What stays, what changes

| Layer | v1 (this brief) | Notes |
|---|---|---|
| Backend / API | **Laravel** | Unchanged. REST API + auth surface per the brief. |
| Frontend framework | **Svelte** (SvelteKit) | Replaces Laravel-served React + Blade templates. |
| Frontend primitives | **Open** — Bits UI *or* shadcn-svelte | This is **Q-F**. Lock in PRD / Architecture. |
| Frontend styling | Tailwind CSS | Required by both Bits UI + shadcn-svelte. |

### Why Svelte for the frontend

The previous choice was Laravel serving ShadCN's React components — that conflates the backend framework with the frontend framework in a way the brief now wants to clean up. The reasons to switch the *frontend* to Svelte:

- **Clean API-first seam.** The brief already commits that the contract is the API and auth surface, not the UI. SvelteKit keeps the frontend as a thin client over the Laravel REST API; Laravel stays purely on the backend side. No more mixed Laravel + React templates in one app.
- **Component portability.** Svelte components are closer to "annotated HTML" than to a framework abstraction. Customers who want to replace the reference UI can lift individual Svelte components wholesale.
- **Build / ship ergonomics.** SvelteKit produces a static or SSR SPA with first-class server endpoints. The frontend no longer needs Laravel as a hosting surface; it can deploy to any static host or Node runtime.
- **Lower concept count.** Svelte 5 runes + a primitives library is a smaller surface area than Laravel + Blade + Inertia + a React-style component model — and it keeps Laravel where it belongs (the backend).

### Candidates under comparison

Both candidates are Svelte-native and aim to give us the same outcome as the original Laravel + ShadCN choice — accessible, themeable, copy-paste-able component primitives built on Tailwind CSS — but they differ significantly in *how much they own*.

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

**Default recommendation: shadcn-svelte** for v1, because:

1. It is the closest 1:1 swap for the previous Laravel + ShadCN *frontend* decision — same theming model, same "copy components into your repo" workflow, same Tailwind + CSS-variable conventions. The team can carry over ShadCN muscle memory without a context switch. The difference is the framework around it: Laravel stays on the backend, Svelte serves the UI.
2. v1 ships a *reference* admin UI. Velocity and visual coherence matter more than maximum styling flexibility at this stage. We are not building a customer-facing brand; we are building an internal tool that admins will tolerate.
3. shadcn-svelte composes on top of Bits UI / Melt UI under the hood, so the a11y posture is inherited.

**When to revisit toward Bits UI instead:**

- If the design language needs to diverge significantly from ShadCN (e.g. a bespoke customer brand on the admin UI).
- If component customization becomes a recurring tax — every styling tweak requires forking a copied component.
- If the team wants to standardize on a headless layer across multiple future frontends (admin UI + a possible v2 customer portal).

### Open question

See **Q-F** in the Open Questions section. The PRD / Architecture phases must lock the primitive-library choice (Bits UI vs shadcn-svelte) before the frontend epic is broken down.

---

## Open Questions

These are unresolved at brief-finalize time and must be resolved in downstream phases (PRD / Architecture) before they become locked decisions:

- **Q-A · Cloud platform for v2 managed service.** GCP and Azure have both been mentioned. Pricing is volatile; architectural primitives matter more than platform choice for v1. Resolve during v2 planning, not v1.
- **Q-B · Source coverage in v1 demo / first deployment.** v1 ingests Markdown files only. For the first design-partner deployment, confirm whether a thin Markdown-export path from one real source (Confluence export, forum archive) is in scope as part of v1, or whether v1 ships with seed Markdown content only and connectors land in v1.1.
- **Q-C · Default agent reply channel.** When the agent replies to a forum post, does it post back to the originating forum (write loop), or only return via the API? Affects the auth model and the data-flow diagram.
- **Q-D · Data retention, GDPR, right-to-be-forgotten posture.** Per-tenant policy in v1. Requires an explicit position before any real customer data lands.
- **Q-E · Merkle-style subtree indexing feasibility.** Hypothesis from this brief's decision-log. Validate or invalidate during Architecture.
- **Q-F · Frontend primitive library — Bits UI vs shadcn-svelte.** Svelte is locked for v1; the primitives layer (Bits UI vs shadcn-svelte) is still Open. Lock in PRD / Architecture before the frontend epic is broken down. See the "Frontend Stack Decision" section above for the full comparison and a provisional recommendation.

## Next Steps

The brief feeds the BMM required pipeline:

1. **[PRD]** `bmad-prd` — convert this brief into acceptance-criteria-grade requirements.
2. **[CA]** `bmad-create-architecture` — formalise the LangGraph compiler / LlamaIndex Property Graph / Neo4j / RabbitMQ / Merkle-curation decisions as architectural decision records. This is where the open questions above get answered or formally parked.
3. **[CU]** `bmad-ux` — design the Svelte admin UI's MVP surface (Laravel stays the API backend).
4. **[CE]** `bmad-create-epics-and-stories` — break the system into buildable units.
5. **[IR]** `bmad-check-implementation-readiness` — final alignment gate before sprint planning.

The addendum holds rejected alternatives, the Merkle-curation hypothesis in detail, source-connector roadmap, multi-tenant primitives roadmap, and any technical constraints that don't belong in the brief.
