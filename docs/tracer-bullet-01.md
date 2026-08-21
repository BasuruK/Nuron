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
tenancy, no supersession, no connectors, no write-back. **Because auth is off, `nuron-web` and
`nuron-api` bind loopback only (`127.0.0.1`) and this slice is local-only** — never published on
`0.0.0.0` or a LAN/WAN interface.

**This is a host-published-port restriction, not a container-networking rule.** It governs the
compose `ports:` host bind address (`127.0.0.1:PORT:PORT`) — what's reachable from outside the
Mac. Containers still bind their listener to their own container interface and reach each other
over the compose network by **service name** (`nuron-api` calls `http://nuron-ai:PORT`, never
`127.0.0.1` — inside a container, `127.0.0.1` is that container's own loopback, not another
container's). A provider running directly on the host (not in compose) needs the runtime's
host-gateway hostname (`host.docker.internal`, which OrbStack also supports) from inside the
container — `127.0.0.1` in a container's `base_url` only reaches something sharing that same
container's network namespace.

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
| `nuron-web` | SvelteKit. Upload, review queue, diff view, body editor, merge-candidate confirm, query box. | loopback (`127.0.0.1`) |
| `nuron-api` | Laravel. REST facade: upload, review, merge-confirm, query. Owns schema `nuron_api`. | loopback (`127.0.0.1`) |
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
  watched/**/*.{md,txt,docx,pdf}              upload (web → api → ai)
  │  scan every 24h, recursive                │  single request, 25 MB cap
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

**Scan is recursive.** Extension filter unchanged (`.md`, `.txt`, `.docx`, `.pdf`). Cadence
unchanged (24h, mtime stable 30s). **Acceptance:** a supported file at
`watched/nested/dir/file.md` is discovered within one scan interval and processed the same as a
top-level file; unsupported extensions in nested dirs are ignored.

**Identity / concurrent ingest.** `content_hash` (sha256) is unique on the Landing Zone table.
Inserts are conflict-safe (`ON CONFLICT DO NOTHING` or equivalent): concurrent upload and scan of
the same bytes create one row. RustFS write is idempotent or conditional (If-None-Match / write
only if absent); a second writer leaves the existing immutable object untouched. A12 is the
acceptance case.

**Write order across the Postgres/RustFS boundary**: object write first, then the Landing Zone row.
A crash between them leaves an unreferenced RustFS object and no row — harmless, since the next
scan or upload of the same bytes just writes-or-no-ops the object again and then inserts the row.
The reverse order (row first) would leave a row referencing an object that was never confirmed
written, which a downstream reader can't safely treat as landed. There is no cross-store
transaction; this ordering is what makes a half-landed pair harmless instead of a dangling
reference.

**Same hash, different filename.** A second arrival of already-known bytes is a no-op per A5/A12
regardless of filename — the second filename is never consulted, including for the parser's
filename-date-prefix fallback. **Insert before parsing, not after** — a bare row keyed by
`content_hash` (`ON CONFLICT DO NOTHING`, `RETURNING`) lands first, with no metadata yet; only the
arrival whose insert actually returns a row (i.e., wins the race) goes on to parse, using its own
filename, and fills in the metadata with an `UPDATE`. A losing concurrent arrival's insert returns
nothing, so it never parses at all — there is no discarded parse result, and no window where two
different filenames could each produce a different stored date for the same hash. Which of several
*simultaneous* arrivals wins the insert isn't predictable in advance, but exactly one deterministic
outcome is written and nothing is silently re-derived later. A12's concurrent-arrival case extends
to this: two different date-prefixed filenames, same bytes, neither carrying an in-content date —
the row's date comes from whichever filename won the insert, once, and that choice is never revisited.

**Reviewed-source contract (Compiler input).** FR-2's four-section Raw Ingest Agreement *schema*
(Content Header / Content / Key Discoveries / Tags as a required document shape) is **superseded
for this slice**. The Compiler consumes a human-approved Reviewed Source: Content Header
(Subject/`title`, Date, author + `author_source`; Reason left blank if unmatched) plus the
reviewer-edited body. Tags remain a parser/reviewer field (`tags:` frontmatter → reviewer
correction; empty list allowed). Key Discoveries is not a required section — `decisions[]` from
the Compiler replaces that slot. The Raw Ingest Agreement *seam* is what is retained (per-format
extractor → common markdown → header parse). Compilation must not start until Content Header and
body are present on the approved version.

**Two agents in v1**: Compiler and query agent. The parser and extractors are code. The Curator
is out of scope. "Default Agent" is retired as a name — the ingest pipeline is a state machine.

**Pipeline states**: `landed → extracted → parsed → awaiting_review → content_approved →
compiled → awaiting_merge_confirm → persisted`, plus **`failed`** — a terminal state reachable
from any automated transition that exhausts its retries (see Attempts). One Postgres table in
`nuron_ai`. The review queue *is* the work queue.

**Worker claim / lease.** A worker claims a row by setting `claimed_by` + `lease_until` +
incrementing `lease_token` in the same `UPDATE` that selects it (`WHERE state IN (…) AND
(lease_until IS NULL OR lease_until < now()) AND (next_attempt_at IS NULL OR next_attempt_at <=
now())` — dropping the backoff clause would let a worker immediately re-claim a row that just
soft-failed and is still waiting out its `next_attempt_at`). The state-transition `UPDATE` itself
is conditioned on `WHERE claimed_by = <worker> AND lease_token = <token>`, so a zombie worker whose
lease was reclaimed cannot advance the row's state out from under the worker that actually holds
the lease now.

**That Postgres-level fence does not, by itself, fence the RustFS or Neo4j writes** — those systems
know nothing about `lease_token`. It doesn't need to: the RustFS write is already
idempotent/conditional by content hash (a zombie's write is either identical and a no-op, or loses
a benign write-once race — never a corruption). The Neo4j write runs `plan_delta` against the
*live* graph state at write time, not a cached snapshot, so a zombie's persist is self-correcting —
the worker that actually wins the state-transition race computes its delta against whatever is
already there, including anything the zombie wrote, rather than blindly overwriting it. This falls
short of a true cross-store fencing guarantee (an idempotency key enforced by RustFS/Neo4j
themselves, or a durable outbox with reconciliation) — accepted as a gap for this slice, given a
single-worker-in-practice, human-gated pipeline; revisit if this ever runs multiple workers for
real. Crashes during an external call (LlamaParse at `extracted`, LLM at `compiled`, embeddings at
`persist`) expire the lease; another worker resumes from the persisted state, not from scratch.
`extracted` is independently claimable — a LlamaParse failure does not rewind `landed`.

**Attempts.** Each automated transition stores `attempt_count` and `next_attempt_at` (backoff).
Soft-fail retries until `attempt_count` hits a persisted limit (default 5); then state becomes
`failed` (terminal for workers — no claim predicate selects a `failed` row — visible as
needs-operator). Expired leases look like unclaimed rows (`lease_until < now()`) and are resumed by
the next claim. Exhausted retries do **not** auto-resume: an operator clears `attempt_count` and
resets the row to its last retryable state to give it another pass. No in-process-only work: the
row is the checkpoint.

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
| 11 | Retrieval: **HNSW entry → graph expand** = `VectorContextRetriever(path_depth=2)`. No BM25, no RRF, no LLMRerank. |
| 12 | Embeddings live in **Neo4j**, on the graph nodes. Not pgvector — no dual-write. |
| 13 | Scheduled **recursive** scan (not `inotify`), **content hash for identity** — and the hash is the object key. Extension filter unchanged. |
| 14 | Re-approve **replaces** the document's provenance slice. Provenance is a **set**; refcounted. |
| 15 | Node identity across compiles: **natural key** `normalize(name) + label`, extended by human-confirmed **aliases**. |
| 16 | Relationship discovery at ingest: **cosine + normalised-name overlap** as candidate signals. Hop expansion deferred. |
| 17 | Human confirms **merges** ("same thing?"), not links. Auto-joins shown as information, never asked. Link creation deferred to v1.1. |
| 18 | Originals live in **RustFS**, content-addressed by sha256 — **not UUID**, which would break dedupe. Unique constraint on Landing Zone `content_hash`; conflict-safe insert; RustFS write idempotent or conditional. Read back and re-hash before acking a write. |
| 19 | **Upload is a second ingestion entry point.** Single request, size-capped at **25 MB**. Multipart/resumable deferred. |
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
| LlamaIndex | `llama-index==0.14.23` (`llama-index-core==0.14.23`) | Pins `VectorContextRetriever.path_depth` as relation hops after the vector hit (library default is 1 = one triple). Required so A1's two-hop path is reproducible. |
| Upload size cap | 25 MiB (raw file bytes) | `upload_max_filesize=25M` caps the file itself — PHP's `M` ini shorthand is already binary (25×1024×1024 bytes = 25 MiB), matching Laravel's `max:25600` KB (25600×1024 bytes = 25 MiB) exactly. `post_max_size=26M` leaves 1 MiB headroom for multipart boundaries and other form fields, so PHP rejects an oversized file at the PHP layer — before temp-file allocation and memory use for multipart parsing — rather than after Laravel finishes parsing it. Setting both PHP limits equal would let a file in the 25–26 MiB window be fully parsed and buffered before Laravel's validation ever ran, defeating the point of an ini-level cap. Rejects the request before the upload handler or `nuron-ai` extraction. Single request; multipart deferred. |

### LlamaIndex surface

Use the library, don't rebuild it:

- **`VectorContextRetriever(store, similarity_top_k=k, path_depth=2)`** — this *is* decision 11.
  Vector hit, then follow two relation hops (`Decision → session store → rate limiter`). Wired via
  `index.as_retriever(sub_retrievers=[vector_retriever])`. Retrieval is configured, not built.
  Pin `llama-index==0.14.23` (`llama-index-core==0.14.23`) so `path_depth` stays "hops after the
  vector hit".
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

Five fixtures: four Markdown files and one PDF. A one-file graph is entirely within vector reach and would let the bullet pass
without traversing anything.

- **A** — the decision. *"Dropping the session store for stateless JWT... — Basuru, 2026-05-14"*
- **B** — an entity doc. *"The rate limiter keys off the session store."* Does not mention JWT,
  sessions being dropped, or the decision.
- **C** — noise. Superficially similar (tokens, auth, performance), semantically unrelated.
- **D** — the alias case. *"The sessions table is replicated nightly."*
- **E** — a one-page PDF carrying the same decision as **A**. Used only by A11.

A and B both name `session store` identically, so A9 auto-joins on the natural key. D names
`sessions table` — a merge candidate, never an auto-join. Candidate signals (decision 16): cosine
similarity and normalised-name overlap on `session`.

Query: **"what did the auth change affect?"** The answer is *the rate limiter*, in **B**,
reachable only via `Decision(A) → Entity(session store) → Entity(rate limiter)`. Vector search
retrieves A (JWT) and must not independently retrieve B (no JWT).

| # | Assertion |
|---|---|
| A1 | The query returns the rate limiter, citing a node contributed by **B**, reached by traversal. |
| A2 | The cited Decision node: `label=Decision`, `author="Basuru"`, **`author_source="extracted"`**, `timestamp=2026-05-14`, `EVIDENCE` edge resolves to Reviewed Source A-v1. |
| A3 | **Control** — same query, graph expansion disabled (`path_depth=0`). Vector hits must include A and exclude B (the rate limiter is not independently retrievable). Must degrade or fail. If it passes, the graph contributed nothing and the bullet's finding is negative. |
| A4 | A file cannot reach `compiled` without content approval, nor `persisted` without merge confirmation. |
| A5 | Re-scan with unchanged hash is a no-op: no re-extract, no re-parse, no re-review, no LLM call. |
| A6 | Change A with a **property-only or relationship-only** edit (no added/removed nodes), re-review, approve v2 → node count stays *N*, not 2*N*; provenance unique to A-v1 is removed; content reflects v2; nodes contributed only by B, C, and D are untouched. |
| A7 | The *session store* entity — referenced by both A and B — survives removal of A-v1's provenance ref. |
| A8 | A query with no matching Decision returns an explicit "no matching decision" citing nearest entities. Never a fabricated answer. |
| A9 | Ingest A, then B. `Entity(session store)` is a **single node** with provenance refs to both Reviewed Sources, and A1's traversal path exists **without any human link step**. |
| A10 | Ingest **D**. Candidate signals (decision 16): cosine + normalised-name overlap (`session`); names differ, so this is not an auto-join. The reviewer is offered `sessions table` ≈ `session store` as a merge candidate; on confirm, one node survives carrying `sessions table` as an **alias**, with refs to A, B and D. Re-ingesting D is then a no-op — the alias matches without asking again. |
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
- **A11 is deliberately non-blocking.** A1–A10 must not depend on LlamaParse/LlamaCloud; if they
  went red because LlamaCloud timed out, the bullet would have told you nothing. They still need
  an LLM and an embedding provider — see the offline profile below.
- **A12** proves the two entry points converge on one identity. Without it, uploading a file you
  already ingested silently duplicates everything downstream.

**Offline acceptance profile (A1–A10).** A1–A10 run with no cloud: `OpenAILike` pointed at a
loopback-compatible local LLM (`is_chat_model` / `is_function_calling_model` set; the startup
structured-output check still required — reachable via a compose service name or the host-gateway
hostname per the networking note above, not necessarily literal `127.0.0.1`) and embeddings
replaced by a local 1024-dim model **or** a deterministic test double. **The test double must be
golden, not generically derived** — a single constant vector for every input collapses the HNSW
ranking A1/A3 depend on, and a generic hash or keyword-overlap feature vector isn't good enough
either: C is deliberately designed to share surface tokens (auth/token/performance) with the real
decision while being semantically unrelated, so a naive similarity formula can rank C where it
shouldn't. Assign each fixture file (A/B/C/D) **and the query itself** a fixed, hand-picked
1024-dim vector — same input always produces the same vector — chosen so that at
`similarity_top_k=3`, vector search alone ranks **A** top and does **not** surface **B** within the
top 3 (forcing A1 through the graph edge, which is the entire point of A3's control), while **C**
ranks below A but inside the top 3, as a plausible near-miss. `k=3` against a 4-document corpus is
deliberately tight — large enough to admit a near-miss, small enough that including everything
would prove nothing about ranking.

**This same contract applies to the local-model branch, not only the golden test double.** Whichever
you use for a given run, the offline profile is not trustworthy until this exact assertion —
`similarity_top_k=3`, **A** in, **B** out, **C** in but ranked below **A** — has been checked
against it directly and passed, *before* running A1's full retrieve-then-traverse evaluation on top
of it. A real local embedding model is not guaranteed to reproduce this ranking on a four-document
corpus by default; treat that check as a precondition gate, not an assumption. Whichever producer
is used, it stores its own `model_id` (ADR-0004) — the golden double never claims to be the real
model's id. The offline test suite must assert the ordering explicitly, for whichever branch ran —
not just that the final answer happens to come out right, since a lucky final answer with the
wrong ranking underneath proves nothing about A1/A3. Dimension stays 1024 (one-way door; store the
model id on each node). The local LLM's outputs must also be
deterministic across runs — pin a fixed seed/temperature=0 local model with golden-tested expected
output, or serve canned fixture responses — before treating A1–A10 as reliable with it in the loop;
a model that varies its output run-to-run makes the offline profile flaky rather than
trustworthy. LlamaParse stays off. Boot with `OFFLINE=1` (or equivalent) refuses any LLM or
embedding endpoint that isn't a configured compose service name, the container runtime's
host-gateway hostname, or literal loopback — i.e. anything resolving to a public address — never a
literal-`127.0.0.1`-only check, which would wrongly reject a local LLM running as its own compose
service (reached by service name, not loopback; see the networking note above). Required
providers for this profile: local OpenAI-compatible LLM + local 1024-dim embedder or test double.
A11 remains the only assertion that may call LlamaCloud.

## 5. PRD statements this supersedes

| PRD | Says | Superseded by |
|---|---|---|
| §5.4 | `nuron-api` reachable only on the internal Docker network | `web` + `api` bound to host loopback only (`127.0.0.1`); `ai`, Postgres, Neo4j, RustFS internal. This is F-1's own recommended fix, tightened because auth is off. |
| §4.5, FR-10 | Default Agent handles ingest and reply; "Realizes UJ-2" | "Reply" is v1.1 write-back. §4.4 owns UJ-2 alone. "Default Agent" retired as a name. Resolves F-2 and F-3. |
| §3, FR-3 | LLM-Wiki is a *Markdown document* with four sections | Structured output with an explicit `decisions[]`. **The four-section schema had no slot for the Decision node, and `author` appeared nowhere in either upstream schema — a write-side hole the adversarial review did not find.** |
| FR-2 | Structuring agent (LLM) normalises into the Raw Ingest Agreement; failures *flagged* for admin review | Deterministic extractor + parser; **every** file gated. FR-2's near-zero-temperature `[ASSUMPTION]` deleted along with the LLM pass. **The Raw Ingest Agreement *seam* is retained; the four-section schema is superseded** — see the notes below. |
| FR-1 | *"Files must be UTF-8 plain Markdown with optional YAML frontmatter"* | `.md`, `.txt`, `.docx`, `.pdf`. Format extraction converges all of them on markdown before the header parse. |
| FR-1 | Ingestion is a scheduled scan of a configured directory | Two entry points: scheduled scan **and** upload. Uploads skip the mtime-stability rule (bytes arrive complete) and have no second copy on disk. Scan is recursive; extension filter unchanged. |
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
common markdown form → deterministic header parse. Reversed on new information. The *schema*
FR-2 required (Content Header / Content / Key Discoveries / Tags, all four present before
compile) is superseded — see **Reviewed-source contract** above. Tags survive as a field; Key
Discoveries does not.

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
- **Multipart / resumable upload** — out. Single request, size-capped at 25 MB.
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
3. **RustFS maturity watch.** Beta; its own README marks distributed mode, lifecycle management
   and KMS *"Under Testing."* Accepted knowingly. Because storage goes through `fsspec`, MinIO or
   S3 remain drop-in alternatives if it bites. **Uploaded documents have no second copy** — the
   read-back-and-re-hash check on write is the only thing standing between a silent write failure
   and permanent loss of an original.
