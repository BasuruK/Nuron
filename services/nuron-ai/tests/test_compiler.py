test_compiler.py 426L cognitive
// /Users/basuruk/Dev/Nuron/Nuron/services/nuron-ai/tests/test_compiler.py
§ block block (L1-L24)
"""Tests for compiler.py: content_approved -> compiled, typed triples with provenance (NU-008)."""

import os
import uuid
from collections.abc import Iterator, Sequence
from datetime import date
from typing import Any
from unittest.mock import MagicMock

import psycopg
import pytest
from llama_index.core.graph_stores.types import KG_NODES_KEY, KG_RELATIONS_KEY, EntityNode, Relation
from llama_index.core.schema import BaseNode

from nuron_ai import compiler, db
from nuron_ai.compiler import (
    check_structured_output,
    compile_graph,
    compile_pending,
    enrich_and_serialize,
)
from nuron_ai.core import content_hash


§ type _StubExtractor (L25-L27)
class _StubExtractor:
    """A GraphExtractor stand-in returning canned nodes/relations -- no LLM involved."""

§ function __init__ (L28-L37)
    def __init__(
        self,
        nodes: list[EntityNode] | None = None,
        relations: list[Relation] | None = None,
        *,
        raises: Exception | None = None,
    ) -> None:
        self._nodes = nodes or []
        self._relations = relations or []
        self._raises = raises
// ... 1 lines omitted
§ function __call__(self (L39-L45)
    def __call__(self, nodes: Sequence[BaseNode], **kwargs: Any) -> list[BaseNode]:
        if self._raises is not None:
            raise self._raises
        [node] = nodes
        node.metadata[KG_NODES_KEY] = self._nodes
        node.metadata[KG_RELATIONS_KEY] = self._relations
        return [node]
§ comment comment (L46-L50)


# -- enrich_and_serialize: pure logic, no LLM or DB -------------------------------


§ test test_enrich_stamps_author_author_source_timestamp_and_lists_decision_in_decisions (L51-L69)
def test_enrich_stamps_author_author_source_timestamp_and_lists_decision_in_decisions() -> None:
    decision = EntityNode(label="DECISION", name="drop session store")

    compiled = enrich_and_serialize(
        [decision],
        [],
        content_hash="a" * 64,
        reviewed_source_id=1,
        author="Basuru",
        author_source="extracted",
        document_date=date(2026, 5, 14),
        body="irrelevant",
    )

    [node] = compiled["nodes"]
    assert node["properties"]["author"] == "Basuru"
    assert node["properties"]["author_source"] == "extracted"
    assert node["properties"]["timestamp"] == "2026-05-14"
    assert compiled["decisions"] == [node["key"]]
// ... 2 lines omitted
§ test test_enrich_stamps_provenance_on_every_node_regardless_of_label (L72-L91)
def test_enrich_stamps_provenance_on_every_node_regardless_of_label() -> None:
    decision = EntityNode(label="DECISION", name="drop session store")
    entity = EntityNode(label="ENTITY", name="rate limiter")

    compiled = enrich_and_serialize(
        [decision, entity],
        [],
        content_hash="b" * 64,
        reviewed_source_id=7,
        author=None,
        author_source=None,
        document_date=None,
        body="irrelevant",
    )

    for node in compiled["nodes"]:
        assert node["properties"]["provenance"] == {"content_hash": "b" * 64, "reviewed_source_id": 7}
    # only the DECISION node gets author/timestamp fields
    entity_node = next(n for n in compiled["nodes"] if n["label"] == "ENTITY")
    assert "author" not in entity_node["properties"]
// ... 2 lines omitted
§ test test_enrich_computes_evidence_span_when_evidence_text_found_in_body (L94-L111)
def test_enrich_computes_evidence_span_when_evidence_text_found_in_body() -> None:
    body = "Basuru decided to drop the session store. Signed off in the auth review."
    evidence = EntityNode(label="EVIDENCE", name="Signed off in the auth review.")

    compiled = enrich_and_serialize(
        [evidence],
        [],
        content_hash="c" * 64,
        reviewed_source_id=1,
        author=None,
        author_source=None,
        document_date=None,
        body=body,
    )

    [node] = compiled["nodes"]
    start = body.index(evidence.name)
    assert node["properties"]["evidence_span"] == [start, start + len(evidence.name)]
// ... 2 lines omitted
§ test test_enrich_omits_evidence_span_when_evidence_text_not_found_in_body (L114-L129)
def test_enrich_omits_evidence_span_when_evidence_text_not_found_in_body() -> None:
    evidence = EntityNode(label="EVIDENCE", name="text that never appears verbatim")

    compiled = enrich_and_serialize(
        [evidence],
        [],
        content_hash="d" * 64,
        reviewed_source_id=1,
        author=None,
        author_source=None,
        document_date=None,
        body="completely unrelated body text",
    )

    [node] = compiled["nodes"]
    assert "evidence_span" not in node["properties"]
9/30 chunks shown (966 tokens)
[lean-ctx] full source: read "/Users/basuruk/Dev/Nuron/Nuron/services/nuron-ai/tests/test_compiler.py" directly (no MCP)  ·  or ctx_read("/Users/basuruk/Dev/Nuron/Nuron/services/nuron-ai/tests/test_compiler.py", mode="full")
