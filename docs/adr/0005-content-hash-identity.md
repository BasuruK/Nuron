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
is effectively permanent once real content exists. A write must be read back and re-hashed before
being acknowledged, since content-addressing makes integrity verifiable for free and this is the
only thing standing between a silent storage failure and permanent loss of an uploaded original
(RustFS is Beta and, for uploads, holds the only copy).
