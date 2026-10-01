"""Persist compiled triples to Neo4j: alias matching, delta apply, refcounted provenance (NU-009).

Diffs a document's compiled graph against the live Neo4j state -- its own prior contribution
(for drop_ref) and whatever already exists under its proposed keys (for update/unchanged, so an
auto-joined entity another document already wrote is never re-embedded) -- then applies
plan_delta's classification with a refcounted provenance set per node (docs/tracer-bullet-01.md
"persist", ADR-0003, ADR-0004). Compiler.py extracts and enriches; this module is what actually
calls insert/upsert against a live graph store.

**Deliberately does not go through `PropertyGraphIndex(embed_kg_nodes=True)`** (the construction
ADR-0003 shows): that index's own `insert_nodes()` skips a node entirely once its id already
exists in the store, with no update path -- exactly wrong for a re-approval, which must still
overwrite a changed node's content (A6). Driving `Neo4jPropertyGraphStore.upsert_nodes()` directly
with our own plan_delta-classified embedding is what makes "leave unchanged alone, update in
place" possible at all; ADR-0003's point -- embeddings live on the graph node, one store, one
write -- holds either way.

**Relations are re-upserted, never diffed or dropped.** A relationship-only edit (A6's own
trigger case) leaves a prior version's edges in place alongside the new ones. plan_delta has no
edge-level concept to apply (core.py's contract is nodes only), and edges carry no provenance ref
of their own in this schema -- ponytail: stale edges accumulate, revisit with an edge provenance
scheme if a real re-approval workload needs relationships to disappear too.
"""

import logging
import os
import time
import uuid
from collections.abc import Callable, Sequence
from typing import Any

import psycopg
from llama_index.core.graph_stores.types import EntityNode, Relation
from llama_index.graph_stores.neo4j import Neo4jPropertyGraphStore
from openai import OpenAI  # nosemgrep
from psycopg import sql

from nuron_ai import db
from nuron_ai.core import NodeDelta, plan_delta, resolve_key

logger = logging.getLogger(__name__)

_LEASE_SECONDS = 600.0
_MAX_ATTEMPTS = 5
_RETRY_DELAY_SECONDS = 60.0
_POLL_DELAY_SECONDS = 5.0

# Structural type for the configured embedding model -- lets tests use a stub with no live endpoint.
Embedder = Callable[[str], list[float]]

_PROVENANCE_REFS_PROPERTY = "provenance_refs"
_EMBEDDING_MODEL_PROPERTY = "embedding_model_id"
_EMBEDDING_DIMENSIONS_PROPERTY = "embedding_dimensions"
_DISPLAY_NAME_PROPERTY = "display_name"
# Bookkeeping/identity properties this module owns -- never part of a node's compared content.
_RESERVED_PROPERTIES = frozenset(
    {
        _PROVENANCE_REFS_PROPERTY,
        _EMBEDDING_MODEL_PROPERTY,
        _EMBEDDING_DIMENSIONS_PROPERTY,
        _DISPLAY_NAME_PROPERTY,
        "name",
    }
)


class EmbeddingConfigurationMismatchError(ValueError):
    """An existing node's embedding model or dimension count differs from config -- terminal until a re-embedding migration (ADR-0004)."""


def build_store_from_env() -> Neo4jPropertyGraphStore:
    """Connects to Neo4j via NEO4J_URI/NEO4J_PASSWORD -- community edition has exactly one user, `neo4j`."""
    return Neo4jPropertyGraphStore(
        username="neo4j",
        password=os.environ["NEO4J_PASSWORD"],
        url=os.environ["NEO4J_URI"],
    )


def build_embedder_from_env() -> tuple[Embedder, str, int]:
    """Builds an OpenAI-compatible embedder from OPENAI_*/EMBEDDING_DIMENSIONS; returns it with the model id and dimension count ADR-0004 requires stamped on every node."""
    # Guardrails suppressed: this client only embeds. Any text generation or summarization
    # (planned) must go through a Guardrails-wrapped client -- generated output needs validation.
    client = OpenAI(base_url=os.environ["OPENAI_BASE_URL"], api_key=os.environ["OPENAI_API_KEY"])  # nosemgrep
    model = os.environ["OPENAI_EMBEDDING_MODEL"]
    dimensions = int(os.environ["EMBEDDING_DIMENSIONS"])

    def embed(text: str) -> list[float]:
        """Calls the configured OpenAI-compatible embeddings endpoint for one text."""
        response = client.embeddings.create(model=model, input=text, dimensions=dimensions)
        return response.data[0].embedding

    return embed, model, dimensions


def _content(label: str, display_name: str, extra_properties: dict[str, Any]) -> dict[str, Any]:
    """Builds one node's comparable content: label, display name, and its non-bookkeeping properties."""
    content: dict[str, Any] = {"label": label, "display_name": display_name}
    for key, value in extra_properties.items():
        content[key] = value
    return content


def _content_from_compiled_node(node: dict[str, Any]) -> dict[str, Any]:
    """Strips the per-document provenance dict from a compiled_graph node before comparison.

    Also drops None-valued properties (compiler.py always sets author/author_source/timestamp on
    a DECISION node, even to None when unmatched): Neo4j has no null property value, so a
    round-tripped node never has that key at all (`upsert_nodes`'s own `apoc.map.clean` strips it
    on write, `get()`'s `remove_empty_values` strips it on read). Comparing against a dict that
    still carries `{"author": None}` would never equal the round-tripped one, marking every such
    node "update" forever and re-embedding it on every persist -- the exact spend this module
    exists to avoid.
    """
    extra: dict[str, Any] = {}
    for key, value in node["properties"].items():
        if key == "provenance" or value is None:
            continue
        extra[key] = value
    return _content(node["label"], node["name"], extra)


def _content_from_existing_node(entity: EntityNode) -> dict[str, Any]:
    """Strips this module's own bookkeeping properties from a node read back from Neo4j."""
    extra: dict[str, Any] = {}
    for key, value in entity.properties.items():
        if key in _RESERVED_PROPERTIES:
            continue
        extra[key] = value
    display_name = entity.properties.get(_DISPLAY_NAME_PROPERTY, entity.name)
    return _content(entity.label, display_name, extra)


def _embedding_text(label: str, display_name: str) -> str:
    """Deterministic text embedded for a node -- retrieval quality is A1's concern, not this one's."""
    return f"{display_name} ({label})"


def _node_properties(content: dict[str, Any], model_id: str, dimensions: int) -> dict[str, Any]:
    """Builds a node's content properties plus embedding bookkeeping -- never the provenance set.

    Provenance is mutated separately, atomically, in Neo4j itself (see `_add_ref_atomic`/
    `_release_ref_atomic`): `upsert_nodes`'s own Cypher does `SET e += ...`, a merge, so it can
    only ever add or overwrite keys, never clear one on a concurrent writer's behalf -- exactly
    wrong for a value multiple documents contribute refs to independently.
    """
    properties: dict[str, Any] = {_DISPLAY_NAME_PROPERTY: content["display_name"]}
    for key, value in content.items():
        if key in {"label", "display_name"}:
            continue
        properties[key] = value
    properties[_EMBEDDING_MODEL_PROPERTY] = model_id
    properties[_EMBEDDING_DIMENSIONS_PROPERTY] = dimensions
    return properties


# Both provenance queries recompute the ref set from whatever is actually stored at the moment
# they run, inside one Neo4j transaction each (`execute_query` auto-commits per call) -- never
# from a Python-held snapshot. Two documents racing to touch the same auto-joined node therefore
# each apply their own add/remove against the graph's current state, not against a stale read;
# core.py's release_ref/plan_delta already proved the *rule* (remove one ref, the node dies only
# when the set is empty) -- this is that same rule, just evaluated where the mutation actually
# happens so it can't be lost to a lost update.
_RELEASE_REF_QUERY = """
    MATCH (n {id: $node_key})
    WITH n, [x IN coalesce(n.provenance_refs, []) WHERE x <> $ref] AS remaining
    SET n.provenance_refs = remaining
    WITH n, remaining
    WHERE size(remaining) = 0
    DETACH DELETE n
    """

_ADD_REF_QUERY = """
    MATCH (n {id: $node_key})
    WITH n, [x IN coalesce(n.provenance_refs, []) WHERE x <> $ref_to_remove] AS without_old
    WITH n, CASE WHEN $ref_to_add IN without_old THEN without_old ELSE without_old + $ref_to_add END AS refs
    SET n.provenance_refs = refs
    """

# Dynamic property access (`n[$key]`) parameterizes the property *name*, not just its value --
# property keys here can trace back to LLM-extracted entity properties (compiler.py's
# `dict(node.properties)`), so they are not a fixed, trusted vocabulary. String-formatting a key
# straight into `REMOVE n.<key>` would be a Cypher injection surface; this keeps every key bound.
_REMOVE_STALE_PROPERTIES_QUERY = """
    MATCH (n {id: $node_key})
    UNWIND $stale_keys AS stale_key
    REMOVE n[stale_key]
    """


def _release_ref_atomic(store: Neo4jPropertyGraphStore, node_key: str, ref: str) -> None:
    """Atomically drops one provenance ref, deleting the node in the same query if it was the last."""
    store.structured_query(_RELEASE_REF_QUERY, param_map={"node_key": node_key, "ref": ref})


def _add_ref_atomic(
    store: Neo4jPropertyGraphStore, node_key: str, ref_to_add: str, ref_to_remove: str | None
) -> None:
    """Atomically adds one provenance ref, optionally dropping another (a re-approval's old ref)."""
    store.structured_query(
        _ADD_REF_QUERY,
        param_map={"node_key": node_key, "ref_to_add": ref_to_add, "ref_to_remove": ref_to_remove or ""},
    )


def _remove_stale_properties(store: Neo4jPropertyGraphStore, node_key: str, stale_keys: frozenset[str]) -> None:
    """Clears properties the new content no longer carries -- `upsert_nodes`'s own SET += never would."""
    if not stale_keys:
        return
    store.structured_query(
        _REMOVE_STALE_PROPERTIES_QUERY, param_map={"node_key": node_key, "stale_keys": sorted(stale_keys)}
    )


def _apply_node_delta(
    store: Neo4jPropertyGraphStore,
    embed: Embedder,
    model_id: str,
    dimensions: int,
    *,
    delta: NodeDelta,
    content: dict[str, Any] | None,
    existing: EntityNode | None,
    content_hash: str,
    previous_content_hash: str | None,
    is_previous_contribution: bool,
) -> None:
    """Applies one node's classification: create, update in place, leave alone, or release a ref."""
    if delta.action == "drop_ref":
        if existing is None:
            logger.warning("drop_ref for %s but the node no longer exists in Neo4j", delta.node_key)
            return
        old_refs = frozenset(existing.properties.get(_PROVENANCE_REFS_PROPERTY, []))
        if previous_content_hash is None or previous_content_hash not in old_refs:
            # Already released by an earlier attempt at this same persist (the worker crashed or
            # lost its lease between this Neo4j write and the Postgres bookkeeping that follows
            # it) -- retrying is expected to be a no-op here, not a second release (tracer-bullet-01
            # "the Neo4j write... is self-correcting"). This is a snapshot-based pre-check only --
            # the actual release below is still atomic against live state either way.
            return
        _release_ref_atomic(store, delta.node_key, previous_content_hash)
        return

    # add/update/unchanged: plan_delta only ever emits these actions for a key it read out of
    # `current_content`, so `content` is always present here -- trust that contract.
    assert content is not None

    ref_to_remove = previous_content_hash if is_previous_contribution else None

    if delta.action == "unchanged":
        old_refs = frozenset(existing.properties.get(_PROVENANCE_REFS_PROPERTY, [])) if existing else frozenset()
        already_has_ref = content_hash in old_refs and (ref_to_remove is None or ref_to_remove not in old_refs)
        if already_has_ref:
            return  # true no-op: same content, same contributor -- nothing to write (A5). Snapshot-based
            # fast path only; a redundant or (rarely) skipped write here never loses a ref, since
            # the write itself, when it does happen, is the atomic query below either way.
        _add_ref_atomic(store, delta.node_key, content_hash, ref_to_remove)
        return

    # add or update: (re)write content and (re)compute the embedding -- the only branch that
    # spends an embedding call -- then reconcile the provenance ref and any properties the new
    # content dropped, each as its own atomic step.
    embedding_text = _embedding_text(content["label"], content["display_name"])
    node = EntityNode(
        name=delta.node_key,
        label=content["label"],
        properties=_node_properties(content, model_id, dimensions),
        embedding=embed(embedding_text),
    )
    store.upsert_nodes([node])

    if existing is not None:
        stale_keys = frozenset(_content_from_existing_node(existing)) - frozenset(content)
        _remove_stale_properties(store, delta.node_key, stale_keys)

    _add_ref_atomic(store, delta.node_key, content_hash, ref_to_remove)


def _apply_relations(
    store: Neo4jPropertyGraphStore,
    relations: Sequence[dict[str, Any]],
    resolved_by_natural_key: dict[str, str],
) -> None:
    """Re-upserts every relation this document contributes, resolved through the same alias keys as its nodes."""
    resolved_relations: list[Relation] = []
    for relation in relations:
        source_key = resolved_by_natural_key.get(relation["source_key"])
        target_key = resolved_by_natural_key.get(relation["target_key"])
        if source_key is None or target_key is None:
            logger.warning("relation %s references a node key missing from this compile", relation)
            continue
        resolved_relations.append(
            Relation(
                label=relation["label"],
                source_id=source_key,
                target_id=target_key,
                properties=dict(relation["properties"]),
            )
        )
    if resolved_relations:
        store.upsert_relations(resolved_relations)


def persist_compiled_graph(
    store: Neo4jPropertyGraphStore,
    embed: Embedder,
    model_id: str,
    dimensions: int,
    *,
    content_hash: str,
    aliases: dict[tuple[str, str], str],
    previous_content_hash: str | None,
    previous_node_keys: frozenset[str],
    compiled_graph: dict[str, Any],
) -> frozenset[str]:
    """Applies one document's compiled graph to Neo4j; returns the node keys it now contributes to."""
    current_content: dict[str, dict[str, Any]] = {}
    resolved_by_natural_key: dict[str, str] = {}
    for node in compiled_graph["nodes"]:
        resolved_key = resolve_key(node["name"], node["label"], aliases)
        resolved_by_natural_key[node["key"]] = resolved_key
        current_content[resolved_key] = _content_from_compiled_node(node)

    lookup_keys = set(current_content) | set(previous_node_keys)
    existing_by_key: dict[str, EntityNode] = {}
    if lookup_keys:
        for entity in store.get(ids=list(lookup_keys)):
            existing_by_key[entity.id] = entity

    for entity in existing_by_key.values():
        existing_model_id = entity.properties.get(_EMBEDDING_MODEL_PROPERTY)
        if existing_model_id is not None and existing_model_id != model_id:
            raise EmbeddingConfigurationMismatchError(
                f"node {entity.id!r} was embedded with model {existing_model_id!r}, not the "
                f"configured {model_id!r} -- re-embedding is a migration, not a write (ADR-0004)"
            )
        existing_dimensions = entity.properties.get(_EMBEDDING_DIMENSIONS_PROPERTY)
        if existing_dimensions is not None and existing_dimensions != dimensions:
            raise EmbeddingConfigurationMismatchError(
                f"node {entity.id!r} was embedded at {existing_dimensions} dimensions, not the "
                f"configured {dimensions} -- re-embedding is a migration, not a write (ADR-0004)"
            )

    prior_content: dict[str, dict[str, Any]] = {}
    for key, entity in existing_by_key.items():
        prior_content[key] = _content_from_existing_node(entity)

    deltas = plan_delta(prior_content, current_content)
    for delta in deltas:
        _apply_node_delta(
            store,
            embed,
            model_id,
            dimensions,
            delta=delta,
            content=current_content.get(delta.node_key),
            existing=existing_by_key.get(delta.node_key),
            content_hash=content_hash,
            previous_content_hash=previous_content_hash,
            is_previous_contribution=delta.node_key in previous_node_keys,
        )

    _apply_relations(store, compiled_graph["relations"], resolved_by_natural_key)

    return frozenset(current_content)


_RETRY_SET_SQL = sql.SQL(
    """
    attempt_count = attempt_count + 1,
    next_attempt_at = now() + %(retry_delay_seconds)s * interval '1 second',
    state = CASE WHEN attempt_count + 1 >= %(max_attempts)s
                 THEN 'failed'::nuron_ai.pipeline_state
                 ELSE state END
    """
)

_RETRY_PARAMS: dict[str, Any] = {"retry_delay_seconds": _RETRY_DELAY_SECONDS, "max_attempts": _MAX_ATTEMPTS}

_PERSISTED_SET_SQL = sql.SQL("state = 'persisted', attempt_count = 0, next_attempt_at = NULL")

_FAILED_SET_SQL = sql.SQL("state = 'failed'")

_AWAITING_MERGE_CONFIRM_SET_SQL = sql.SQL(
    "state = 'awaiting_merge_confirm', attempt_count = 0, next_attempt_at = NULL"
)


def merge_gate_pending(conn: psycopg.Connection, worker_id: str) -> bool:
    """Claims one compiled row and advances it to awaiting_merge_confirm."""
    # ponytail: no merge-candidate detection exists yet (NU-010), so every compiled row is an
    # auto-join needing no human confirmation (CONTEXT.md "Auto-join") and passes straight
    # through. NU-010 replaces this pass-through: rows with candidates stay blocked here.
    claimed = db.claim(conn, worker_id, "compiled", lease_seconds=_LEASE_SECONDS)
    if claimed is None:
        return False

    digest, *_rest, lease_token = claimed
    db.release(conn, digest, worker_id, lease_token, _AWAITING_MERGE_CONFIRM_SET_SQL, {})
    return True

# Versions are numbered per original_filename, not per content_hash (review.py's approve():
# "Version lineage follows original_filename... a re-ingested file lands under a new content_hash
# every time"). rs_current.version - 1 is therefore the row this content_hash's own prior
# ingestion produced, joined back through original_filename to find its content_hash.
_PREVIOUS_CONTENT_HASH_QUERY = """
    SELECT rs_prev.content_hash
    FROM nuron_ai.reviewed_sources rs_current
    JOIN nuron_ai.documents d_current ON d_current.content_hash = rs_current.content_hash
    JOIN nuron_ai.reviewed_sources rs_prev ON rs_prev.version = rs_current.version - 1
    JOIN nuron_ai.documents d_prev ON d_prev.content_hash = rs_prev.content_hash
        AND d_prev.original_filename = d_current.original_filename
    WHERE rs_current.content_hash = %(content_hash)s
    ORDER BY rs_current.version DESC
    LIMIT 1
    """


def persist_pending(
    conn: psycopg.Connection,
    store: Neo4jPropertyGraphStore,
    embed: Embedder,
    model_id: str,
    dimensions: int,
    worker_id: str,
) -> bool:
    """Claims one awaiting_merge_confirm row and persists its compiled graph to Neo4j."""
    # ponytail: claims unconditionally -- rows only reach awaiting_merge_confirm via
    # merge_gate_pending, which is where NU-010's real merge-candidate blocking must land. Until
    # a human-confirm path exists, every row here is an auto-join (CONTEXT.md "Auto-join").
    claimed = db.claim(conn, worker_id, "awaiting_merge_confirm", lease_seconds=_LEASE_SECONDS)
    if claimed is None:
        return False

    digest, original_filename, *_rest, lease_token = claimed

    graph_row = conn.execute(
        "SELECT compiled_graph FROM nuron_ai.documents WHERE content_hash = %(content_hash)s",
        {"content_hash": digest},
    ).fetchone()
    reviewed_source_row = conn.execute(
        "SELECT id FROM nuron_ai.reviewed_sources WHERE content_hash = %(content_hash)s "
        "ORDER BY version DESC LIMIT 1",
        {"content_hash": digest},
    ).fetchone()
    previous_row = conn.execute(_PREVIOUS_CONTENT_HASH_QUERY, {"content_hash": digest}).fetchone()
    alias_rows = conn.execute(
        "SELECT alias_key, label, survivor_key FROM nuron_ai.entity_aliases"
    ).fetchall()
    conn.commit()

    if graph_row is None or reviewed_source_row is None:
        logger.error(
            "awaiting_merge_confirm row %s has no compiled_graph or reviewed_sources row -- "
            "should be impossible",
            digest,
        )
        db.release(conn, digest, worker_id, lease_token, _RETRY_SET_SQL, _RETRY_PARAMS)
        return True
    compiled_graph = graph_row[0]
    reviewed_source_id = reviewed_source_row[0]
    previous_content_hash = previous_row[0] if previous_row is not None else None

    previous_node_keys: frozenset[str] = frozenset()
    if previous_content_hash is not None:
        provenance_rows = conn.execute(
            "SELECT node_key FROM nuron_ai.node_provenance WHERE content_hash = %(content_hash)s",
            {"content_hash": previous_content_hash},
        ).fetchall()
        conn.commit()
        previous_node_keys = frozenset(row[0] for row in provenance_rows)

    aliases: dict[tuple[str, str], str] = {}
    for alias_key, label, survivor_key in alias_rows:
        aliases[(alias_key, label)] = survivor_key

    try:
        current_keys = persist_compiled_graph(
            store,
            embed,
            model_id,
            dimensions,
            content_hash=digest,
            aliases=aliases,
            previous_content_hash=previous_content_hash,
            previous_node_keys=previous_node_keys,
            compiled_graph=compiled_graph,
        )
    except EmbeddingConfigurationMismatchError as err:
        logger.warning("persist failed permanently for %s (%s): %s", digest, original_filename, err)
        db.release(conn, digest, worker_id, lease_token, _FAILED_SET_SQL, {})
        return True
    except Exception as err:
        logger.warning("persist failed for %s (%s): %s", digest, original_filename, err)
        db.release(conn, digest, worker_id, lease_token, _RETRY_SET_SQL, _RETRY_PARAMS)
        return True

    if previous_content_hash is not None:
        conn.execute(
            "DELETE FROM nuron_ai.node_provenance WHERE content_hash = %(content_hash)s",
            {"content_hash": previous_content_hash},
        )
    for node_key in current_keys:
        conn.execute(
            """
            INSERT INTO nuron_ai.node_provenance (node_key, content_hash, reviewed_source_id)
            VALUES (%(node_key)s, %(content_hash)s, %(reviewed_source_id)s)
            ON CONFLICT (node_key, content_hash) DO NOTHING
            """,
            {"node_key": node_key, "content_hash": digest, "reviewed_source_id": reviewed_source_id},
        )
    conn.commit()

    db.release(conn, digest, worker_id, lease_token, _PERSISTED_SET_SQL, {})
    return True


def main() -> None:
    """Runs the Persist worker forever: gates compiled rows, then persists awaiting_merge_confirm rows."""
    logging.basicConfig(level=logging.INFO)
    worker_id = uuid.uuid4().hex
    store = build_store_from_env()
    embed, model_id, dimensions = build_embedder_from_env()

    while True:
        try:
            with db.from_env() as conn:
                while True:
                    gated = merge_gate_pending(conn, worker_id)
                    persisted = persist_pending(conn, store, embed, model_id, dimensions, worker_id)
                    if not (gated or persisted):
                        time.sleep(_POLL_DELAY_SECONDS)
        except Exception:
            logger.exception(
                "persist worker failed; retrying after %.0f seconds", _RETRY_DELAY_SECONDS
            )
            time.sleep(_RETRY_DELAY_SECONDS)


if __name__ == "__main__":
    main()
