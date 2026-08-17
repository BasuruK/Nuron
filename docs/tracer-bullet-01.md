# Tracer Bullet 01 — Document to cited answer

Output of a grilling session, 2026-08-16. Records decisions, the assertions the bullet must
pass, and where those decisions depart from `prd-Nuron-2026-07-08`.

The PRD is frozen. This document does not amend it — it records what supersedes it for this
slice, so Architecture inherits one list instead of re-deriving it.

## 1. Scope

One end-to-end slice: a document lands (watched directory **or** upload) → original preserved in
object storage → format extraction → deterministic parse → **human review and approval** → LLM
compile → **human merge confirmation** → property graph → a query returns a grounded answer
citing a `Decision` node reached by graph traversal.

Single user. No auth, no users, no roles, no config UI, no audit log, no rate limiting, no
tenancy, no supersession, no connectors, no write-back.

**What the bullet proves:** given human-verified input, does graph persistence plus
vector-entry/graph-expand retrieval produce a cited answer that a chunk store could not have
produced — and does human merge confirmation make the entity matcher improve over time?

**What it does not prove:** whether an LLM could extract Decisions accurately without a human
(the gate answers that by fiat); whether the pipeline scales (a human gate on every file is the
bottleneck by design); which edge-type vocabulary reviewers actually need.

## 2. Architecture

Six containers. RabbitMQ is not among them.

| Container | Role in this slice | Exposure |
|---|---|---|
| `nuron-web` | SvelteKit. Upload, review queue, diff view, body editor, merge-candidate confirm, query box. | host |
| `nuron-api` | Laravel. REST facade: upload, review, merge-confirm, query. Owns schema `nuron_api`. | host |
| `nuron-ai` | Python/LlamaIndex. Watcher, extractors, parser, review state, Compiler, persistence, query agent. Owns schema `nuron_ai`. | internal |
| Postgres | One instance, **two schemas**. Queue, Landing Zone records, Reviewed Source versions, aliases. | internal |
| Neo4j | Property graph **and** HNSW vector index. | internal |
| RustFS | S3-compatible object storage. **Original document bytes**, content-addressed. | internal |

Arrows point one way: `web → api → ai → {postgres, neo4j, rustfs}`. `nuron-ai` never calls upward.

**Schema isolation is enforced, not conventional.** `nuron_api` and `nuron_ai` are separate
schemas with separate DB roles that cannot see each other's tables. If `nuron-api` could
`SELECT` from `nuron_ai.review_queue` it eventually would — and the one-way layering becomes a
comment rather than a constraint, with `nuron-ai` unable to migrate a table without breaking
`nuron-api`.

### Flow

```
  watched/*.{md,txt,docx,pdf}                 upload (web → api → ai)
  │  scan every 24h                           │  single request, size-capped
  │  mtime stable for 30s                     │  no mtime rule — bytes arrive complete
  └──────────────┬────────────────────────────┘
                 ▼
        sha256 = identity = object key
                 │
                 ▼
        RustFS: {sha256[0:2]}/{sha256} ──── original bytes, immutable, write-once
                 │                          read back + re-hash before ack
                 ▼
        format extractor (per type) ───────  .md/.txt passthrough
                 │                           .docx → DocxReader
                 │                           .pdf  → LlamaParse (configurable, not default)
                 ▼
        common form: markdown ────────────── every format converges here
                 │  deterministic parser: frontmatter → in-prose signature → filename date
                 ▼
        awaiting_review ─────────────────── BLOCKS. every file. no exceptions.
                 │  human: edits body freely, fills/corrects Content Header, approves content
                 ▼
        Reviewed Source vN ──────────────── frozen at approval, versioned. THE EVIDENCE ROOT.
                 │  Compiler (LLM, agent 1) — SchemaLLMPathExtractor, typed triples
                 │  + enrichment: parser-derived props, provenance refs
                 ▼
        awaiting_merge_confirm ──────────── BLOCKS. auto-joins shown as info; merges asked.
                 │  human: confirms same-referent merges (≤5, default "not the same")
                 ▼
        persist ─────────────────────────── natural-key + alias match, three-bucket delta,
                 │                          refcounted provenance, embed on add/update
                 ▼
        Neo4j  ◄──── query agent (agent 2): VectorContextRetriever → generate → SSE
```

**Two agents in v1**: Compiler and query agent. The parser and extractors are code. The Curator
is out of scope. "Default Agent" is retired as a name — the ingest pipeline is a state machine.

**Pipeline states**: `landed → extracted → parsed → awaiting_review → content_approved →
compiled → awaiting_merge_confirm → persisted`. One Postgres table in `nuron_ai`. The review
queue *is* the work queue.

`extracted` is its own state because LlamaParse is a network call with independent failure and
retry semantics. Both blocking states are **real persisted states**, not UI steps — a reviewer
may close the browser between them, and restart-safety requires the pipeline resume where it
paused.

## 3. Decisions

| # | Decision |
|---|---|
| 1 | Retire knowledge risk, not topology risk. (Amended by 6 and 8 — see §5 scope note.) |
| 2 | Citation = `Decision` node with author, timestamp, resolving Evidence edge. Not a filename. |
| 3 | Compiler emits **structured output** with an explicit `decisions[]`, not a Markdown document. |
| 4 | `author` extracted from content, configured fallback, plus `author_source: extracted \| default \| unknown`. Config value named `source_owner`, never `author`. |
| 5 | Pass 1 is **deterministic code**, not an LLM: format extractor → common markdown form → header parse. |
| 6 | **Every file blocks for human review.** Reviewers may edit body content, not just metadata. |
| 7 | Reviewed Source is the **Evidence root**, not the raw file. Original reachable one hop back (two for pdf/docx). |
| 8 | `nuron-ai` owns the pipeline and its state. `nuron-api` owns the user-facing surface. |
| 9 | **RabbitMQ deleted.** Postgres is the queue. Neo4j stays; Postgres does not replace it. |
| 10 | "Reply" = v1.1 write-back to originating sources. Not query answering. §4.4 owns UJ-2 alone. |
| 11 | Retrieval: **HNSW entry → graph expand** = `VectorContextRetriever(path_depth=1)`. No BM25, no RRF, no LLMRerank. |
| 12 | Embeddings live in **Neo4j**, on the graph nodes. Not pgvector — no dual-write. |
| 13 | Scheduled scan (not `inotify`), **content hash for identity** — and the hash is the object key. |
| 14 | Re-approve **replaces** the document's provenance slice. Provenance is a **set**; refcounted. |
| 15 | Node identity across compiles: **natural key** `normalize(name) + label`, extended by human-confirmed **aliases**. |
| 16 | Relationship discovery at ingest: **cosine + normalised-name overlap** as candidate signals. Hop expansion deferred. |
| 17 | Human confirms **merges** ("same thing?"), not links. Auto-joins shown as information, never asked. Link creation deferred to v1.1. |
| 18 | Originals live in **RustFS**, content-addressed by sha256 — **not UUID**, which would break dedupe. Read back and re-hash before acking a write. |
| 19 | **Upload is a second ingestion entry point.** Single request, size-capped. Multipart/resumable deferred. |
| 20 | v1 formats: **`.md`, `.txt`, `.docx`, `.pdf`**. No `.doc`. **No OCR** — near-empty extraction is a hard failure, never a silent empty review item. |
| 21 | **LlamaParse for `.pdf`** — a configurable extractor, **never the shipped default** (§5.1). |
| 22 | The bullet's fixture is `.md`/`.txt`; **A11** proves the PDF seam without blocking A1–A10. |

### Configuration

| Item | Value | Note |
|---|---|---|
| LLM | `OpenAILike` — configurable `base_url`, `api_key`, `model` | Not `OpenAI`; compatible endpoints need explicit `is_chat_model` / `is_function_calling_model` flags. **Startup check must verify structured output works** — `SchemaLLMPathExtractor` depends on it. Fail loudly at boot, not at 3am on malformed triples. |
| Embeddings | `text-embedding-3-large`, `dimensions=1024` | Exactly §5.2's floor. **One-way door**: dimension is baked into the Neo4j vector index; changing it means re-embedding everything and rebuilding the index. Store the embedding model id **on each node** so a partial migration is detectable. Revisit only if accuracy drops. |
| Object storage | RustFS, S3 API, via `fsspec`/`s3fs` | LlamaIndex already depends on `fsspec`, so the backend is a URI. Keeps MinIO/S3 as drop-in alternatives if RustFS's Beta bites. |
| PDF extractor | LlamaParse, `result_type="markdown"` | **Off by default in shipped config.** Returns markdown, so PDFs converge on the same header parse as `.md`. |
| Scan interval | 24 hours | Directory ingest only; uploads are immediate. |
| mtime stability window | 30 seconds | **Decoupled from scan interval.** FR-1 ties them together; at a 24h interval that would mean ~48h worst case from drop to review queue. |
| Parser rules | frontmatter `author:`/`title:`/`tags:`/`date:` → in-prose signature regex (`— Name, YYYY-MM-DD`) → filename date prefix | **Never file mtime** — that's the file's date, not the decision's. Everything unmatched is left blank for the reviewer. |
| Merge candidates | ≤5, gated on a precision bar, **default "not the same"** | Show nothing rather than weak candidates. Fatigue must produce the safe outcome. Suppress anything the natural key already auto-joined. |

### LlamaIndex surface

Use the library, don't rebuild it:

- **`VectorContextRetriever(store, similarity_top_k=k, path_depth=1)`** — this *is* decision 11.
  Vector hit, then follow relations to depth N. Wired via
  `index.as_retriever(sub_retrievers=[vector_retriever])`. Retrieval is configured, not built.
- **`SchemaLLMPathExtractor`** — `possible_entities=Literal["DECISION","ENTITY","EVIDENCE"]`,
  `possible_relations=Literal["SUPERSEDES","EVIDENCED_BY","AFFECTS","DEPENDS_ON","PART_OF"]`,
  `kg_validation_schema={...}`, `strict=True`. Pydantic-validated typed triples — decision 3's
  guardrail enforced by the library rather than by prompt discipline.
- **`file_extractor` dict** on `SimpleDirectoryReader` — the format seam. `DocxReader` for
  `.docx`; LlamaParse registered against `.pdf` when configured. Defaults cover `.pdf`, `.docx`,
  `.pptx`, `.hwp` — **`.doc` is not in the map** and would need LibreOffice.
- Known, deliberately unused: `DynamicLLMPathExtractor`, `ImplicitPathExtractor`,
  `LLMSynonymRetriever` (cheaper stand-in for the deferred BM25 source), `TextToCypherRetriever`
  (powerful, and a genuine injection surface — not in the bullet), `PyMuPDFReader`.

**Two integration seams the library does not cover:**

1. `SchemaLLMPathExtractor` yields typed nodes and edges. It does **not** carry `author`,
   `author_source`, `timestamp`, `evidence_span`, or provenance ref-sets. `allowed_entity_props`
   is a list of permitted property *names*, not a typed validated model — and `author_source`
   originates in the **parser**, which the extractor never sees. Decisions 4, 14 and 15 need a
   custom extractor subclass or a post-extraction enrichment step that stamps these before
   `insert_nodes()`.
2. `PropertyGraphIndex.from_documents()` does chunk→extract→persist in one shot. This pipeline
   splits those across two human gates, so drive the extractor and `insert_nodes()` directly.

## 4. Fixture and assertions

Four files, `.md`. A one-file graph is entirely within vector reach and would let the bullet pass
without traversing anything.

- **A** — the decision. *"Dropping server-side sessions for stateless JWT... — Basuru, 2026-05-14"*
- **B** — an entity doc. *"The rate limiter keys off the session store."* Does not mention JWT,
  sessions being dropped, or the decision.
- **C** — noise. Superficially similar (tokens, auth, performance), semantically unrelated.
- **D** — the alias case. *"The sessions table is replicated nightly."*
- **E** — a one-page PDF carrying the same decision as **A**. Used only by A11.

Query: **"what did the auth change affect?"** The answer is *the rate limiter*, in **B**,
reachable only via `Decision(A) → Entity(session store) → Entity(rate limiter)`.

| # | Assertion |
|---|---|
| A1 | The query returns the rate limiter, citing a node contributed by **B**, reached by traversal. |
| A2 | The cited Decision node: `label=Decision`, `author="Basuru"`, **`author_source="extracted"`**, `timestamp=2026-05-14`, `EVIDENCE` edge resolves to Reviewed Source A-v1. |
| A3 | **Control** — same query, graph expansion disabled (`path_depth=0`). Must degrade or fail. If it passes, the graph contributed nothing and the bullet's finding is negative. |
| A4 | A file cannot reach `compiled` without content approval, nor `persisted` without merge confirmation. |
| A5 | Re-scan with unchanged hash is a no-op: no re-extract, no re-parse, no re-review, no LLM call. |
| A6 | Change A, re-review, approve v2 → node count stays *N*, not 2*N*; content reflects v2; nodes contributed only by B, C, D are untouched. |
| A7 | The *session store* entity — referenced by both A and B — survives removal of A-v1's provenance ref. |
| A8 | A query with no matching Decision returns an explicit "no matching decision" citing nearest entities. Never a fabricated answer. |
| A9 | Ingest A, then B. `Entity(session store)` is a **single node** with provenance refs to both Reviewed Sources, and A1's traversal path exists **without any human link step**. |
| A10 | Ingest **D**. The reviewer is offered `sessions table` ≈ `session store` as a merge candidate; on confirm, one node survives carrying `sessions table` as an **alias**, with refs to A, B and D. Re-ingesting D is then a no-op — the alias matches without asking again. |
| A11 | **Non-blocking.** PDF **E** extracts via LlamaParse to markdown, and the deterministic header parse finds the same Subject, author and date it finds in **A**. |
| A12 | An uploaded document and the same document dropped in the watched directory produce **one** Landing Zone record and **one** RustFS object — same hash, same key. |

**Why these specific assertions carry weight:**

- **A2's `author_source="extracted"`** is what stops A2 passing by copying a config value into a
  node property.
- **A3** converts "it worked" into "it worked *because of the graph*".
- **A7** is the reference-counting bug a one-file fixture would never catch.
- **A9** proves the cross-document join mechanism: the shared entity name *is* the join, produced
  by the natural key with no human involvement. This is why A1 can pass on compiler output alone.
- **A10** proves the compounding property. Without it you've built merge-on-confirm and never
  verified it *learns*. Every confirmed merge extends `normalize(name) → node` to
  many-names-per-node, so the matcher improves monotonically from human input — no threshold, no
  tuning, no regression. This is the only part of the system that gets better as it is used.
- **A11 is deliberately non-blocking.** A1–A10 must not depend on a cloud service; if they went
  red because LlamaCloud timed out, the bullet would have told you nothing.
- **A12** proves the two entry points converge on one identity. Without it, uploading a file you
  already ingested silently duplicates everything downstream.

## 5. PRD statements this supersedes

| PRD | Says | Superseded by |
|---|---|---|
| §5.4 | `nuron-api` reachable only on the internal Docker network | `web` + `api` exposed to host; `ai`, Postgres, Neo4j, RustFS internal. This is F-1's own recommended fix. |
| §4.5, FR-10 | Default Agent handles ingest and reply; "Realizes UJ-2" | "Reply" is v1.1 write-back. §4.4 owns UJ-2 alone. "Default Agent" retired as a name. Resolves F-2 and F-3. |
| §3, FR-3 | LLM-Wiki is a *Markdown document* with four sections | Structured output with an explicit `decisions[]`. **The four-section schema had no slot for the Decision node, and `author` appeared nowhere in either upstream schema — a write-side hole the adversarial review did not find.** |
| FR-2 | Structuring agent (LLM) normalises into the Raw Ingest Agreement; failures *flagged* for admin review | Deterministic extractor + parser; **every** file gated. FR-2's near-zero-temperature `[ASSUMPTION]` deleted along with the LLM pass. **The Raw Ingest Agreement seam is retained** — see the note below. |
| FR-1 | *"Files must be UTF-8 plain Markdown with optional YAML frontmatter"* | `.md`, `.txt`, `.docx`, `.pdf`. Format extraction converges all of them on markdown before the header parse. |
| FR-1 | Ingestion is a scheduled scan of a configured directory | Two entry points: scheduled scan **and** upload. Uploads skip the mtime-stability rule (bytes arrive complete) and have no second copy on disk. |
| FR-1 | A file is not read until mtime has been stable for **one scan interval** | Stability window (30s) decoupled from scan interval (24h). Coupled, a 24h interval means ~48h worst case from drop to queue. |
| FR-4 | "RabbitMQ is a required data-plane component, not optional" | Postgres queue. A committed row satisfies restart-safety more strongly than an unacked message, and the human gate makes the pipeline human-paced, so broker throughput is moot. Critically: the review queue *is* the work queue — RabbitMQ would be a second copy of the same state. |
| FR-7 | Touched-subtree curation is conditional on the Merkle-style subtree indexing hypothesis | Unnecessary **for the write path** — provenance refs give the touched set directly. FR-7's Merkle question remains open for *scheduled re-curation* only. |
| §3, glossary | Evidence is the raw source content | Evidence roots at the Reviewed Source. Human-vouched content is better evidence than raw. Original reachable one hop back — **two** for pdf/docx, since extracted text sits between. |
| FR-3 | "Duplicate or near-duplicate raw content compiles to a single Core Entity, not two" | Partly satisfied. Natural key catches identical names; human-confirmed merge + aliases catch different names. **Unconfirmed near-duplicates still split** — no automatic threshold in this bullet. |
| FR-3, FR-6 | Candidate merges / contradictions are *"queued for admin review"* | Moved **earlier**, to the moment the reviewer is already reading the document. Same confirmation, no separate queue to ignore. |
| §6 | *"Non-textual content — text only in v1"* | Restated: **text-bearing** documents. No OCR. A scanned PDF extracts to near-nothing and must **fail loudly**, never reach a reviewer as an empty item. |

**Note on the Raw Ingest Agreement seam.** This document originally deferred it (one source, one
producer, one consumer — the seam wasn't earning its keep). Four input formats feeding one
Compiler *is* the heterogeneity that seam exists for, so it is retained: per-format extractor →
common markdown form → deterministic header parse. Reversed on new information.

**Note on §5.1 and LlamaParse.** §5.1 promises *"the only outbound traffic is whatever the
customer explicitly configures — LLM provider, embedding provider."* LlamaParse is a third-party
cloud service, so it is a **configurable extractor that is off by default in shipped
configuration**. Enabled for development and testing against our own fixture documents. A
customer deployment must not route documents to it without explicit, informed configuration, and
no real customer document should pass through it in a demo.

**Scope note on decision 1.** The bullet was scoped to two containers to retire knowledge risk.
Decisions 6 and 8 grew it to five containers plus a review workflow; decision 17 added a second
human gate; decisions 18–19 added a sixth container and a second entry point. Accepted knowingly —
the enterprise-accuracy thesis was judged to outrank slice size. The cost: this bullet will not
tell you whether Nuron works without a human on every file, and v1.1's gate-on-low-confidence
work will have to answer that cold.

## 6. Deliberate gaps

Marked here so they are deferred rather than forgotten.

- **Local PDF extraction for production.** LlamaParse is dev/test only under §5.1. A local path
  (`PyMuPDFReader`, `UnstructuredReader`) or OCR is needed before a customer deployment parses
  PDFs. **This is on the critical path to shipping, not optional polish.**
- **OCR / scanned PDFs** — out. Hard-fail on near-empty extraction.
- **`.doc` (legacy binary)** — out. No LlamaIndex reader; needs LibreOffice.
- **Multipart / resumable upload** — out. Single request, size-capped.
- **Page-anchored citations** ("jump to page 4") — needs page metadata captured at extraction.
  Check whether LlamaParse preserves page boundaries; if it does this is nearly free, and it is
  much cheaper now than retrofitted after documents are ingested.
- **Automatic near-duplicate merge** (FR-3) — no cosine threshold in this bullet. Human
  confirmation substitutes. A threshold needs corpus volume to calibrate.
- **Reviewer's edits are lost on re-ingest.** Mitigation: show raw-v1→raw-v2 diff alongside the
  reviewer's prior edits; they re-apply manually. Three-way merge deferred. **This is the workflow
  that decides whether reviewers engage or start rubber-stamping — watch it.**
- **Link creation (typed, directed edges by hand)** — deferred to v1.1. `possible_relations` is a
  closed `Literal`; get the vocabulary wrong and reviewers either can't express the link they see
  or pick the first option. **Log every "I want to link these but there's no type for it" moment
  and let that log design the vocabulary** — don't design it from imagination.
- **Hop-expansion candidate signal** — with five documents, 1–2 hops returns roughly the whole
  graph. Untestable now.
- **Gate-on-low-confidence-only** (FR-2 as written) — v1.1.
- **Supersession** (FR-6), **Curator re-curation** (FR-7), **BM25/Tags + RRF + LLMRerank** (FR-8),
  **write-back** — all deferred.
- **Postgres as the single datastore** — revisit once PG19 is GA (~Sept 2026) and this bullet has
  told you which graph queries you actually issue. SQL/PGQ compiles to relational joins and there
  is no LlamaIndex Postgres/AGE `PropertyGraphStore`.

## 7. Still open

1. **Precision bar for merge candidates** — the cosine floor below which nothing is shown.
   Needs calibration against a real corpus; pick a conservative starting value and log rejections.
2. **LlamaParse credit accounting** — credits are consumed per page and vary by parse mode, so
   verify the headroom against current pricing rather than assuming a per-document rate.
3. **Upload size cap** — pick a number.
4. **RustFS maturity watch.** Beta; its own README marks distributed mode, lifecycle management
   and KMS *"Under Testing."* Accepted knowingly. Because storage goes through `fsspec`, MinIO or
   S3 remain drop-in alternatives if it bites. **Uploaded documents have no second copy** — the
   read-back-and-re-hash check on write is the only thing standing between a silent write failure
   and permanent loss of an original.
