---
status: accepted
---

# Content hash, not UUID, is document identity and the object storage key

A document's identity is `sha256(bytes)`, and that same digest is its RustFS object key
(`{sha256[0:2]}/{sha256}`) — never a generated UUID. This slice has two ingestion entry points
(watched directory, upload); a UUID-keyed store would let the same bytes arrive through both and
be stored twice, which breaks the dedupe the design depends on (assertion A12) and defeats the
point of content-addressing originals in the first place.

## Considered options

- **UUID per ingested document**, the conventional choice for a storage key — rejected: identity
  would be assigned rather than derived, so two paths ingesting the same bytes produce two
  records, silently.
- **Content hash as identity and key** — accepted: identity falls out of the bytes themselves;
  re-ingesting the same content by either path is a no-op by construction, not by an added
  dedupe check.

## Consequences

Re-keying is not an option later without re-establishing identity for every stored object — this
is effectively permanent once real content exists. Content hash is a `UNIQUE` constraint on the
Landing Zone table with a conflict-safe insert (`ON CONFLICT DO NOTHING` or equivalent), and the
RustFS write for a given key is idempotent or conditional (write-only-if-absent) — both are
required for A12 (concurrent upload and directory-scan of the same bytes converging on one record)
to actually hold, not just for it to usually hold.

A write must be read back and re-hashed before being acknowledged. This catches a failed or
corrupted write *at ack time* — it is not durability and provides no protection against loss
*after* a successful ack (bit rot, a RustFS bug, disk failure). RustFS is Beta and, for uploads,
holds the only copy; that loss window is an accepted risk for this slice, not one this check
closes.

**Permanent orphans are an accepted gap, not a solved one.** Object-write-before-row-insert
(tracer-bullet-01.md, "Write order across the Postgres/RustFS boundary") makes a *crash-window*
orphan harmless — the next arrival of the same bytes just no-ops the write and inserts the row.
It does not reclaim an object whose row insert never happens at all (the watched file is deleted
before the next scan, an upload is never retried). No garbage collection exists in this slice for
that case; a permanently-orphaned object just sits in RustFS, which one 5-file fixture corpus
cannot make expensive enough to matter. Revisit with a reconciliation job (unreferenced-by-`content_hash`,
past a grace period covering in-flight writes) only if real usage accumulates orphans worth
reclaiming.
