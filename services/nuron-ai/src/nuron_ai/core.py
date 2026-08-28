"""Deterministic, LLM-free pipeline core: identity, header parsing, entity naming, delta.

Pure functions only -- no network, no database, no LLM. See CONTEXT.md and docs/adr/0005
(content-hash identity), docs/adr/0006 (merges-not-links).
"""

import hashlib
import re
import shlex
from dataclasses import dataclass
from datetime import date
from typing import Literal

AuthorSource = Literal["extracted", "default", "unknown"]
DeltaAction = Literal["add", "update", "unchanged", "drop_ref"]

_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\n(.*?\n)---[ \t]*\n?", re.DOTALL)
# Dash class covers hyphen/en-dash/em-dash: markdown conversion (LlamaParse, A11) doesn't
# preserve which one the source used.
_SIGNATURE_RE = re.compile(r"^[-–—][ \t]*([^,\n]+),[ \t]*(\d{4}-\d{2}-\d{2})[ \t]*$", re.MULTILINE)
_FILENAME_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_H1_RE = re.compile(r"^#[ \t]+(.+)$", re.MULTILINE)


def content_hash(data: bytes) -> str:
    """Returns the sha256 hex digest of raw document bytes -- document identity (ADR-0005)."""
    return hashlib.sha256(data).hexdigest()


def object_key(digest: str) -> str:
    """Returns the RustFS object key for a content hash: {digest[0:2]}/{digest}."""
    return f"{digest[:2]}/{digest}"


@dataclass(frozen=True)
class ContentHeader:
    """A document's parsed Content Header, still missing whatever nothing could confirm."""

    subject: str | None
    author: str | None
    author_source: AuthorSource
    timestamp: date | None
    tags: list[str]


def parse_header(text: str, filename: str, source_owner: str | None) -> ContentHeader:
    """Extracts Subject/Author/Date/Tags: frontmatter -> in-prose signature -> filename date."""
    text = text.replace("\r\n", "\n")
    frontmatter_match = _FRONTMATTER_RE.match(text)
    if frontmatter_match is None:
        body_text = text
    else:
        body_text = text[frontmatter_match.end():]
    frontmatter = _parse_frontmatter(frontmatter_match)
    last_line = text.rstrip().rsplit("\n", maxsplit=1)[-1]
    signature = _SIGNATURE_RE.fullmatch(last_line)

    frontmatter_author = frontmatter.get("author")
    author: str | None
    author_source: AuthorSource
    if isinstance(frontmatter_author, str) and frontmatter_author.strip():
        author, author_source = frontmatter_author.strip(), "extracted"
    elif signature is not None:
        author, author_source = signature.group(1).strip(), "extracted"
    elif source_owner:
        author, author_source = source_owner, "default"
    else:
        author, author_source = None, "unknown"

    title = frontmatter.get("title")
    if isinstance(title, str) and title.strip():
        subject = title.strip()
    else:
        heading = _H1_RE.search(body_text)
        if heading is not None:
            subject = heading.group(1).strip()
        else:
            subject = None

    frontmatter_date = frontmatter.get("date")
    if isinstance(frontmatter_date, str):
        timestamp = _parse_date(frontmatter_date)
    else:
        timestamp = None
    if timestamp is None and signature is not None:
        timestamp = _parse_date(signature.group(2))
    if timestamp is None:
        filename_match = _FILENAME_DATE_RE.search(filename)
        if filename_match is not None:
            timestamp = _parse_date(filename_match.group(1))

    tags = frontmatter.get("tags", [])
    if not isinstance(tags, list):
        tags = [tags]

    return ContentHeader(
        subject=subject,
        author=author,
        author_source=author_source,
        timestamp=timestamp,
        tags=tags,
    )


def _parse_date(value: str) -> date | None:
    """Returns an ISO date, or None when the value is not a calendar date."""
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _unquote(value: str) -> str:
    """Removes one matching pair of surrounding single or double quotes."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _parse_frontmatter(match: re.Match[str] | None) -> dict[str, str | list[str]]:
    """Parses flat `key: value` / `key: [a, b]` fields from matched frontmatter."""
    if match is None:
        return {}

    fields: dict[str, str | list[str]] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            items = shlex.shlex(value[1:-1], posix=True)
            items.whitespace = ","
            items.whitespace_split = True
            items.commenters = ""
            fields[key] = [item.strip() for item in items if item.strip()]
        elif value:
            fields[key] = _unquote(value)
    return fields


def normalize(name: str) -> str:
    """Case-folds and trims whitespace only -- no stemming, so it deliberately under-merges."""
    return " ".join(name.split()).casefold()


def natural_key(name: str, label: str) -> str:
    """An entity's pre-human identity: normalize(name) + label."""
    if ":" in label:
        raise ValueError("label must not contain colons")
    return f"{normalize(name)}:{label}"


def resolve_key(name: str, label: str, aliases: dict[tuple[str, str], str]) -> str:
    """Resolves an entity's graph key: a confirmed alias survivor, else its own natural key."""
    if ":" in label:
        raise ValueError("label must not contain colons")
    survivor = aliases.get((normalize(name), label))
    if survivor is not None:
        return survivor
    return natural_key(name, label)


@dataclass(frozen=True)
class EntityAlias:
    """A human-confirmed merge, ready to persist to nuron_ai.entity_aliases."""

    alias_key: str
    label: str
    survivor_key: str


def merge_alias(alias_name: str, label: str, survivor_name: str) -> EntityAlias:
    """Builds the alias record for a human-confirmed merge at merge gate 2."""
    if ":" in label:
        raise ValueError("label must not contain colons")
    return EntityAlias(
        alias_key=normalize(alias_name),
        label=label,
        survivor_key=natural_key(survivor_name, label),
    )


@dataclass(frozen=True)
class NodeDelta:
    """One node's classification against its prior persisted state, for one document."""

    node_key: str
    action: DeltaAction
    needs_embedding: bool


def plan_delta(
    prior: dict[str, dict[str, object]],
    current: dict[str, dict[str, object]],
) -> list[NodeDelta]:
    """Classifies each node this document contributes: add, update, unchanged, or drop_ref."""
    deltas: list[NodeDelta] = []

    for node_key in sorted(current):
        properties = current[node_key]
        if node_key not in prior:
            deltas.append(NodeDelta(node_key, "add", needs_embedding=True))
        elif prior[node_key] != properties:
            deltas.append(NodeDelta(node_key, "update", needs_embedding=True))
        else:
            deltas.append(NodeDelta(node_key, "unchanged", needs_embedding=False))

    for node_key in sorted(prior):
        if node_key not in current:
            deltas.append(NodeDelta(node_key, "drop_ref", needs_embedding=False))

    return deltas


def release_ref(refs: frozenset[str], ref: str) -> tuple[frozenset[str], bool]:
    """Removes one provenance ref; returns the remaining refs and whether the node should die."""
    remaining = refs - {ref}
    return remaining, len(remaining) == 0
