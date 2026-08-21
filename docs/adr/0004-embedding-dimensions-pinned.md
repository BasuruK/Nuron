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

Every node stores the embedding model id and dimension count that produced its vector, so a
migration that's only partially run is detectable (mixed model ids) rather than silently producing
an index full of vectors from two incompatible spaces. A write with a model id/dimension pair that
doesn't match the currently configured pair must be rejected at write time, not silently accepted
into the same index. **Queries must filter to the currently configured model id/dimension too**,
not just detect mismatches after the fact — comparing vectors from two incompatible embedding
spaces produces a meaningless similarity score, silently, right inside the ranking A1 depends on.
Revisit the pinned value only if retrieval accuracy proves insufficient — not preemptively, and not
without accepting the re-embed cost above.

Not adopted: separately persisting provider, base URL, model snapshot/version, and an index
generation counter as their own fields. This slice has exactly one configured embedding endpoint
for its whole run — there's no scenario yet where the same model id string could resolve to two
different underlying models, or where "generation" means something `model_id` doesn't already
capture. Revisit if this ever runs multiple providers or a live migration concurrently with
queries; speculative fields for that today would be unused config, not a working safeguard.
