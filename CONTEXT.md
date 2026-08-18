# Nuron

Self-hosted "company brain": ingests an organisation's scattered textual record and turns it into
a graph of Decisions, the Entities they touch, and the evidence lineage connecting them, answered
through cited, traceable queries. This file is the single context for the whole repo — no
`CONTEXT-MAP.md` exists because nothing has split into separate bounded contexts yet.

## Language

### Ingestion & identity

**Landing Zone**:
Untouched storage for raw ingested content before any structuring or review touches it.

**Content hash**:
The sha256 digest of a document's raw bytes. This *is* the document's identity — the same digest
from two different entry points (watched directory, upload) is the same document, never a
duplicate. See ADR-0005.
_Avoid_: UUID, document ID (as a synonym — an ID is assigned; a content hash is derived).

**`source_owner`**:
The configured fallback author name used when a document's own content has no attribution. Never
called `author` — that name is reserved for what a citation actually claims a person said or
decided.

**`author_source`**:
One of `extracted | default | unknown`, recorded alongside every `author` value so a citation can
be trusted proportionally: `extracted` means the document's own content named the author;
`default` means `source_owner` filled the gap.

**Pipeline state**:
Where a document currently sits in the ingest state machine: `landed → extracted → parsed →
awaiting_review → content_approved → compiled → awaiting_merge_confirm → persisted`. Persisted as
a row, not a UI step — a document can sit at any state indefinitely between human actions.

### Review & evidence

**Reviewed Source**:
The immutable, versioned document produced when a human approves content at the first review
gate. This, not the raw file, is the **Evidence root** — everything a citation resolves to is
rooted here, because a human vouched for this content and the raw original cannot make that claim.
_Avoid_: approved document (imprecise about what "approved" froze).

**Evidence root**:
The Reviewed Source version that an `evidence_span` offset is measured against. Never the raw
original — if a reviewer edited the body, offsets into the raw file would resolve intermittently,
which is worse than not resolving at all.

**Evidence / Episode**:
The raw source content a Decision or Entity ultimately traces back to. One hop back from a
Reviewed Source (two hops for formats that go through extraction, like `.pdf`/`.docx`) — reachable
on request, never the default view a citation shows.

### Entity matching

**Natural key**:
`normalize(name) + label` — the identity two entities are compared against before any human is
involved. Two identically-named entities of the same label are automatically the same node
(an **auto-join**); this deliberately under-merges; differently-named same-referents stay separate
nodes until a human confirms otherwise.

**Alias**:
An alternate name a human has confirmed refers to the same entity as its natural key, folded into
that entity's match set. Once confirmed, any future document using either name matches the same
node without asking again.

**Auto-join**:
Two documents' entities connecting with no human step, because their names share a natural key.
Shown to a reviewer as information, never asked as a question — the name alone already answered it.

**Merge candidate**:
A cosine-similarity-surfaced pair of differently-named entities that *might* be the same referent,
presented to a human as a question ("is X the same as Y?"), capped at 5 per document, defaulting
to "not the same" if the reviewer doesn't actively confirm.

**Provenance ref**:
A refcounted pointer from a graph node back to a Reviewed Source that contributed to it. A node
survives as long as at least one ref remains; it is deleted only when its last ref is released.

### Agents & compilation

**Compiler**:
The LLM agent that turns an approved Reviewed Source into typed graph triples with an explicit
`decisions[]`. Retained from the PRD, but its output form changed — see ADR-0001 and the note
below on LLM-Wiki.

**Decision**:
A first-class graph node representing a choice that was made: author, timestamp, and a resolving
Evidence edge. What a query answer ultimately cites.

**Entity**:
A first-class graph node representing something a Decision touches or relates to, distinct from
the Decision itself and from the Evidence it traces back to.

## Retired or redefined PRD terms

The PRD's Glossary (§3) is frozen and stays authoritative for the product as a whole. These terms
carry a different status for Nuron's first vertical slice specifically:

- **Curator** — out of scope for this slice entirely. Re-curation (PRD FR-7) doesn't happen yet;
  the write-time provenance-ref bookkeeping (see Provenance ref, above) solves the touched-subtree
  problem for initial persistence without needing the Curator's scheduled pass.
- **Default Agent** — retired as a name for this slice. The PRD described one agent handling both
  "ingest" and "reply" over RabbitMQ; the ingest pipeline here is a state machine, not an agent,
  and "reply" (write-back to originating sources) is a separate, deferred v1.1 concept, distinct
  from query answering. See ADR-0001.
- **LLM-Wiki** — superseded for this slice. The PRD's four-section Markdown schema (Executive
  Summary, Core Entities & Relationships, Known Issues & Verified Solutions, Cross-References) had
  no section for Decisions, and `author` appeared in neither it nor the Raw Ingest Agreement
  upstream of it. The Compiler emits structured output with an explicit `decisions[]` instead.
- **Raw Ingest Agreement** — retained, not retired. An earlier pass judged this seam not worth
  keeping (one source, one producer, one consumer). Reinstated once four input formats
  (`.md`/`.txt`/`.docx`/`.pdf`) converging on one Compiler made the heterogeneity the seam exists
  for actually real.
