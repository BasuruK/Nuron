# /to-spec handoff prompt

Paste the block below into a fresh Claude Code session in this repo. It is written as arguments to
`/to-spec`, which is user-invocable only — an agent cannot call it for you.

---

```
/to-spec Nuron — tracer bullet 01. There is no conversation in this context, so the artifacts below ARE the conversation. Read them first, then synthesize the spec from them.

## Read these as the conversation (in this order)

1. `docs/tracer-bullet-01.md` — committed at 19f0654. The authoritative design record: 22 decisions, 12 assertions (A1–A12), 13 superseded PRD statements, deliberate gaps, still-open items. This is the substance of a long grilling session.
2. GitHub issue #15 in `BasuruK/Nuron` — the map. Scope, architecture, assertions, decisions, frontier table. Its comment thread holds the fixture corpus text plus the constraints on it.
3. GitHub issues #16–#28 = stories NU-001 … NU-013, with full detail inline, linked as sub-issues of #15, with native `blocked_by` edges.
4. `AGENTS.md` plus `docs/agents/issue-tracker.md`, `docs/agents/triage-labels.md`, `docs/agents/domain.md` — tracker conventions, the five triage labels, and the domain-docs convention.

Reference only, FROZEN, do not edit: `_bmad-output/planning-artifacts/prds/prd-Nuron-2026-07-08/prd.md` (sections 4.1–4.4) and `review-adversarial-general.md` (findings F-1, F-2, F-3).

## What Nuron is

Self-hosted "company brain". Documents (.md/.txt/.docx/.pdf) land in a watched directory or by upload, originals are preserved content-addressed in object storage, a deterministic parser extracts a Content Header, EVERY file blocks for human review where the reviewer may edit body content, an LLM Compiler emits typed triples, a second human gate confirms entity merges, and the result persists into a Neo4j property graph that answers queries with cited Decision lineage over SSE.

Six containers: `nuron-web` (SvelteKit), `nuron-api` (Laravel), `nuron-ai` (Python/LlamaIndex), Postgres (two schemas, two roles, no cross-schema grants), Neo4j (graph + HNSW vectors), RustFS (originals). RabbitMQ was deliberately removed — the review queue is the work queue.

## What route was taken, and what was skipped

`/grill-me` produced the design record, and the map plus 13 stories were then created BY HAND. `/to-spec` was skipped. That is why you are here.

Two route irregularities to be aware of:
- The map carries the `wayfinder:map` label but `/wayfinder` was never run. Its children are implementation tickets with assertions, not wayfinder decision tickets.
- No spec artifact exists in the `/to-spec` template shape. `docs/tracer-bullet-01.md` is a decision record, not a spec.

## Your job

1. Produce the spec per the `/to-spec` template and publish it to the tracker with `ready-for-agent`. Link it to map #15 (sub-issue and/or `Part of #15`).
2. Then RECONCILE: compare the spec against stories NU-001 … NU-013 and update GitHub where they diverge — edit story bodies, retitle, add or remove blocking edges, open missing stories, close redundant ones. Keep the `NU-00N` prefix scheme contiguous and in dependency order.
3. Report every divergence you found and what you did about it. If you conclude a story should be deleted or a dependency edge is wrong, say so before acting.

## Suspected divergences — verify each explicitly, do not take my word for it

1. **No story owns creating the fixture corpus.** A1, A2, A3, A5, A6, A7, A9, A10, A11 and A12 all depend on it, and it exists only as text in a comment on #15. NU-002 describes parser/fixture co-design but does not own producing the files.
2. **No `CONTEXT.md` and no `docs/adr/`**, though `AGENTS.md` points at both. The grill invented domain vocabulary (Landing Zone, Reviewed Source, Evidence root, provenance ref, natural key, alias, auto-join, merge candidate) and made hard-to-reverse decisions (RabbitMQ removed; Neo4j over Postgres/AGE; embeddings in Neo4j not pgvector; 1024 dims as a one-way door; LlamaParse off by default under PRD §5.1). Per `docs/agents/domain.md` that is glossary and ADR material. None of it was recorded. I suspect this is the largest real gap.
3. **Assertions split across stories with no clear owner.** A4 spans NU-006 and NU-009; A12 spans NU-002, NU-003, NU-004 and NU-011. Nothing states which story finally closes them.
4. **Still-open items from `docs/tracer-bullet-01.md` §7 are not stories**: merge-candidate precision bar, LlamaParse per-page credit verification, upload size cap.
5. **NU-013 duplicates closed issue #13**, which was closed for exactly that deletion without the deletion landing.
6. **Map #15 carries `ready-for-human`**, which may be wrong for a map.
7. **`docs/agents/` is untracked in git** — `AGENTS.md` references files not committed.

## Constraints

- **Nothing is implemented.** No `src`, no `services`, no `package.json`, no `pyproject.toml`. An earlier draft of NU-001 and NU-002 was written before the stories existed and was deliberately deleted; the story bodies say so. Do not resurrect it, and do not write implementation code in this session — this session is spec and tracker only.
- `/to-spec` step 2 says prefer existing seams. **There are none** — the codebase is empty, so every seam is new. Do not spend effort hunting. Propose seams at the highest points and confirm them with me.
- The repo is **public**. Full detail inline on issues is an accepted, deliberate choice.
- **GitHub is having a global outage.** GraphQL 503s frequently; REST is more reliable. Use `gh api` REST endpoints and wrap every write in a retry loop of 8–10 attempts. Verify after writing rather than trusting a single response.
- Sub-issues: `POST repos/BasuruK/Nuron/issues/15/sub_issues -F sub_issue_id=<child DB id>`. Dependencies: `POST repos/BasuruK/Nuron/issues/<child>/dependencies/blocked_by -F issue_id=<blocker DB id>`. Both take the numeric **database id** (`gh api repos/BasuruK/Nuron/issues/<n> --jq .id`), not the `#number`.
- Labels that exist: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`, `wayfinder:map`, `wayfinder:task`.
- Branch is `project-initation`. Commit `19f0654` is local and unpushed.
```
