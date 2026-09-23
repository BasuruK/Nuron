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


class _StubExtractor:
    """A GraphExtractor stand-in returning canned nodes/relations -- no LLM involved."""

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

    def __call__(self, nodes: Sequence[BaseNode], **kwargs: Any) -> list[BaseNode]:
        if self._raises is not None:
            raise self._raises
        [node] = nodes
        node.metadata[KG_NODES_KEY] = self._nodes
        node.metadata[KG_RELATIONS_KEY] = self._relations
        return [node]


# -- enrich_and_serialize: pure logic, no LLM or DB -------------------------------


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


def test_enrich_dedupes_the_same_entity_appearing_in_multiple_triplets() -> None:
    # SchemaLLMPathExtractor appends one EntityNode per triplet it participates in.
    session_store_a = EntityNode(label="ENTITY", name="session store")
    session_store_b = EntityNode(label="ENTITY", name="session store")
    rate_limiter = EntityNode(label="ENTITY", name="rate limiter")

    compiled = enrich_and_serialize(
        [session_store_a, rate_limiter, session_store_b],
        [],
        content_hash="e" * 64,
        reviewed_source_id=1,
        author=None,
        author_source=None,
        document_date=None,
        body="irrelevant",
    )

    keys = [node["key"] for node in compiled["nodes"]]
    assert sorted(keys) == sorted({"session store:ENTITY", "rate limiter:ENTITY"})


def test_enrich_rejects_entity_id_that_maps_to_two_natural_keys() -> None:
    # EntityNode.id is the name, so these share an id and disagree on natural key.
    # A relation is present so a silent overwrite would compile instead of raising.
    entity = EntityNode(label="ENTITY", name="atlas")
    decision = EntityNode(label="DECISION", name="atlas")
    relation = Relation(label="AFFECTS", source_id=entity.id, target_id=decision.id)

    with pytest.raises(ValueError, match="already maps to 'atlas:ENTITY', not 'atlas:DECISION'"):
        enrich_and_serialize(
            [entity, decision],
            [relation],
            content_hash="e" * 64,
            reviewed_source_id=1,
            author=None,
            author_source=None,
            document_date=None,
            body="irrelevant",
        )


def test_enrich_resolves_relations_to_natural_keys_and_dedupes_duplicates() -> None:
    decision = EntityNode(label="DECISION", name="drop session store")
    entity = EntityNode(label="ENTITY", name="rate limiter")
    relation = Relation(label="AFFECTS", source_id=decision.id, target_id=entity.id)
    duplicate = Relation(label="AFFECTS", source_id=decision.id, target_id=entity.id)

    compiled = enrich_and_serialize(
        [decision, entity],
        [relation, duplicate],
        content_hash="f" * 64,
        reviewed_source_id=1,
        author=None,
        author_source=None,
        document_date=None,
        body="irrelevant",
    )

    assert compiled["relations"] == [
        {
            "label": "AFFECTS",
            "source_key": "drop session store:DECISION",
            "target_key": "rate limiter:ENTITY",
            "properties": {},
        }
    ]


# -- compile_graph: wires body/provenance through a stub extractor ---------------


def test_compile_graph_enriches_the_extractors_output() -> None:
    decision = EntityNode(label="DECISION", name="drop session store")
    extractor = _StubExtractor(nodes=[decision])

    compiled = compile_graph(
        extractor,
        body="Basuru decided to drop the session store.",
        content_hash="a" * 64,
        reviewed_source_id=3,
        author="Basuru",
        author_source="extracted",
        document_date=date(2026, 5, 14),
    )

    [node] = compiled["nodes"]
    assert node["properties"]["provenance"] == {"content_hash": "a" * 64, "reviewed_source_id": 3}
    assert compiled["decisions"] == ["drop session store:DECISION"]


def test_compile_graph_warns_when_a_decision_has_no_evidenced_by_relation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    decision = EntityNode(label="DECISION", name="drop session store")
    extractor = _StubExtractor(nodes=[decision])

    with caplog.at_level("WARNING"):
        compile_graph(
            extractor,
            body="irrelevant",
            content_hash="a" * 64,
            reviewed_source_id=1,
            author=None,
            author_source=None,
            document_date=None,
        )

    assert "no EVIDENCED_BY relation" in caplog.text


def test_compile_graph_does_not_warn_when_a_decision_has_an_evidenced_by_relation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    decision = EntityNode(label="DECISION", name="drop session store")
    evidence = EntityNode(label="EVIDENCE", name="signed off in the auth review")
    relation = Relation(label="EVIDENCED_BY", source_id=decision.id, target_id=evidence.id)
    extractor = _StubExtractor(nodes=[decision, evidence], relations=[relation])

    with caplog.at_level("WARNING"):
        compile_graph(
            extractor,
            body="irrelevant",
            content_hash="a" * 64,
            reviewed_source_id=1,
            author=None,
            author_source=None,
            document_date=None,
        )

    assert "no EVIDENCED_BY relation" not in caplog.text


# -- check_structured_output ------------------------------------------------------


def test_check_structured_output_passes_when_the_extractor_returns_entities() -> None:
    extractor = _StubExtractor(nodes=[EntityNode(label="DECISION", name="drop session store")])
    check_structured_output(extractor)  # no raise


def test_check_structured_output_raises_when_the_extractor_returns_no_entities() -> None:
    extractor = _StubExtractor(nodes=[])
    with pytest.raises(RuntimeError, match="structured output check failed"):
        check_structured_output(extractor)


def test_check_structured_output_raises_when_the_extractor_itself_raises() -> None:
    extractor = _StubExtractor(raises=ConnectionError("endpoint unreachable"))
    with pytest.raises(RuntimeError, match="structured output check failed") as raised:
        check_structured_output(extractor)
    assert isinstance(raised.value.__cause__, ConnectionError)


# -- compile_pending: claim / release semantics, mocked Postgres -----------------


def _claimed_row(
    filename: str,
    body: str,
    *,
    author: str | None = None,
    author_source: str | None = None,
    document_date: date | None = None,
) -> tuple[Any, ...]:
    """A db.claim RETURNING tuple: hash, filename, title, author, author_source, date, tags, body, lease_token."""
    return ("a" * 64, filename, None, author, author_source, document_date, [], body, 3)


def test_compile_pending_returns_false_when_the_queue_is_empty() -> None:
    conn = MagicMock(spec=psycopg.Connection)
    conn.execute.return_value.fetchone.return_value = None

    assert compile_pending(conn, _StubExtractor(), "worker-1") is False


def test_compile_pending_writes_the_compiled_graph_and_transitions_state() -> None:
    conn = MagicMock(spec=psycopg.Connection)
    conn.execute.return_value.fetchone.side_effect = [
        _claimed_row("decision.md", "# Decision\n", author="Basuru", author_source="extracted"),
        (7,),
    ]
    conn.execute.return_value.rowcount = 1
    decision = EntityNode(label="DECISION", name="drop session store")
    extractor = _StubExtractor(nodes=[decision])

    claimed = compile_pending(conn, extractor, "worker-1")

    assert claimed is True
    release_call = conn.execute.call_args_list[-1]
    assert "state = 'compiled'" in str(release_call.args[0])
    compiled_graph = release_call.args[1]["compiled_graph"].obj
    assert compiled_graph["decisions"] == ["drop session store:DECISION"]
    assert compiled_graph["nodes"][0]["properties"]["provenance"] == {
        "content_hash": "a" * 64,
        "reviewed_source_id": 7,
    }


def test_compile_pending_retries_when_the_extractor_fails() -> None:
    conn = MagicMock(spec=psycopg.Connection)
    conn.execute.return_value.fetchone.side_effect = [
        _claimed_row("decision.md", "# Decision\n"),
        (7,),
    ]
    conn.execute.return_value.rowcount = 1
    extractor = _StubExtractor(raises=ConnectionError("endpoint unreachable"))

    claimed = compile_pending(conn, extractor, "worker-1")

    assert claimed is True
    retry_call = conn.execute.call_args_list[-1]
    assert "attempt_count = attempt_count + 1" in str(retry_call.args[0])


def test_compile_pending_logs_releases_and_continues_when_the_reviewed_source_row_is_missing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    conn = MagicMock(spec=psycopg.Connection)
    conn.execute.return_value.fetchone.side_effect = [
        _claimed_row("decision.md", "# Decision\n"),
        None,
    ]
    conn.execute.return_value.rowcount = 1

    with caplog.at_level("ERROR"):
        claimed = compile_pending(conn, _StubExtractor(), "worker-1")

    assert claimed is True
    assert any(
        record.levelname == "ERROR" and "a" * 64 in record.message and "reviewed_sources" in record.message
        for record in caplog.records
    )
    retry_call = conn.execute.call_args_list[-1]
    assert "attempt_count = attempt_count + 1" in str(retry_call.args[0])
    assert retry_call.args[1]["retry_delay_seconds"] == compiler._RETRY_DELAY_SECONDS
    assert retry_call.args[1]["max_attempts"] == compiler._MAX_ATTEMPTS
    assert retry_call.args[1]["content_hash"] == "a" * 64
    assert retry_call.args[1]["worker_id"] == "worker-1"
    assert retry_call.args[1]["lease_token"] == 3


def test_compile_pending_binds_provenance_to_the_latest_reviewed_source_version() -> None:
    conn = MagicMock(spec=psycopg.Connection)
    stale_reviewed_source_id = 1
    latest_reviewed_source_id = 9

    def execute(query: object, _params: object = None) -> MagicMock:
        result = MagicMock()
        text = str(query)
        if "reviewed_sources" in text:
            picks_latest = "ORDER BY version DESC" in text and "LIMIT 1" in text
            result.fetchone.return_value = (
                (latest_reviewed_source_id,) if picks_latest else (stale_reviewed_source_id,)
            )
            return result
        result.fetchone.return_value = _claimed_row("decision.md", "# Decision\n")
        result.rowcount = 1
        return result

    conn.execute.side_effect = execute
    decision = EntityNode(label="DECISION", name="drop session store")

    assert compile_pending(conn, _StubExtractor(nodes=[decision]), "worker-1") is True

    compiled_graph = conn.execute.call_args_list[-1].args[1]["compiled_graph"].obj
    assert compiled_graph["nodes"][0]["properties"]["provenance"]["reviewed_source_id"] == (
        latest_reviewed_source_id
    )


def test_main_reuses_one_connection_across_idle_polls_and_reconnects_after_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Connection:
        def __init__(self) -> None:
            self.closed = False

        def __enter__(self) -> "_Connection":
            return self

        def __exit__(self, *_exc: object) -> None:
            self.closed = True

    opened: list[_Connection] = []
    seen: list[_Connection] = []
    outcomes: list[bool | Exception] = [True, False, RuntimeError("boom"), False]
    sleep_delays: list[float] = []

    def connect() -> _Connection:
        if opened:
            assert opened[-1].closed
        conn = _Connection()
        opened.append(conn)
        return conn

    def run_compile(conn: _Connection, _kg_extractor: object, _worker_id: str) -> bool:
        seen.append(conn)
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def sleep(seconds: float) -> None:
        sleep_delays.append(seconds)
        if len(sleep_delays) == 3:
            raise KeyboardInterrupt

    monkeypatch.setattr(compiler, "build_kg_extractor", lambda: object())
    monkeypatch.setattr(compiler, "check_structured_output", lambda _extractor: None)
    monkeypatch.setattr(compiler.db, "from_env", connect)
    monkeypatch.setattr(compiler, "compile_pending", run_compile)
    monkeypatch.setattr(compiler.time, "sleep", sleep)

    with pytest.raises(KeyboardInterrupt):
        compiler.main()

    assert len(opened) == 2
    assert seen == [opened[0], opened[0], opened[0], opened[1]]
    assert sleep_delays == [
        compiler._POLL_DELAY_SECONDS,
        compiler._RETRY_DELAY_SECONDS,
        compiler._POLL_DELAY_SECONDS,
    ]
    assert all(conn.closed for conn in opened)


# -- compile_pending: real Postgres, gated behind infra --------------------------


@pytest.fixture
def db_conn() -> Iterator[psycopg.Connection]:
    if not os.environ.get("POSTGRES_INTEGRATION_TESTS"):
        pytest.skip("POSTGRES_INTEGRATION_TESTS not set -- skipping Postgres integration tests")
    conn = db.from_env()
    try:
        yield conn
    finally:
        conn.close()


def _content_approved(
    conn: psycopg.Connection,
    body: str,
    filename: str,
    *,
    author: str | None = None,
    author_source: str | None = None,
    document_date: date | None = None,
) -> tuple[str, int]:
    """Lands one row directly at content_approved with a matching reviewed_sources row."""
    digest = content_hash(body.encode())
    conn.execute(
        """
        INSERT INTO nuron_ai.documents
            (content_hash, entry_point, original_filename, state, author, author_source,
             document_date, body)
        VALUES (%s, 'watched_directory', %s, 'content_approved', %s, %s, %s, %s)
        """,
        (digest, filename, author, author_source, document_date, body),
    )
    row = conn.execute(
        """
        INSERT INTO nuron_ai.reviewed_sources (content_hash, version, author, author_source,
                                                document_date, body)
        VALUES (%s, 1, %s, %s, %s, %s)
        RETURNING id
        """,
        (digest, author, author_source, document_date, body),
    ).fetchone()
    assert row is not None
    conn.commit()
    return digest, row[0]


def _cleanup(conn: psycopg.Connection, digest: str) -> None:
    conn.execute("DELETE FROM nuron_ai.documents WHERE content_hash = %s", (digest,))
    conn.commit()


def test_compile_pending_takes_a_content_approved_row_to_compiled(
    db_conn: psycopg.Connection,
) -> None:
    body = "Basuru decided to drop the session store for stateless JWT sessions."
    digest, reviewed_source_id = _content_approved(
        db_conn, body, f"decision-{uuid.uuid4().hex}.md", author="Basuru", author_source="extracted"
    )
    decision = EntityNode(label="DECISION", name="drop the session store")
    extractor = _StubExtractor(nodes=[decision])
    try:
        claimed = compile_pending(db_conn, extractor, "worker-1")
        assert claimed is True

        row = db_conn.execute(
            "SELECT state, compiled_graph FROM nuron_ai.documents WHERE content_hash = %s",
            (digest,),
        ).fetchone()
        assert row is not None
        state, compiled_graph = row
        assert state == "compiled"
        assert compiled_graph["decisions"] == ["drop the session store:DECISION"]
        assert compiled_graph["nodes"][0]["properties"]["provenance"] == {
            "content_hash": digest,
            "reviewed_source_id": reviewed_source_id,
        }
    finally:
        _cleanup(db_conn, digest)
