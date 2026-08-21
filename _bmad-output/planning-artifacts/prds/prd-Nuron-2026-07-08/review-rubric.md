# PRD Quality Review — Nuron (prd-Nuron-2026-07-08)

## Overall verdict

This PRD holds up well: it has a genuine thesis (write-time supersession as the differentiator against Mem0/Cognee), FRs carry testable consequences almost without exception, and the 2026-07-13 Notion-comment reconciliation (nuron-ai split, three-source hybrid retrieval, FR-1/FR-6 confirmations, FR-11 v2 deferral) is cleanly threaded through every affected section with no stale references or contradictions found. The only material gaps are mechanical: one technical term ("Core Entity") is used repeatedly but never defined in the Glossary, and the high-level API contract in §5.4 doesn't list an endpoint for the audit-retrieval capability FR-17 requires. Nothing here blocks handoff to Architecture.

## Decision-readiness — strong

Trade-offs are named with what was given up, not smoothed over: FR-7's Merkle-conditional fallback states a concrete degraded target (2 hours vs. 10 minutes) rather than hand-waving; SM-C1/SM-C2 counter-metrics explicitly forbid the obvious shortcuts (weakening FR-2 validation, dropping a retrieval source) that would otherwise look like wins. The §7.2 `[NOTE FOR PM]` on write-back flags a real narrative tension rather than a safe checkpoint. Open Questions (§9) are genuinely unresolved decisions deferred to Architecture, not rhetorical questions answered in the next sentence. No findings.

### Findings
None.

## Substance over theater — strong

Four JTBD personas (§2.1) each map to a distinct feature area rather than padding the section — the "other internal agents" persona alone justifies FR-16's separate rate-limit buckets. The Vision (§1) names specific competitors (Mem0, Cognee) and specific mechanisms (no telemetry, restart-safe RabbitMQ) it's differentiating against — not a swappable generic statement. NFRs are concrete: §5.2's embedding floor pins a parameter count and dimensionality with a named default model, and FR-7's performance NFR defines the test hardware. No boilerplate "must be scalable/secure/reliable" language found anywhere in the document.

### Findings
None.

## Strategic coherence — strong

The thesis (Decisions with write-time Supersession lineage, compounding over time) drives feature sequencing (Ingest → Structure → Compile → Graph → Query) and is validated by SM-1 (decision-lineage answerable) rather than an activity metric. The non-quantitative signal in §8 ("people... return to Nuron voluntarily") is a real qualitative bar, not a vanity number. Counter-metrics (SM-C1, SM-C2) are present and tied to specific FRs. MVP scope reads as a coherent pipeline-proof (problem-solving MVP), not a backlog with headings.

### Findings
None.

## Done-ness clarity — adequate

Nearly every FR has testable, bounded consequences (a grep for vague qualifiers — "gracefully," "reasonable," "user-friendly," "robust," "scalable," "seamless" — returned zero hits across the whole document). FR-1, FR-6, FR-8, FR-14, FR-15, FR-16 in particular read as acceptance criteria already. One gap:

### Findings
- **low** API contract silent on audit-retrieval endpoint (§5.4 vs. FR-17) — FR-17's consequences require "an admin can retrieve the specific Decision/Entity node(s)" a response cited and "the raw Evidence/Episode" a node derived from, but §5.4's endpoint enumeration ("source and system configuration, user/role management, tenant deletion (FR-15), and query") has no audit/traceability endpoint listed. §5.4 is explicitly "high-level only," so this doesn't block Architecture, but it's the one FR whose retrieval surface isn't named even at high level. *Fix:* add a one-line audit/traceability endpoint bullet to §5.4, or note the omission is deliberate (UI-only in v1).

## Scope honesty — strong

§6 Non-Goals is explicit and comprehensive (SSO, connectors, multi-tenant SaaS, non-textual content, write-back, OpenClaw plugin all called out). The three remaining `[ASSUMPTION]` tags (FR-2 determinism, FR-3 near-duplicate threshold, FR-7/SM-5 Merkle-conditional) are indexed in §10 with a clean roundtrip — no orphaned inline tags, no index entries without an inline source. The FR-11/UJ-3/SM-4 v2 deferral is handled as an honest, explicit rescope (identifier preserved, not silently dropped) rather than quietly disappearing. Open-item density (17 Open Questions + 3 assumptions + 1 NOTE FOR PM) is high in absolute terms but proportionate: this PRD hands off to Architecture next, not to a build-now decision, and the decision log records a deliberate user choice not to over-specify implementation detail at PRD level.

### Findings
None.

## Downstream usability — adequate

Glossary (§3) is present and the vast majority of terms are used identically across FRs, UJs, and SMs — the 2026-07-13 additions (`nuron-ai`, HNSW, Neo4j GraphRAG, RRF, LLMRerank) are all defined and consistently applied everywhere they appear, including the Assumptions Index and Success Metrics. FR/UJ/SM IDs are contiguous and cross-references resolve. One drift:

### Findings
- **low** "Core Entity" used but never defined (§4.2 FR-3, §4.3 FR-5, §10) — the Glossary (§3) defines "Entity / Knowledge Node" as the graph-node term, but FR-3 and FR-5 repeatedly use "Core Entity" (echoing the LLM-Wiki's "Core Entities & Relationships" section name) without stating whether it's the same concept pre-persistence or a distinct intermediate representation. *Fix:* either add a "Core Entity" Glossary entry distinguishing it from "Entity / Knowledge Node," or normalize FR-3/FR-5 to use the Glossary term directly.

## Shape fit — strong

This is a chain-top PRD (feeds Architecture → UX → Epics) for a technical-capability / internal-tool product, and the shape matches: JTBD personas carry the "who and why" without over-building a consumer-style persona set, while UJ-1/UJ-2 (and deferred UJ-3) each have a named protagonist (Devika, Kian, Priya) carrying context inline, exactly where downstream usability needs it. Capability depth (full testable Consequences per FR) matches the user's explicit choice for FR depth recorded in the decision log. Not over-formalized, not under-formalized.

### Findings
None.

## Mechanical notes

- **2026-07-13 reconciliation integrity:** verified clean. No stale "vector similarity," "fourth retrieval mode," "LangGraph" (outside the intentionally-tracked Open Question #1), or lingering Agent-Template v1 references found anywhere outside the deferred-to-v2 anchors. The three retired `[ASSUMPTION]` tags (FR-1 UTF-8, FR-6 contradiction criteria) are fully removed from both the inline text and §10 Assumptions Index — no orphaned entries either direction.
- **Assumptions Index roundtrip:** clean. Exactly 3 inline `[ASSUMPTION]` tags (FR-2, FR-3, FR-7/SM-5), all 3 indexed in §10, no extras either direction.
- **ID continuity:** FR-1…FR-17 contiguous with FR-11 intentionally retained-but-deferred (documented, not a gap). UJ-1…UJ-3 and SM-1…SM-5 (+ SM-C1/C2) contiguous with deferrals clearly labeled `[v2]`.
- **Glossary drift:** see "Core Entity" finding above (Downstream usability). No other case/plural/synonym drift observed — "tenant/workspace," "Evidence/Episode" (already tracked as Open Question #15), and the three new 2026-07-13 terms are all used consistently.

