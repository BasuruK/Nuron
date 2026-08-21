---
status: accepted
---

# Neo4j as the property graph store, not Postgres/AGE

Postgres is already in the stack for two schemas (`nuron_ai`, `nuron_api`), so consolidating the
graph into Postgres via Apache AGE — or waiting for native `SQL/PGQ`/`GRAPH_TABLE` support — was a
real option worth evaluating rather than defaulting to Neo4j out of habit. We evaluated it and
rejected it for this slice: Apache AGE's LlamaIndex support does not cover a
`PropertyGraphStore` backend, and native `SQL/PGQ` is committed to PostgreSQL 19 (not 18, GA
around September 2026), which would compile graph traversal to relational joins we have no
evidence yet perform well for this workload. Neo4j ships both the property graph and, via its
native vector index (5.13+), the HNSW index this slice's retrieval design needs in one store.

## Considered options

- **Apache AGE inside the existing Postgres instance** — rejected: no LlamaIndex
  `PropertyGraphStore` implementation exists against it today.
- **Wait for PostgreSQL 19's native graph query support** — rejected for this slice: not GA yet,
  and even once available it's relational joins under the hood, not a graph-native engine; we'd be
  trading a proven graph store for an unproven one on a timeline we don't control.
- **Neo4j** — accepted.

## Consequences

One more datastore to operate (three total: Postgres, Neo4j, RustFS) instead of two. Revisit this
decision once PostgreSQL 19 is GA and this slice has shown which graph queries actually get
issued in practice — that evidence didn't exist when this decision was made.
