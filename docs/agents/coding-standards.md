# Coding Standards

How to write code in this repo. Complements `CONTEXT.md` (domain vocabulary) and `docs/adr/`
(hard-to-reverse decisions) — this file is style and judgment, not domain or architecture.

## Philosophy: simplest thing that works

Default posture is YAGNI: minimum code that solves the problem, nothing speculative.

- No abstraction for single-use code. No interface with one implementation, no factory for one product, no config for a value that never changes.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for scenarios that can't happen. Validate at system boundaries (user input, external APIs), trust internal code.
- Three similar lines beats a premature abstraction.

**Exception — load-bearing seams stay firm.** The tracer bullet already declared some seams
non-negotiable: the deterministic core (`content_hash`, `parse_header`, `normalize`, `plan_delta`)
stays pure and LLM-free; `nuron_api`/`nuron_ai` schema isolation is enforced by DB role, not
convention; Reviewed Source is the Evidence root, not the raw file. Simplicity-first does not mean
re-opening those — they're architecture decisions (see `docs/adr/`), not code-shape opinions.

## No compressed one-liners

Default to the expanded, explicit form — named intermediates, multiple statements — over chained
comprehensions, nested ternaries, or dense method chains. Readability at a glance beats fewer
characters. The person writing the code decides case-by-case whether something is simple enough to
stay a one-liner; the default assumption is "write it long."

```python
# Avoid
rows = [r for r in fetch() if r.state == "landed" and not r.claimed_by]

# Prefer
unclaimed = fetch()
rows = []
for row in unclaimed:
    if row.state == "landed" and row.claimed_by is None:
        rows.append(row)
```

## Surgical changes

Touch only what the task needs.

- Don't refactor, reformat, or "improve" adjacent code while fixing something else.
- Match existing style even where you'd write it differently.
- Notice unrelated dead code → mention it, don't delete it.
- Clean up only the orphans your own change created (an import or variable your edit made unused).

## Handling ambiguity

- **Trivial or reversible** (naming, a config default, which helper to call): ship the simplest
  default and flag the choice in the same response. Never stall on a question you can default.
- **Architectural or hard-to-reverse** (schema shape, a security boundary, the data model, a new
  external dependency): stop and ask. Present the interpretations if more than one is real; don't
  pick silently.

## Comments and docstrings

- Every method gets a comment describing what it does — always, max 2 lines.
- Add a second comment only when the *why* is genuinely non-obvious: a hidden constraint, a
  workaround, a subtle invariant. Skip it if removing it wouldn't confuse a future reader.
- No multi-paragraph docstrings, in any language.

```python
def content_hash(data: bytes) -> str:
    """Returns the sha256 hex digest of raw document bytes."""
    return hashlib.sha256(data).hexdigest()
```

## Types

Mandatory everywhere, no exceptions carved out per-service:

- Python: type hints on every function signature.
- PHP: type declarations on every method signature (params and return).
- TypeScript: strict mode on.

A typed signature replaces most of what a docstring would otherwise have to explain.

## Testing

- **Assert on observable state, never on library internals or prompt text.** For this pipeline
  that means Postgres rows, the Neo4j graph, RustFS objects, and the SSE response — never
  LlamaIndex internals. This is a general rule, not scoped to the tracer bullet that introduced it.
- **Bug fixes are test-first.** Write a failing test that reproduces the bug before touching the
  fix. Fix. Verify the test goes green. Don't fix from a description alone.

## Commit messages

Conventional Commits, required: `type: short imperative summary` (`feat:`, `fix:`, `docs:`,
`chore:`, `refactor:`, `test:`). Matches the existing git history — codifying an accidental
pattern, not introducing a new one.

## `nuron-web` UI stack

Component priority order, highest first:

1. **Bits UI** (v2) native component — it ships far more than dialogs/tabs; check it before
   reaching for anything else.
2. **shadcn-svelte** — copied into the repo, not an npm dependency, so it's freely editable once
   pulled in. Use when Bits UI has the primitive but not the styled wrapper.
3. **Custom component** — last resort, for the genuinely bespoke pieces (the diff view, the
   SSE-streaming query box).

Styling: **Tailwind CSS** (required by shadcn-svelte, pairs directly with Bits UI). Icons:
**Lucide Svelte**.

## Anti-patterns to catch in review

**Over-abstraction for a single caller.** A `MergeStrategy` interface with one implementation
because "there might be other merge strategies later" — there's exactly one merge rule in this
slice (natural-key + human-confirmed alias). Write the function; add the interface if a second
strategy actually shows up.

**Speculative parameters.** A `parse_header(text, filename, source_owner, strict=False,
on_error=None)` where `strict` and `on_error` were never asked for. Every parameter should trace to
a real caller's real need, not a guess about future callers.

**Drive-by refactor inside a bug fix.** A diff fixing "empty `author` crashes the parser" that also
renames variables, adds type hints to unrelated functions, and reformats quote style. Only the
lines that fix the reported bug should appear in that diff.
