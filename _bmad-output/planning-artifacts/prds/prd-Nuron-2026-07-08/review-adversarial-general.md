---
title: "Nuron PRD — Adversarial Review"
status: review
reviewed: 2026-07-08
reviewer: Technical Adversary
---

# Adversarial Review: Nuron PRD

## Executive Summary: Red Flags Exceed Green Lights

This PRD commits to a complex, stateful, distributed system (compiler pipeline over RabbitMQ → curator → graph → query agent) while leaving critical foundations unvalidated and ambiguous. The core differentiator — decision lineage with explicit supersession modeled at write time — rests on three unproven pillars: (1) the Merkle-style touched-subtree curation hypothesis is called a hypothesis and marked for validation but is hard-committed as FR-7 without a fallback, (2) curator determinism is assumed but acknowledged in the brief as "risky — LLM non-determinism even at temperature 0," and (3) the decision-lineage detection logic ("when the evidence indicates a change") is fuzzy and potentially unreliable. The PRD also doubles down on LlamaIndex after the brief repeatedly named LangGraph, creating a tech-stack ambiguity that makes Architecture's job impossible. Below: the highest-risk findings.

---

## Tier-1 Findings (Blocking)

### F-1: Merkle-Hypothesis Curation Commits a Hypothesis Without Fallback

**Finding:** FR-7 ("Touched-subtree-only curation") and success metric SM-5 (10k-node graph, 1% touched, under 10 minutes) assume the Merkle-style indexing hypothesis works. But the brief's addendum §B explicitly marks it as "Hypothesis. Validate or invalidate during [CA]." If the hypothesis fails during Architecture, the entire scalability story collapses.

**Why this matters:**
- If Merkle fails, the fallback options (full re-curation nightly, heuristic windowing) are dramatically different architecturally.
- Full re-curation will not meet the SM-5 performance budget on anything but tiny graphs.
- The PRD is already written around the Merkle win; backing out breaks the narrative and likely shifts scope/timeline.

**Recommendation:**
- Either move FR-7 and SM-5 to a "Conditional on Merkle Validation" note, or define a non-Merkle fallback algorithm (e.g., heuristic time-windowed re-curation) and commit to that, treating Merkle as a v1.1 optimization.

---

### F-2: Curator Determinism Assumption is Dangerous and Acknowledged as Risky

**Finding:** The Merkle-style indexing in F-1 *requires* determinism — if the input subtree hasn't changed, the output must be byte-identical. But the brief addendum §B.4 lists the main risk as: "LLM non-determinism even at temperature 0 in some pipelines." The PRD suppresses this risk by not mentioning it in the main body; it only appears in the addendum.

**Why this matters:**
- If the curator produces slightly different LLM-Wiki text on a re-run of an unchanged subtree, the Merkle hash changes, and the whole touched-subtree optimization is wasted.
- FR-7's "determinism is enforced — pinned temperature, ordered inputs" is passive voice that papers over the fact that LLM determinism is not always enforced by the API or the model.

**Recommendation:**
- Explicitly test the target LLM with `temperature=0` and identical input multiple times; confirm byte-for-byte reproducibility, or use a fuzzy-match hash and accept false-positive re-curations. Surface this finding in the main PRD body, not just the addendum.

---

### F-3: Tech Stack Ambiguity: LangGraph vs. LlamaIndex — PRD Flip-Flopped, Creating a Cascade of Unknowns

**Finding:** The brief mentions **LangGraph** multiple times as the compiler/curator framework. The PRD then says **LlamaIndex** without deep reconciliation (Open Question #1 flags it but doesn't resolve it).

**Why this matters:**
- The compiler pipeline (ingest → structure → compile → curate → graph) is complex and stateful; LangGraph's explicit state graph and replay-ability are a natural fit, while LlamaIndex leans retrieval/query-first.
- If implementation starts on the wrong framework, discovery is late and rewrite risk is high.
- The "one action per request" model (FR-10) implies explicit request-response semantics.

**Recommendation:**
- Resolve the LangGraph/LlamaIndex choice with Architecture immediately rather than carrying it as an open question; document why the chosen framework is adequate for a stateful, long-running pipeline.

*(Facilitator note: this correction was explicit and deliberate — the user confirmed LlamaIndex is what's actually being built, overriding the brief's language. The adversarial framing above is preserved verbatim as the reviewer produced it, but the PRD's Open Question #1 already flags this for Architecture reconciliation; no further action needed beyond what's already tracked.)*

---

### F-4: Decision Lineage Detection ("When Evidence Indicates a Change") Is Fuzzy and Untestable

**Finding:** FR-6 says the Curator creates a Supersession edge "when the evidence indicates a change." This criterion is fuzzy — what counts as "indicating a change" is undefined.

**Why this matters:**
- Supersession is the central differentiator claimed in the Vision. If the curator cannot reliably detect supersession, the differentiation evaporates into generic RAG with version labels.
- SM-1 requires the Supersession chain to be correct; if detection is unreliable, the chain is wrong.

**Recommendation:**
- Define explicit criteria (e.g., explicit-statement match vs. contradiction-with-new-evidence), and/or add an admin confirmation step for candidate supersessions, and/or a counter-metric against relaxing the detection threshold.

---

### F-5: Hybrid Retrieval Ranking and Fallback Behavior Is Unspecified

**Finding:** FR-8 says retrieval combines vector + structural + BM25/Tags but does not specify weighting, ranking, or fallback when a mode returns nothing or times out.

**Why this matters:**
- Query quality depends entirely on ranking; two teams could implement "hybrid retrieval" differently and both be compliant with the FR text.

**Recommendation:**
- Specify a default ranking/weighting scheme and fallback behavior (e.g., a mode that times out is weighted 0 but doesn't block the query).

---

## Tier-2 Findings (High Risk, Implementation-Blocking)

### F-6: "Touched Subtree" Curation Boundary Is Undefined
FR-7 assumes the curator can identify "subtrees changed since the last pass" but graph databases don't have natural subtree semantics — this boundary (Decision + edges? connected component? domain tags? sibling-context dependency per addendum §B.4) needs an explicit definition, ideally before Architecture starts building against it.

### F-7: Agent Template Contract and Scoping Are Undefined
FR-11 doesn't specify what a template *is* (schema/format), how scoping is expressed (natural language? Cypher? domain tags?), how scope is enforced, or which templates ship in v1. This is the product surface for agent customization and deserves more definition in the PRD itself, not just Architecture.

### F-8: Raw Ingest Agreement Schema Has No Versioning Strategy
FR-2's fixed schema has no version field or migration strategy for future schema evolution (e.g., v1.1 adding a new section). Recommend adding a `ria_version` concept and a confidence/fallback path for files that don't fit the schema.

### F-9: API Contract Is Deferred to Architecture; Frontend Team Cannot Start Until Then
§5.4 defers full schemas to Architecture. This blocks frontend parallelism. Recommend at least illustrative request/response examples per named endpoint group in the PRD.

### F-10: Performance Assumption (SM-5) Lacks Hardware Definition and Depends on Unvalidated Merkle
"Modest hardware" is undefined and SM-5 depends on F-1's unproven hypothesis. Recommend defining hardware baseline and making SM-5 conditional on Merkle validation, with a full-re-curation fallback target as a backstop.

---

## Tier-3 Findings (Medium Risk, Design-Clarifying)

### F-11: Audit Log Specification Is Vague — retention, access control, and orphaning-on-deletion behavior are unspecified, despite the audit log likely containing PII (query text, decision authorship).

### F-12: Right-to-Be-Forgotten Deletion (FR-15) Has Referential-Integrity Risks — synchronous vs async deletion semantics, in-flight query behavior, and backup handling are unspecified.

### F-13: Restart-Safety Claim (FR-4) Versus Actual Failure Modes — durability SLA, idempotency on redelivery, and orphaned-write scenarios (broker crash after Neo4j write but before ack) aren't addressed.

### F-14: LLM-Wiki Validation and Recovery Are Missing — no schema validation step or dead-letter/retry policy is defined for malformed compiler output.

### F-15: Success Metric SM-1 Is Cherry-Picked to a Hand-Curated Seed Dataset — recommend testing against a larger, messier, more adversarial real-world-shaped corpus, not just the demo seed set.

### F-16: No Backwards-Compatibility or Migration Strategy for Schema/Graph Evolution across v1 → v1.1 → v2.

---

## Tier-4 Findings (Lower Risk, Clarifying)

### F-17: "Evidence" vs. "Episode" Terminology Is Inconsistent — the Glossary conflates these; pick one term or define the distinction (unit of source content vs. a temporal sequence of such units).

---

## Summary Table: Finding Severity & Action

| ID | Title | Tier | Action |
|---|---|---|---|
| F-1 | Merkle Hypothesis Commits Without Fallback | T1 | Define fallback, make SM-5 conditional |
| F-2 | Curator Determinism Unproven | T1 | Test LLM determinism empirically; surface risk in PRD body |
| F-3 | LangGraph vs. LlamaIndex Ambiguity | T1 | Already tracked as Open Question #1 — deliberate, user-confirmed correction |
| F-4 | Decision Lineage Detection Fuzzy | T1 | Define supersession detection criteria |
| F-5 | Hybrid Retrieval Ranking Unspecified | T1 | Document ranking function and weights |
| F-6 | "Touched Subtree" Boundary Undefined | T2 | Define subtree boundary schema |
| F-7 | Agent Template Contract Undefined | T2 | Enumerate v1 templates and scoping mechanism |
| F-8 | RIA Schema Has No Versioning | T2 | Add schema versioning + fallback strategy |
| F-9 | API Contract Deferred | T2 | Add illustrative request/response examples |
| F-10 | Performance Assumption Vague | T2 | Define hardware baseline, make metric conditional |
| F-11 | Audit Log Vague | T3 | Specify retention, access, orphaning |
| F-12 | Deletion Referential-Integrity Risk | T3 | Define deletion semantics |
| F-13 | Restart-Safety Edge Cases | T3 | Specify durability SLA, idempotency |
| F-14 | LLM-Wiki Validation Missing | T3 | Add validation + DLQ handling |
| F-15 | SM-1 Cherry-Picked to Seed Data | T3 | Test against messier real-world corpus |
| F-16 | No Migration Strategy | T3 | Define backwards-compatibility policy |
| F-17 | Evidence/Episode Terminology Muddled | T4 | Clarify or remove Episode |

---

## Verdict

Six Tier-1/near-Tier-1 findings represent fundamental ambiguities (decision lineage detection, curation algorithm/performance dependency, retrieval ranking) that should be resolved or explicitly flagged for early Architecture spikes before parallel Architecture/UX work proceeds at full speed. One of the six (F-3, tech stack) is a deliberate, already-tracked, user-confirmed decision rather than an open gap. The rest (F-1, F-2, F-4, F-5, and the Tier-2 set) are legitimate scope/definition gaps worth triaging.

*Review completed 2026-07-08.*
