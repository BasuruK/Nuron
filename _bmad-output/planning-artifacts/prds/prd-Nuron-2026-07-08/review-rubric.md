# PRD Quality Review — Nuron

## Overall verdict

This PRD successfully bridges the product brief into acceptance-criteria-grade requirements with a clear thesis (first-class Decisions with explicit Supersession edges as the core differentiator) and a coherent pipeline narrative (ingest → structure → compile → graph → query). The vision, user journeys, and feature descriptions are earned, not generic, and the pipeline design is well-motivated. However, three moderate issues could create downstream friction: (1) **scope gaps** on Markdown source format and Agent Template definitions, which are part of the product contract but deferred to Architecture; (2) **done-ness ambiguity** in several FRs where terms like "near-duplicate," "contradicts," and "matching Decision" lack precise definition; and (3) **incomplete cross-referencing** that would ease extraction for UX and story creation. These are fixable and do not block Architecture or UX work, but rework will be needed during story step-down if not clarified now.

## Decision-readiness — adequate

The PRD surfaces real trade-offs and deferred decisions clearly. The brief addendum (§A) explicitly rejects alternatives with reasoning: generic RAG (insufficient lineage), real-time-only ingestion (couples extraction to query quality), freeform agent authoring (scope risk), self-hosted with a control plane (privacy tax), and source-connector-first sequencing (de-risks pipeline first, connectors later). Sequencing decisions are supported: v1 proves the pipeline against seed Markdown (§7.1), v1.1 adds connectors (§5.5), v2 adds managed service (§5.5). Open Questions (§9) are numbered and genuine, not rhetorical — Q-H on tenant scoping keys, Q-E on Merkle-tree feasibility, and Q-J on RabbitMQ topology are all architectural unknowns waiting for resolution. `[NOTE FOR PM]` at §7.2 acknowledges write-back as "emotionally load-bearing" for the continuity narrative, naming the tension rather than dodging it. Counter-metrics (§8, SM-C1, SM-C2) explicitly forbid optimizations that would break the thesis: "do not optimize ingestion throughput by weakening validation" and "do not optimize query latency by dropping to vector-only retrieval." This is disciplined.

However, mechanics remain vague in places that will require clarification during Architecture. FR-14's tenant scoping is stated as "every persistent record can carry a tenant/workspace identifier," but the actual schema (is it a single string key? a hierarchical namespace? does it nest?) is not specified. FR-11's agent scoping ("scoped to a subset of the graph") is similarly abstract — is it a list of node IDs? a tag-based filter? a semantic boundary? These belong here, not in Architecture.

## Substance over theater — strong

Personas are load-bearing. Devika (the feeder, UJ-1) drives the ingest-and-normalise path (FR-1, FR-2, FR-3) and the edge case of conflict reconciliation (FR-6). Kian (the successor, UJ-2) drives the query and lineage path (FR-8, FR-5, FR-6 Supersession visibility). Priya (the admin, UJ-3) drives agent templating and scope configuration (FR-11, FR-13). Each persona has a specific entry state, path, climax, resolution, and edge case — not placeholder names. None are interchangeable; removing one would break a feature thread.

Vision is specific and tied to differentiation. "Decisions are first-class and carry explicit Supersession edges to what they replaced, modeled at write time" is explicitly contrasted against Mem0 (retrieval-time temporal ranking, no graph invalidation) and Cognee (no explicit decision entity). The vision also names the moat: "not the embeddings, the LLM choice, or the graph technique, all of which are commodity by the time v1 ships," focusing on the architecture (self-hosted, no telemetry, restart-safe pipeline) as the real differentiator.

NFRs are product-specific, not boilerplate. "No telemetry by default" is a pillar tied to self-hosted posture (§5.1). "Restart-safety" is tied to RabbitMQ pub/sub as a required component, not optional (FR-4). "API-first" is a constraint with consequences: every configuration action in `nuron-web` must have a REST equivalent (FR-13), and `nuron-web` is replaceable, not the product itself. These are not generic "reliable, scalable, secure" NFRs; they're design decisions with enforcement.

Counter-metrics (§8) show discipline: SM-C1 and SM-C2 explicitly say what *not* to optimize toward, preventing a fast-but-wrong direction. This is the opposite of theater.

One soft spot: Agent Templates are described as "Nuron-supplied, admin-configurable agent definition (e.g. Onboarding Q&A, Decision Lineage Reporter)" but the template definition itself is not detailed. UJ-3 shows *how an admin uses them*, but not *what they are*. Is a template a JSON schema? A Python class? A YAML configuration? The PRD doesn't say, and this could be considered theater because it's named as a first-class concept but left abstract. However, this level of abstraction may be appropriate for a PRD whose downstream is Architecture; the template *surface* (how admins interact with it) is documented; the template *definition* (how it's authored or deployed) is deferred.

## Strategic coherence — strong

The thesis is: organizations have decision-making knowledge scattered across people and systems. Nuron captures it in a first-class Decision graph with explicit Supersession edges so the next person doesn't start from zero.

Features serve this thesis coherently:
- FR-1, FR-2, FR-3 → ingest and compile structured knowledge from raw input.
- FR-5, FR-6 → store Decisions and their lineage in the graph (core thesis).
- FR-7 → keep the graph curated efficiently as it grows (scalability).
- FR-8, FR-9 → query and ground answers in that knowledge (usability).
- FR-10, FR-11 → agents handle the read/respond/ingest loop (automation).
- FR-12 to FR-17 → governance and auditability (production readiness).

There is no feature that does not serve one of these threads. No "random capability we added because it seemed cool." MVP Scope (§7.1) is coherent: prove the *full pipeline* end-to-end (ingest → compile → graph → query), not a partial slice like "query only" or "ingest only."

Success Metrics validate the thesis:
- SM-1 (Decision lineage answerable) validates the Supersession core.
- SM-2 (Pipeline proven end-to-end) validates the full flow with no human intervention.
- SM-3, SM-4 (agents handle load, template agents run without Nuron engineering) validate the automation layer.

Non-Goals are strategic: no freeform agents (keeps Nuron focused on templated patterns, not a general-purpose platform), no connectors yet (sequence: prove the brain first, then expand what feeds it), no SSO (admin-provisioned simplifies v1), no multi-tenant service (that's v2). These are not "everything that didn't fit," they're design choices that narrow scope to deliver the thesis.

## Done-ness clarity — adequate

Most FRs have testable consequences. FR-1: "Files added to the configured directory (including nested subdirectories) are picked up within one scheduled scan interval without manual triggering." Testable. FR-2: "Every file that reaches the Compiler has all four Raw Ingest Agreement sections populated." Testable. FR-5: "Every Core Entity in a compiled document maps to a graph node; every Cross-Reference maps to a graph edge." Testable. FR-12: "Standard users cannot self-register." Testable.

However, several FRs have fuzzy boundaries:

- **FR-3, consequence 3: "Duplicate or near-duplicate raw content ... compiles to a single Core Entity, not two."** What counts as near-duplicate? Cosine similarity > 0.9? Substring match? Exact-match? The PRD doesn't define the threshold. An engineer implementing this would ask.

- **FR-6, consequence 1: "When new evidence *contradicts* an existing Decision, the Curator pass creates a Supersession edge."** How is contradiction detected? Does the evidence explicitly state a reversal? Does the inference from evidence conflict with the prior Decision? Is it LLM-detected or heuristic-based? The PRD doesn't say. This is a load-bearing definition for the core thesis and should be pinned.

- **FR-8, consequence 1: "A query about a Decision returns the Decision node ... when such a Decision *exists*."** What is a "matching Decision"? How is relevance determined? The hybrid retrieval (vector + structural + BM25) is named, but scoring/weighting is not. The consequences say retrieval must use "at least two of the three retrieval modes" but don't explain how they're combined or ranked. An engineer would need this definition to know if they've implemented it correctly.

- **FR-10, consequence 1: "Under a benchmark mixed-load stream, every request results in exactly one action."** What is the benchmark? What's the load (requests/sec)? What's the ratio of ingest to reply? The PRD says "benchmark mixed-load stream" but doesn't parameterize it. This is not testable without more specifics.

- **FR-11, consequence 2: "A scoped agent's responses are limited to the configured subset of the graph."** How is scoping defined? Is it a list of node IDs? A graph query? A domain name or tag? The PRD doesn't say. Without this, a UX person couldn't sketch the scoping UI and an engineer couldn't implement scope enforcement.

These gaps are moderate severity because they're the kinds of things Architecture or story step-down will clarify. But they leave ambiguity in a PRD that claims to be "acceptance-criteria-grade."

## Scope honesty — thin

Non-Goals are explicit: §6 lists freeform agents, source connectors beyond Markdown, OIDC/SAML, multi-tenant managed service, non-textual content, write-back loops, and OpenClaw plugin. §7.2 says "Everything listed in §6 Non-Goals" is out of scope for MVP.

`[ASSUMPTION]` tags are present (§4.3 FR-7 NFR, §8 SM-5 on curator performance budget) and indexed in §10. `[NOTE FOR PM]` at §7.2 names the write-back tension explicitly.

Open Questions (§9) are numbered and genuine. However, key definitions are missing that are part of the product contract, not Architecture deliverables:

- **Markdown source format**: The PRD says "Markdown files" and gives examples ("decision restated in two threads," "auth model handles refresh tokens") but doesn't specify the format. Is there a frontmatter convention? Directives? YAML boundaries? Should files have a header? Tags in the raw Markdown or extracted by the structuring agent? Without this, a user cannot know what constitutes valid input, and an engineer cannot write the Landing Zone reader. This is *not* an Architecture issue; it's a product contract issue.

- **Agent Template definition**: UJ-3 shows Priya selecting "Onboarding Q&A" from a library, but the PRD doesn't say: (a) what templates ship with v1 (is it "Onboarding Q&A" + "Decision Lineage Reporter" and nothing else?), (b) whether the library is extensible (can customers add new templates in v1?), (c) what the template definition format is (JSON schema? LlamaIndex workflow? Configuration file?). This matters for scope: is template authoring in v1 or deferred?

- **Contradiction detection logic** (FR-6): Related to done-ness but also scope — is this explicit LLM reasoning, heuristic pattern-matching, or a user signal? The brief addendum doesn't clarify and neither does the PRD. The Curator needs to know what "contradicts" means in order to be built.

- **Scoping primitives for agents** (FR-11): Related to done-ness but also scope — is scoping a first-class concept in the Agent Template definition, or is it a runtime wrapper? Can you scope one template multiple ways, or is a scoped template a distinct deployment unit?

These are not all Architecture issues; the Markdown format and Agent Template definitions are part of the user-facing contract and belong in the PRD.

Additionally, the Assumptions Index is lightweight. Only one assumption is indexed (curator performance budget). The PRD has several `[ASSUMPTION]` inline tags (I see the curator one explicitly marked), but Q-H ("Tenant scoping key(s) for v1") and Q-E ("Merkle-style subtree indexing feasibility") read like assumptions too (the PRD *assumes* single-key scoping will work, *assumes* Merkle indexing is a valid hypothesis). Open Questions are not Assumptions, so this is fine, but the boundary could be clearer.

## Downstream usability — adequate

**Glossary (§3):** Present with 14 terms. All are used consistently. "Decision," "Supersession," "Evidence," "Entity," "Curator," "Compiler," "Agent Template," "Landing Zone," "Raw Ingest Agreement," "LLM-Wiki" all appear in multiple FRs and UJs with the same meaning. No drift.

**IDs:** FRs are numbered FR-1 through FR-17, globally unique and contiguous. UJs are UJ-1 through UJ-3, contiguous. Success Metrics are SM-1 through SM-5, plus SM-C1 and SM-C2 (counter-metrics). Cross-references: "Realizes UJ-1" appears in Feature descriptions; "Validates FR-6, FR-8" appears in SM definitions. These resolve cleanly.

However:
- **Cross-references are section-relative**, not absolute. E.g., "§4.3 FR-7 NFR" and "§8 SM-5" point to sections, not to global anchors. If a downstream team extracts FR-7 alone, "§4.3" means nothing to them. An extracted section would need its own anchor scheme or a Glossary cross-link to be standalone.

- **Consequences within each FR are not numbered globally.** FR-1 lists three bullet-point consequences, but they're not labeled "FR-1-C1," "FR-1-C2," etc. This makes it hard to reference a specific consequence downstream (e.g., story: "tests FR-1-C3: a file that fails to ingest does not block other files in the same scan").

- **Reverse cross-references are light.** FRs reference UJs ("Realizes UJ-1") and SMs reference FRs ("Validates FR-6, FR-8"), but there's no reverse index (e.g., all SMs that validate FR-8). A downstream team building FR-8 would need to manually search to find which SMs depend on it.

These are not deal-breakers; the structure is usable. But they add friction for downstream extraction.

**For Architecture:** The tech stack is named (Neo4j, RabbitMQ, LlamaIndex, Laravel, SvelteKit + Bits UI) and constraints are clear ("event-driven," "no telemetry by default," "API-first"). Full schemas, agent definition formats, and data models are explicitly called out as "Architecture deliverables" (§5.4). This is appropriate; the PRD is not a design doc.

**For UX:** UJs are present with named protagonists and flows. However, "MCP-style connection settings" (FR-13) is vague — MCP typically refers to Anthropic's Model Context Protocol, but how it applies to agent configuration or source setup is not explained. A UX person sketching the admin UI would ask. Scoping in FR-11 is similarly vague (no UI mockups or information architecture).

**For Stories:** FRs are numbered and have consequences, but acceptance criteria per FR are implicit, not explicit. An engineer creating a story from FR-1 would need to escalate for clarification on "within one scheduled scan interval" (seconds? minutes? configurable?). "Near-duplicate" and "contradicts" (FRs 3 and 6) would need definition before story creation. This is not a blocker but it will generate rework.

## Shape fit — strong

This product is a **self-hosted internal tool with multi-stakeholder ingest and consumption flows, meaningful UX, and operator configuration.** The PRD shape matches:

- **Vision (§1)** — Yes, load-bearing. Sets the differentiator (first-class Decisions, Supersession, no telemetry, restart-safe).
- **UJs with named protagonists (§2.3)** — Yes, load-bearing. Three personas (Devika, Kian, Priya) with distinct jobs drive feature threads.
- **Glossary (§3)** — Yes, necessary. Domain-heavy product (Decisions, Supersession, Curator, Raw Ingest Agreement) requires fixed vocabulary.
- **FRs with consequences (§4)** — Yes, appropriate. 17 FRs with testable consequences; not a laundry list.
- **Non-Goals (§6)** — Yes, necessary. Sequencing and scope decisions (no freeform agents, connectors deferred, SSO out, managed service in v2) are strategic.
- **MVP Scope (§7)** — Yes, critical. Defends the choice to prove the pipeline end-to-end, not partial slices.
- **Success Metrics (§8)** — Yes, tied to thesis. Metrics validate decision lineage, end-to-end pipeline, agent handling, and template enablement.
- **Open Questions (§9)** — Yes, honest. Architectural unknowns (Merkle feasibility, tenant keys, RabbitMQ topology) are named.
- **Assumptions Index (§10)** — Yes, lightweight but present. Curator performance budget is flagged as unvalidated.

Is it over-formalized? No. Three UJs, not ten. A glossary, not a 100-term taxonomy. 17 FRs with consequences, not 100 line items. ~10 pages of body text (excluding appendices) is right-sized for this scope.

Is it under-formalized? No. It has enough rigor for downstream work, even if some details are deferred.

One note: the PRD is appropriately technical because **the product is technical**. A self-hosted, event-driven, graph-based system with agentic pipeline management is not simple, and the PRD reflects that. It names RabbitMQ, LlamaIndex, Neo4j, and Laravel because they are part of the product contract. This is not bloat; it's accuracy.

## Mechanical notes

- **Glossary drift:** None detected. All 14 glossary terms are used consistently throughout. No case drift (Decision vs decision), no synonyms (e.g., "graph node" vs Entity).
- **ID continuity:** Contiguous, no gaps. FR-1 through FR-17 (no gaps); UJ-1 through UJ-3 (no gaps); SM-1 through SM-5 plus SM-C1, SM-C2 (clear separation of counter-metrics). Cross-references resolve: "Realizes UJ-1" in feature descriptions, "Validates FR-6, FR-8" in SM definitions.
- **Unresolved cross-references:** None. All section references (§1, §3, §4.1, etc.) and ID references (FR-1, UJ-2, SM-3) are valid. `[ASSUMPTION]` tags appear in the text (§4.3 FR-7 NFR, §8 SM-5) and are indexed in §10.
- **UJ protagonist naming:** All three UJs have named protagonists carrying context inline. UJ-1 is Devika (senior specialist, retiring, has decades of notes). UJ-2 is Kian (six months in, predecessor vacated, extending auth model). UJ-3 is Priya (platform admin, narrowing scope for new hires). Clear.
- **Required sections present:** Status: draft (§metadata). Vision (§1), JTBD (§2.1), Non-Users (§2.2), UJs (§2.3), Glossary (§3), Features with FRs (§4), Cross-Cutting Requirements (§5), Non-Goals (§6), MVP Scope (§7), Success Metrics (§8), Open Questions (§9), Assumptions Index (§10). All present. No missing sections for this product type (single-tenant internal tool with significant UX and ops configuration).

---

## Findings Index

### Critical
- None. The PRD is not fundamentally broken.

### High

1. **[high]** Markdown source format specification (§4.1, FR-1, FR-2) — "Markdown files" is not defined. The PRD does not specify whether files require frontmatter, directives, YAML boundaries, header structure, or tag placement. This is part of the user-facing contract (how Devika drops files) and should be pinned here. *Fix:* Add a subsection in §4.1 or §0 (Document Purpose) specifying Markdown format (e.g., "Files are assumed to be UTF-8 Markdown with optional YAML frontmatter; the Raw Ingest Agreement normalisation extracts or generates the Content Header from file metadata or LLM inference").

2. **[high]** Agent Template definition and v1 library (§4.5, FR-11, UJ-3) — The PRD names "Nuron-supplied Agent Templates" and gives examples (Onboarding Q&A, Decision Lineage Reporter) but does not specify: (a) which templates ship with v1 (is it only these two?), (b) whether the library is extensible (can customers add templates in v1?), or (c) what the template definition format is. This affects scope (is template authoring in-scope for v1?) and downstream product work (admin UI, REST schema). *Fix:* Add a subsection specifying the v1 template library (e.g., "v1 ships with Onboarding Q&A and Decision Lineage Reporter templates; customer-authored templates are deferred to v2. Templates are defined as [JSON/YAML/LlamaIndex workflow] and are loaded from [location]. Admins can enable, disable, and scope templates via admin UI or REST").

3. **[high]** Contradiction detection logic (§4.3, FR-6, edge case in UJ-1) — "When new evidence *contradicts* an existing Decision" is not defined. The PRD does not specify whether contradiction is explicitly detected by the LLM, heuristic-based (e.g., conflicting conclusions), or a user signal. This is a load-bearing definition for the core thesis (Supersession at write time, not inferred at read time) and blocks implementation of FR-6. *Fix:* Clarify in FR-6 consequences whether contradiction is LLM-detected (e.g., "The LLM-based Curator detects contradiction when new evidence explicitly indicates a decision was reversed or when inferred conclusions conflict with prior Evidence") or heuristic-based, with examples.

### Medium

4. **[medium]** "Near-duplicate" definition in FR-3 (§4.2) — Consequence states "Duplicate or near-duplicate raw content ... compiles to a single Core Entity, not two" but does not define the threshold. Without this, an engineer cannot implement deduplication and would ask for clarification. *Fix:* Specify in FR-3 consequence 3 (e.g., "Documents with >0.9 cosine similarity in embedding space are considered near-duplicates and merged into a single Core Entity").

5. **[medium]** "Matching Decision" criteria in FR-8 (§4.4) — The consequence "A query about a Decision returns the Decision node ... when such a Decision *exists*" does not define relevance. The consequence also says "Retrieval combines at least two of the three retrieval modes" but doesn't explain scoring or weighting. An engineer needs to know: is a "match" the top-1 result? The top-1 above a threshold? How are vector, structural, and BM25 scores combined? *Fix:* Add clarity (e.g., "A matching Decision is determined by the hybrid retrieval ranking of the top result; relevance is scored as a weighted combination of vector similarity (weight: 0.4), structural graph distance (weight: 0.4), and BM25 keyword match (weight: 0.2). A Decision is returned if the combined score exceeds [threshold] or no Decision exceeds the threshold (return closest related Entities instead)").

6. **[medium]** "Benchmark mixed-load stream" in FR-10 (§4.5) — Consequence states "Under a benchmark mixed-load stream, every request results in exactly one action" but does not parameterize the benchmark. What load (requests/sec)? What ratio of ingest to reply? Without this, the test acceptance criteria cannot be written. *Fix:* Specify (e.g., "A benchmark of 100 req/sec with 60% ingest and 40% reply requests yields exactly one action per request with zero loss or duplication").

7. **[medium]** Agent scoping mechanism (§4.5, FR-11) — "Scoped to a subset of the graph (one domain)" is vague. Is scoping a list of node IDs? A SPARQL/graph query? A domain name or tag-based filter? Is it a first-class concept in the template definition or a runtime wrapper? Without this, a UX team cannot sketch the scoping UI and an engineer cannot implement scope enforcement. *Fix:* Specify (e.g., "A scoped agent restricts its queries to a subset of the graph defined by a domain tag or node-ID list, configured via the admin UI at template enablement time. The subset is enforced server-side by filtering query results before the agent sees them").

### Low

8. **[low]** Tenant scoping schema vagueness (§4.6, FR-14) — "Every persistent record can carry a tenant/workspace identifier" is stated but the schema is not detailed. Is it a single string key? A UUID? A hierarchical namespace? While this may be an Architecture decision (Q-H asks about this), clarity here would help downstream teams understand the scoping boundary. *Fix:* Add a note (e.g., "Tenant scoping is a single workspace/team identifier (string or UUID, exact schema in Architecture); all graph nodes, edges, queue messages, and audit entries carry this identifier for isolation").

9. **[low]** MCP-style connection settings (§4.6, FR-13) — "MCP-style connection settings" is mentioned but not explained. Does this refer to Anthropic's Model Context Protocol? How does it apply to agent or source configuration? The term is jargon to the PRD's core audience. *Fix:* Either define (e.g., "MCP-style settings follow a [standard/pattern] where [brief desc]") or replace with clearer language (e.g., "platform-integration connection settings").

10. **[low]** FR consequence numbering for extraction — Individual consequences within each FR are not labeled (e.g., "FR-1-C1," "FR-1-C2"), making them harder to reference in downstream stories. *Fix:* Optional: number consequences globally or per-FR for traceability (e.g., "FR-1.1: Files added to the configured directory..."). This is nice-to-have, not essential.

11. **[low]** Reverse cross-references — No reverse index mapping SMs to their dependent FRs (e.g., "All SMs validating FR-8"). A downstream team building FR-8 would need to search manually. *Fix:* Optional: add a table in §5 or §10 mapping FR → dependent SMs for traceability. Not blocking.

---

## Summary

**Overall Verdict:** Adequate-to-strong PRD with coherent thesis and pipeline design. Clear decision-readiness and strategic coherence. Three moderately-severity issues require clarification before downstream work.

**Dimension Verdicts:**
- Decision-readiness: **adequate** (decisions clear; some mechanics deferred)
- Substance over theater: **strong** (earned content, specific vision)
- Strategic coherence: **strong** (unified thesis, metrics validate it)
- Done-ness clarity: **adequate** (most FRs testable; some fuzzy boundaries)
- Scope honesty: **thin** (non-goals explicit; key definitions missing)
- Downstream usability: **adequate** (glossary/IDs clean; reverse refs light)
- Shape fit: **strong** (matches product type, appropriately technical)

**Findings by Severity:**
- **Critical:** 0
- **High:** 3 (Markdown format, Agent Template definitions, contradiction detection)
- **Medium:** 4 ("near-duplicate," "matching Decision," benchmark parameterization, scoping mechanism)
- **Low:** 4 (tenant schema clarity, MCP terminology, consequence numbering, reverse refs)
