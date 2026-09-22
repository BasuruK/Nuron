"""Compiler: content_approved -> compiled, typed triples with provenance (NU-008).

SchemaLLMPathExtractor turns an approved Reviewed Source into schema-validated typed triples.
It carries none of `author`, `author_source`, `timestamp`, `evidence_span`, or a provenance
ref -- this module is the post-extraction enrichment step that stamps them on before storage
(docs/tracer-bullet-01.md "LlamaIndex surface", CONTEXT.md "Compiler"/"Provenance ref"). Does
not call insert_nodes() against a live graph store -- that is NU-009's job.
"""

import logging
import os
import time
import uuid
from collections.abc import Sequence
from datetime import date
from enum import Enum
from typing import Any, Literal, Protocol

import psycopg
from llama_index.core.graph_stores.types import KG_NODES_KEY, KG_RELATIONS_KEY, EntityNode, Relation
from llama_index.core.indices.property_graph import SchemaLLMPathExtractor
from llama_index.core.schema import BaseNode, TextNode
from llama_index.llms.openai_like import OpenAILike
from psycopg import sql
from psycopg.types.json import Jsonb

from nuron_ai import db
from nuron_ai.core import natural_key

logger = logging.getLogger(__name__)

_LEASE_SECONDS = 600.0
_MAX_ATTEMPTS = 5
_RETRY_DELAY_SECONDS = 60.0
_POLL_DELAY_SECONDS = 5.0


class GraphExtractor(Protocol):
    """Structural type for SchemaLLMPathExtractor -- lets tests use a stub with no live LLM."""

    def __call__(self, nodes: Sequence[BaseNode], **kwargs: Any) -> list[BaseNode]: ...


_PossibleEntities = Literal["DECISION", "ENTITY", "EVIDENCE"]
_PossibleRelations = Literal["SUPERSEDES", "EVIDENCED_BY", "AFFECTS", "DEPENDS_ON", "PART_OF"]

# (subject_label, relation, object_label) triples SchemaLLMPathExtractor keeps under strict=True
# -- decision 3's guardrail enforced by the library's own Pydantic validation, not the prompt.
_KG_VALIDATION_SCHEMA: list[tuple[str, str, str]] = [
    ("DECISION", "SUPERSEDES", "DECISION"),
    ("DECISION", "EVIDENCED_BY", "EVIDENCE"),
    ("DECISION", "AFFECTS", "ENTITY"),
    ("DECISION", "DEPENDS_ON", "ENTITY"),
    ("ENTITY", "AFFECTS", "ENTITY"),
    ("ENTITY", "DEPENDS_ON", "ENTITY"),
    ("ENTITY", "PART_OF", "ENTITY"),
    ("ENTITY", "EVIDENCED_BY", "EVIDENCE"),
]

# Names both a Decision and an Entity joined by a schema-valid relation (DECISION AFFECTS
# ENTITY) -- a lone Decision with nothing to relate to would get pruned to zero triplets by
# strict=True even on a working endpoint, making the check flaky rather than informative.
_STRUCTURED_OUTPUT_PROBE = (
    "Basuru decided to drop the session store for stateless JWT sessions on 2026-05-14, "
    "which affects the rate limiter."
)


def build_kg_extractor() -> SchemaLLMPathExtractor:
    """Builds the Compiler's extractor from OPENAI_* env vars (OpenAILike -- any compatible endpoint)."""
    llm = OpenAILike(
        model=os.environ["OPENAI_MODEL"],
        api_base=os.environ["OPENAI_BASE_URL"],
        api_key=os.environ["OPENAI_API_KEY"],
        is_chat_model=os.environ.get("OPENAI_IS_CHAT_MODEL", "false").lower() == "true",
        is_function_calling_model=os.environ.get("OPENAI_IS_FUNCTION_CALLING_MODEL", "false").lower()
        == "true",
    )
    return SchemaLLMPathExtractor(
        llm=llm,
        # SchemaLLMPathExtractor's own stub types this as type[Any] | None; a Literal is
        # exactly what every llama-index example passes here, mypy's imprecision not ours.
        possible_entities=_PossibleEntities,  # type: ignore[arg-type]
        possible_relations=_PossibleRelations,  # type: ignore[arg-type]
        kg_validation_schema=_KG_VALIDATION_SCHEMA,
        strict=True,
    )


def check_structured_output(kg_extractor: GraphExtractor) -> None:
    """Fails loudly at boot if the configured endpoint can't actually produce schema-valid triples."""
    # SchemaLLMPathExtractor swallows a failed structured-predict call into an empty result
    # rather than raising, so an exception isn't the only failure shape to check for below --
    # an empty result on an obviously-extractable probe is too.
    try:
        [transformed] = kg_extractor([TextNode(text=_STRUCTURED_OUTPUT_PROBE)])
    except Exception as err:
        raise RuntimeError(
            "structured output check failed: the configured LLM endpoint raised while "
            "extracting typed triples -- SchemaLLMPathExtractor depends on it working"
        ) from err

    if not transformed.metadata.get(KG_NODES_KEY):
        raise RuntimeError(
            "structured output check failed: the configured LLM endpoint returned no "
            "entities for an obviously-extractable probe sentence"
        )


def _entity_key(node: EntityNode) -> str:
    """Returns a node's identity key -- core.natural_key, not the library's own EntityNode.id."""
    return natural_key(node.name, node.label)


def enrich_and_serialize(
    entity_nodes: Sequence[EntityNode],
    relations: Sequence[Relation],
    *,
    content_hash: str,
    reviewed_source_id: int,
    author: str | None,
    author_source: str | None,
    document_date: date | None,
    body: str,
) -> dict[str, Any]:
    """Stamps provenance/author/timestamp/evidence_span onto extracted triples before insert_nodes()."""
    provenance = {"content_hash": content_hash, "reviewed_source_id": reviewed_source_id}

    # SchemaLLMPathExtractor appends one EntityNode per triplet it participates in, so the
    # same entity (equal name + label) can come back several times -- dedupe onto its natural key.
    nodes_by_key: dict[str, dict[str, Any]] = {}
    decisions: list[str] = []
    # ponytail: EntityNode.id is just node.name in this pinned version, not name+label -- two
    # different-labeled entities sharing a name would collide here. Accepted: real Decisions read
    # as sentences, real Entities as proper nouns, so this fixture domain never hits it.
    id_to_key: dict[str, str] = {}

    for node in entity_nodes:
        node_key = _entity_key(node)
        id_to_key[node.id] = node_key
        if node_key in nodes_by_key:
            continue

        properties: dict[str, Any] = dict(node.properties)
        properties["provenance"] = provenance
        if node.label == "DECISION":
            properties["author"] = author
            properties["author_source"] = author_source
            properties["timestamp"] = document_date.isoformat() if document_date else None
            decisions.append(node_key)
        elif node.label == "EVIDENCE":
            offset = body.find(node.name)
            if offset != -1:
                properties["evidence_span"] = [offset, offset + len(node.name)]

        nodes_by_key[node_key] = {
            "key": node_key,
            "label": node.label,
            "name": node.name,
            "properties": properties,
        }

    # Relation.source_id/target_id are the participating EntityNode.id values from this same
    # call -- resolve them back to the natural key every node above was keyed on.
    seen_relations: set[tuple[str, str, str]] = set()
    relation_dicts: list[dict[str, Any]] = []
    for relation in relations:
        source_key = id_to_key[relation.source_id]
        target_key = id_to_key[relation.target_id]
        relation_id = (source_key, relation.label, target_key)
        if relation_id in seen_relations:
            continue
        seen_relations.add(relation_id)
        relation_dicts.append(
            {
                "label": relation.label,
                "source_key": source_key,
                "target_key": target_key,
                "properties": dict(relation.properties),
            }
        )

    return {"nodes": list(nodes_by_key.values()), "relations": relation_dicts, "decisions": decisions}


def compile_graph(
    kg_extractor: GraphExtractor,
    *,
    body: str,
    content_hash: str,
    reviewed_source_id: int,
    author: str | None,
    author_source: str | None,
    document_date: date | None,
) -> dict[str, Any]:
    """Runs the Compiler on one Reviewed Source's body and enriches the typed triples it returns."""
    [transformed] = kg_extractor([TextNode(text=body)])
    entity_nodes = transformed.metadata.get(KG_NODES_KEY, [])
    relations = transformed.metadata.get(KG_RELATIONS_KEY, [])
    compiled = enrich_and_serialize(
        entity_nodes,
        relations,
        content_hash=content_hash,
        reviewed_source_id=reviewed_source_id,
        author=author,
        author_source=author_source,
        document_date=document_date,
        body=body,
    )

    # CONTEXT.md: a Decision is "author, timestamp, and a resolving Evidence edge" -- not
    # a reason to reject the compile (the LLM may genuinely find no supporting quote), but
    # worth surfacing rather than leaving silent.
    evidenced_keys = {
        relation["source_key"] for relation in compiled["relations"] if relation["label"] == "EVIDENCED_BY"
    }
    for node in compiled["nodes"]:
        if node["label"] == "DECISION" and node["key"] not in evidenced_keys:
            logger.warning("compiled Decision %r for %s has no EVIDENCED_BY relation", node["name"], content_hash)

    return compiled


class _ReleaseOperation(Enum):
    RETRY = "retry"
    COMPILED = "compiled"


_ATTEMPT_RELEASE_SQL = sql.SQL(
    """
    attempt_count = attempt_count + 1,
    next_attempt_at = now() + %(retry_delay_seconds)s * interval '1 second',
    state = CASE WHEN attempt_count + 1 >= %(max_attempts)s
                 THEN 'failed'::nuron_ai.pipeline_state
                 ELSE state END
    """
)

_RELEASE_SET_SQL = {
    _ReleaseOperation.RETRY: _ATTEMPT_RELEASE_SQL,
    _ReleaseOperation.COMPILED: sql.SQL(
        "state = 'compiled', compiled_graph = %(compiled_graph)s, attempt_count = 0, next_attempt_at = NULL"
    ),
}


def _release(
    conn: psycopg.Connection,
    digest: str,
    worker_id: str,
    lease_token: int,
    operation: _ReleaseOperation,
    params: dict[str, Any],
) -> None:
    """Releases a claimed row via db.release, picking this module's own SET fragment for the outcome."""
    db.release(conn, digest, worker_id, lease_token, _RELEASE_SET_SQL[operation], params)


def compile_pending(
    conn: psycopg.Connection,
    kg_extractor: GraphExtractor,
    worker_id: str,
    lease_seconds: float = _LEASE_SECONDS,
) -> bool:
    """Claims one content_approved row and advances it to compiled with enriched typed triples."""
    claimed = db.claim(conn, worker_id, "content_approved", lease_seconds=lease_seconds)
    if claimed is None:
        return False

    digest, original_filename, _, author, author_source, document_date, _, body, lease_token = claimed

    reviewed_source_row = conn.execute(
        "SELECT id FROM nuron_ai.reviewed_sources WHERE content_hash = %(content_hash)s",
        {"content_hash": digest},
    ).fetchone()
    conn.commit()
    if reviewed_source_row is None:
        _release(
            conn,
            digest,
            worker_id,
            lease_token,
            _ReleaseOperation.RETRY,
            {"retry_delay_seconds": _RETRY_DELAY_SECONDS, "max_attempts": _MAX_ATTEMPTS},
        )
        raise RuntimeError(
            f"content_approved row {digest} has no reviewed_sources row -- should be impossible"
        )
    reviewed_source_id = reviewed_source_row[0]

    try:
        compiled = compile_graph(
            kg_extractor,
            body=body,
            content_hash=digest,
            reviewed_source_id=reviewed_source_id,
            author=author,
            author_source=author_source,
            document_date=document_date,
        )
    except Exception as err:
        logger.warning("compilation failed for %s (%s): %s", digest, original_filename, err)
        _release(
            conn,
            digest,
            worker_id,
            lease_token,
            _ReleaseOperation.RETRY,
            {"retry_delay_seconds": _RETRY_DELAY_SECONDS, "max_attempts": _MAX_ATTEMPTS},
        )
        return True

    _release(
        conn,
        digest,
        worker_id,
        lease_token,
        _ReleaseOperation.COMPILED,
        {"compiled_graph": Jsonb(compiled)},
    )
    return True


def main() -> None:
    """Runs the Compiler worker forever, polling for content_approved rows."""
    logging.basicConfig(level=logging.INFO)
    worker_id = uuid.uuid4().hex
    kg_extractor = build_kg_extractor()
    check_structured_output(kg_extractor)

    while True:
        try:
            with db.from_env() as conn:
                while True:
                    did_compile = compile_pending(conn, kg_extractor, worker_id)
                    if not did_compile:
                        time.sleep(_POLL_DELAY_SECONDS)
        except Exception:
            logger.exception(
                "compiler worker failed; retrying after %.0f seconds", _RETRY_DELAY_SECONDS
            )
            time.sleep(_RETRY_DELAY_SECONDS)
            continue


if __name__ == "__main__":
    main()
