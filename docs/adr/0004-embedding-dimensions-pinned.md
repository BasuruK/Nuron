---
status: accepted
---

# 1024 embedding dimensions, pinned as a one-way door

PRD §5.2 sets only a floor: a general-purpose embedding model, ≥600M parameters, ≥1024-dim output.
For this slice we pinned the exact value — `text-embedding-3-large`, `dimensions=1024` — because
the dimension count is baked into the Neo4j vector index at creation time. Changing the model or
the dimension count later is not a config edit; it is re-embedding every existing node and
rebuilding the index from scratch.

## Consequences

Every node stores the embedding model id that produced its vector, so a migration that's only
partially run is detectable (mixed model ids) rather than silently producing an index full of
vectors from two incompatible spaces. Revisit the pinned value only if retrieval accuracy proves
insufficient — not preemptively, and not without accepting the re-embed cost above.
