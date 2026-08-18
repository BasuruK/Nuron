---
status: accepted
---

# Embeddings live on Neo4j graph nodes, not in pgvector

Retrieval for this slice is HNSW-entry-then-graph-expand: a vector hit *is* a graph node, and
expansion follows edges from it. We store embeddings directly on Neo4j nodes
(`Neo4jPropertyGraphStore(embed_kg_nodes=True)`) rather than splitting them into pgvector, even
though Postgres is already in the stack. A split store means every persist writes twice, and a
half-landed pair (embedding written, node write failed, or vice versa) leaves a vector pointing at
a node that doesn't exist — silently, and directly on the only retrieval path this slice has.

## Considered options

- **pgvector alongside Neo4j** (embeddings in Postgres, graph structure in Neo4j) — rejected: two
  writes per persisted node instead of one, with no transaction spanning both stores to make a
  half-landed pair impossible.
- **Embeddings on Neo4j nodes** — accepted: one store, one identifier, one write.

## Consequences

Neo4j is now load-bearing for both graph traversal and vector search — there is no fallback store
if its vector index underperforms at scale. The embedding model id is stored on every node
specifically so a partial migration to a different model is detectable rather than silently mixed
into the same index (see ADR-0004).
