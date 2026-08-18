---
status: accepted
---

# Humans confirm entity merges only; typed link creation is deferred

The second human gate asks a reviewer to confirm whether two differently-named entities are the
same referent (a merge), and never asks them to create a new typed, directed edge between two
distinct entities (a link). Link creation needs a closed relationship vocabulary
(`possible_relations` is a `Literal`), and this slice's five-document fixture is nowhere near
enough evidence to design that vocabulary correctly. Shipping a wrong or incomplete vocabulary now
means reviewers either can't express the relationship they're looking at or default to picking the
first option — actively degrading the graph rather than improving it.

## Considered options

- **Let reviewers create typed links now**, using the same closed `possible_relations` vocabulary
  the Compiler uses — rejected: the vocabulary would be designed from imagination, not evidence,
  and a wrong choice compounds every time a reviewer picks the nearest-available type instead of
  the correct one.
- **Untyped `RELATED_TO` edges** as a stopgap — rejected: an untyped edge is a similarity blob that
  `path_depth=2` graph expansion would follow indiscriminately, actively degrading retrieval rather
  than improving it.
- **Merge confirmation only, link creation deferred** — accepted.

## Consequences

Every "I want to link these but there's no type for it" moment a reviewer has during this slice
should be logged. That log is what should design the relationship vocabulary for v1.1's link
creation — not a vocabulary invented ahead of the evidence.

**Merge transaction invariants**, required before NU-010 implements the confirmation gate:
survivor selection is deterministic, not arbitrary — the pre-existing (natural-key-matched) node
always survives, and the newly-ingested entity's name is recorded as an alias on it, never the
reverse. On confirm: union the two provenance ref-sets onto the survivor, add the alias, record who
confirmed it. Re-confirming the same pair after a crash or retry must be a no-op (idempotent
replay) — matching every other retry-safety rule in this slice (ADR-0001). Audit trail beyond "who
confirmed it" and undo of a confirmed merge are both **out of scope for this slice**, not silently
assumed: a wrong merge is corrected by a human noticing and filing a follow-up, not by a system
rollback.
