# Reconciliation: prd.md vs addendum.md

## Gaps (things in addendum.md that need FR/NFR-level representation in prd.md but don't have it)

- **APISIX / edge-gateway v1.1 candidacy is entirely absent from the PRD's roadmap.** Addendum §C.1.5 makes a concrete forward-looking call: "v1.1 / v2: bring APISIX in as the edge enforcement layer when (a) untrusted/external traffic volume actually appears, or (b) v2 multi-tenant makes a single external chokepoint + edge auth validation worth the ops cost." This is a roadmap decision, not just rejected-alternative rationale, but PRD §5.5 (Rollout & Phasing) lists only the three connectors for v1.1 and never mentions an API gateway as a v1.1/v2 candidate. Recommend adding a one-line pointer in §5.5 (v1.1 or v2 bullet) or §9 Open Questions so Architecture knows APISIX is a live candidate, not a closed rejection.

- **Q-I (async pipeline mode) is missing from the PRD's Open Questions, and the PRD has silently resolved it in the opposite direction from the addendum's proposed default.** Addendum §I.3 proposes "v1 default: in-process pipeline... v1 opt-in: RabbitMQ" and explicitly marks this "Architecture to confirm" via Q-I (referenced in addendum §H.6/§I). The PRD's FR-4 instead hard-commits: "No pipeline stage has an in-process-only fallback path in v1 — RabbitMQ is a required data-plane component, not optional," and §4.2/§5.3 describe RabbitMQ as mandatory throughout, with no mention of Q-I or an in-process option anywhere in the PRD. Either the PRD's author made a deliberate decision that supersedes the addendum's proposed default (in which case Q-I should be listed in §9 as *resolved*, with a one-line rationale), or this is an unreconciled conflict Architecture should catch. As written, a reader of only the PRD has no visibility that this was ever an open question.

## Consistency checks (roadmap sequencing, rate-limiting approach, tenant primitives)

- **v1.1 connector sequencing: consistent.** Addendum §C order (1. Confluence page export, 2. Internal forum ingestion, 3. Jira issue ingestion) matches PRD §5.5 exactly ("Confluence page export, internal forum ingestion..., Jira issue ingestion"). Write-back gating on the forum connector is also consistent between addendum §C.2 ("Requires Q-C to be resolved") and PRD §4.4/§6 ("deferred to v1.1, gated on the forum connector").

- **Rate limiting / APISIX decision: consistent in substance.** Addendum §C.1.5 decides v1 implements per-tenant rate limiting in Laravel with no new container, deferring APISIX. PRD FR-16 matches this precisely ("implemented in Laravel (no additional edge-gateway dependency in v1)"). The only issue is the missing forward pointer to APISIX as a v1.1/v2 candidate (see Gaps above) — the *v1* decision itself is faithfully carried over.

- **v2 multi-tenant primitive roadmap: fully covered.** All five addendum §D primitives map cleanly to PRD FRs:
  - Tenant ID column on every persistent record → FR-14.
  - Per-tenant data directory → FR-14.
  - Per-tenant auth scope (server-side scoping regardless of request body) → FR-14.
  - Per-tenant rate limits → FR-16.
  - Tenant-aware audit log → FR-17 ("Audit entries are tenant-scoped (per FR-14) even though v1 ships no dedicated per-tenant audit UI").
  What v1 explicitly excludes per addendum §D (tenant mgmt UI, billing, metering, sign-up flows, managed control plane) matches PRD §6 Non-Goals ("Multi-tenant managed Nuron-as-a-service, tenant management UI, billing, metering — v2").

- **Merkle-style subtree curation hypothesis: properly flagged as open, and its validation status is accurate.** PRD §9 Q-E: "Merkle-style subtree indexing feasibility. Hypothesis underlying FR-7's touched-subtree curation (brief addendum §B). Validate or invalidate during Architecture." This correctly mirrors addendum §B.5's "Hypothesis. Validate or invalidate during [CA]." The PRD also independently reflects one of the addendum's specific mitigations (determinism risk, §B.4) in FR-7's consequence text ("Curator output for an unchanged subtree is byte-identical to its prior output (determinism is enforced — pinned temperature, ordered inputs)"), which is a good sign the hypothesis's substance, not just its label, made it into the PRD.

## Correctly-excluded (content that belongs only in addendum.md, confirmed correctly absent from prd.md)

- **§A Rejected Alternatives** (vector-only RAG, real-time-only ingestion, freeform v1 agents, self-hosted-with-control-plane, connector-first v1) — pure rationale for decisions already reflected in the PRD's actual FRs; no FR/NFR-level content missing.
- **§B.1–B.4 Merkle hypothesis mechanics** (hash propagation math, sibling-context mitigation, write-amplification concern) — implementation-level detail correctly left for Architecture; the PRD only needs (and has) the open-question flag.
- **§E Technical Constraints** (Neo4j version pin, RabbitMQ topology/queue layout, LLM/embedder model choice, LLM-Wiki schema finalisation) — correctly parked for Architecture; PRD's Q-J (container/compose topology) and the embedding-floor NFR (§5.2) already carry the pieces that need PRD-level visibility. Audit log retention policy (addendum §E, referencing "Q-D") is the one item here that's thinner than the rest — FR-15 covers tenant-deletion retention but there's no explicit pointer to a general audit-log retention open question in §9. This is a minor/borderline case, not flagged as a full gap since it's plausibly still "Architecture's problem," but worth a note if the PM wants completeness.
- **§F Personas (in-depth)** — PRD §2 already carries personas at the right depth for a PRD; the addendum's expanded bios are reference material, not omitted requirements.
- **§G Glossary** — PRD has its own §3 Glossary with consistent terminology; addendum's copy is a parking spot, not a gap.
- **§H Competitor Landscape (Mem0/Cognee/Graphiti)** — pure research/rationale supporting brief differentiation claims; correctly has zero FR/NFR footprint in the PRD.
- **§I.1/I.2 RabbitMQ trade-off rationale** (durability vs ops-tax arguments) — the *rationale* is correctly addendum-only; only the *decision/open-question* (Q-I, flagged above) needs PRD-level visibility, which is currently missing.
- **§J Embedding Model Floor rationale** — the floor itself is correctly promoted to PRD §5.2 as an NFR; the supporting rationale (why 600M/1024-dim, MTEB/BEIR caveats) correctly stays addendum-only.
- **§K Deployment Topology Notes** — the container set, network boundaries, and per-tenant data directory notes are already reflected at the right altitude in PRD §5.3/§5.4/FR-14; the addendum's volume-mount and profile-model detail is correctly Architecture-level, not PRD-level.
- **§L OpenClaw deferral rationale** — the decision itself is in PRD §6 Non-Goals; the crowded-landscape argument correctly stays addendum-only.
