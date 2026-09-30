"""Tests for persist.py: Neo4j alias matching, delta apply, refcounted provenance (NU-009)."""

from typing import Any
from unittest.mock import MagicMock

import psycopg
import pytest
from llama_index.core.graph_stores.types import EntityNode

from nuron_ai import persist
from nuron_ai.core import natural_key
from nuron_ai.persist import (
    EmbeddingConfigurationMismatchError,
    merge_gate_pending,
    persist_compiled_graph,
    persist_pending,
)

_MODEL_ID = "test-model"
_DIMENSIONS = 3


def _node(label: str, name: str, extra_properties: dict[str, Any] | None = None) -> dict[str, Any]:
    """Builds a compiled_graph node dict as compiler.py's enrich_and_serialize would."""
    properties: dict[str, Any] = {"provenance": {"content_hash": "a" * 64, "reviewed_source_id": 1}}
    if extra_properties:
        properties.update(extra_properties)
    return {"key": natural_key(name, label), "label": label, "name": name, "properties": properties}


def _graph(nodes: list[dict[str, Any]], relations: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Builds a compiled_graph dict as compiler.py's compile_graph would return it."""
    return {"nodes": nodes, "relations": relations or [], "decisions": []}


def _existing_entity(
    node_key: str,
    label: str,
    *,
    display_name: str,
    refs: list[str],
    model_id: str = _MODEL_ID,
    dimensions: int = _DIMENSIONS,
    extra_properties: dict[str, Any] | None = None,
) -> EntityNode:
    """Builds an EntityNode as Neo4jPropertyGraphStore.get() would return it (id-keyed by name)."""
    properties: dict[str, Any] = {
        "display_name": display_name,
        "provenance_refs": refs,
        "embedding_model_id": model_id,
        "embedding_dimensions": dimensions,
    }
    if extra_properties:
        properties.update(extra_properties)
    return EntityNode(name=node_key, label=label, properties=properties)


def _embed_that_must_not_run(text: str) -> list[float]:
    """An Embedder stub for tests where a call means the "don't re-embed" guarantee broke."""
    raise AssertionError(f"this node must not be re-embedded, but embed() was called with {text!r}")


def _structured_query_calls(store: MagicMock, marker: str) -> list[Any]:
    """Returns every store.structured_query() call whose Cypher text contains marker."""
    matches = []
    for one_call in store.structured_query.call_args_list:
        if marker in str(one_call.args[0]):
            matches.append(one_call)
    return matches


def _persist(
    store: Any,
    *,
    content_hash: str,
    aliases: dict[tuple[str, str], str] | None = None,
    previous_content_hash: str | None = None,
    previous_node_keys: frozenset[str] = frozenset(),
    compiled_graph: dict[str, Any],
    embed: persist.Embedder = _embed_that_must_not_run,
) -> frozenset[str]:
    """Calls persist_compiled_graph with this test module's fixed model id/dimensions."""
    return persist_compiled_graph(
        store,
        embed,
        _MODEL_ID,
        _DIMENSIONS,
        content_hash=content_hash,
        aliases=aliases or {},
        previous_content_hash=previous_content_hash,
        previous_node_keys=previous_node_keys,
        compiled_graph=compiled_graph,
    )


# -- persist_compiled_graph: add ---------------------------------------------------


def test_persist_adds_a_brand_new_node_and_embeds_it() -> None:
    store = MagicMock()
    store.get.return_value = []
    embed_calls: list[str] = []

    def embed(text: str) -> list[float]:
        embed_calls.append(text)
        return [0.1, 0.2]

    current_keys = _persist(
        store,
        content_hash="a" * 64,
        compiled_graph=_graph([_node("ENTITY", "session store")]),
        embed=embed,
    )

    assert current_keys == frozenset({"session store:ENTITY"})
    assert embed_calls == ["session store (ENTITY)"]
    [written] = store.upsert_nodes.call_args.args[0]
    assert written.id == "session store:ENTITY"
    assert written.label == "ENTITY"
    assert written.embedding == [0.1, 0.2]
    assert written.properties["embedding_model_id"] == _MODEL_ID
    assert written.properties["embedding_dimensions"] == _DIMENSIONS
    assert written.properties["display_name"] == "session store"
    # provenance is never part of the content upsert -- it's added atomically, separately, so a
    # concurrent writer touching the same node's ref set can never be clobbered by this write.
    assert "provenance_refs" not in written.properties
    [add_ref_call] = _structured_query_calls(store, "SET n.provenance_refs = refs")
    assert add_ref_call.kwargs["param_map"] == {
        "node_key": "session store:ENTITY",
        "ref_to_add": "a" * 64,
        "ref_to_remove": "",
    }


# -- persist_compiled_graph: update (A6) -------------------------------------------


def test_persist_updates_changed_content_and_swaps_the_provenance_ref() -> None:
    prior_hash = "a" * 64
    new_hash = "b" * 64
    existing = _existing_entity(
        "drop session store:DECISION",
        "DECISION",
        display_name="drop session store",
        refs=[prior_hash],
        extra_properties={"author": "Basuru", "timestamp": "2026-05-14"},
    )
    store = MagicMock()
    store.get.return_value = [existing]

    current_keys = _persist(
        store,
        content_hash=new_hash,
        previous_content_hash=prior_hash,
        previous_node_keys=frozenset({"drop session store:DECISION"}),
        compiled_graph=_graph(
            [_node("DECISION", "drop session store", {"author": "Basuru", "timestamp": "2026-05-15"})]
        ),
        embed=lambda text: [0.9],
    )

    assert current_keys == frozenset({"drop session store:DECISION"})
    [written] = store.upsert_nodes.call_args.args[0]
    assert written.properties["timestamp"] == "2026-05-15"
    assert written.embedding == [0.9]
    [add_ref_call] = _structured_query_calls(store, "SET n.provenance_refs = refs")
    assert add_ref_call.kwargs["param_map"] == {
        "node_key": "drop session store:DECISION",
        "ref_to_add": new_hash,
        "ref_to_remove": prior_hash,
    }
    # author/timestamp are present on both versions -- nothing to clear.
    assert _structured_query_calls(store, "REMOVE n[stale_key]") == []


def test_persist_removes_a_property_the_new_version_no_longer_carries() -> None:
    # upsert_nodes's own Cypher is a merge (`SET e += ...`): it would leave a stale `author` on
    # the node forever if we relied on it alone once the new content drops that property.
    prior_hash = "a" * 64
    new_hash = "b" * 64
    existing = _existing_entity(
        "undated decision:DECISION",
        "DECISION",
        display_name="undated decision",
        refs=[prior_hash],
        extra_properties={"author": "Basuru", "timestamp": "2026-05-14"},
    )
    store = MagicMock()
    store.get.return_value = [existing]

    _persist(
        store,
        content_hash=new_hash,
        previous_content_hash=prior_hash,
        previous_node_keys=frozenset({"undated decision:DECISION"}),
        # the reviewer removed the author/date on re-approval -- compiler.py still emits the
        # keys, just set to None, which _content_from_compiled_node drops entirely.
        compiled_graph=_graph([_node("DECISION", "undated decision", {"author": None, "timestamp": None})]),
        embed=lambda text: [0.5],
    )

    [remove_call] = _structured_query_calls(store, "REMOVE n[stale_key]")
    assert remove_call.kwargs["param_map"] == {
        "node_key": "undated decision:DECISION",
        "stale_keys": ["author", "timestamp"],
    }


def test_persist_treats_a_none_valued_property_as_absent_so_it_still_counts_as_unchanged() -> None:
    # compiler.py always sets author/author_source/timestamp on a DECISION, even to None when
    # the reviewed source had no attribution -- Neo4j has no null property, so a round-tripped
    # node never carries that key. Comparing against a dict that still has {"author": None}
    # would wrongly call this "update" and re-embed it on every single persist.
    existing = _existing_entity(
        "undated decision:DECISION", "DECISION", display_name="undated decision", refs=["a" * 64]
    )
    store = MagicMock()
    store.get.return_value = [existing]

    current_keys = _persist(
        store,
        content_hash="a" * 64,
        compiled_graph=_graph(
            [_node("DECISION", "undated decision", {"author": None, "author_source": None, "timestamp": None})]
        ),
    )

    assert current_keys == frozenset({"undated decision:DECISION"})
    store.upsert_nodes.assert_not_called()  # true no-op: unchanged, and the ref is already there.
    store.structured_query.assert_not_called()


# -- persist_compiled_graph: unchanged, cross-document auto-join (A9) -------------


def test_persist_leaves_an_auto_joined_node_unchanged_but_adds_the_new_ref() -> None:
    hash_a = "a" * 64
    hash_b = "b" * 64
    existing = _existing_entity(
        "session store:ENTITY", "ENTITY", display_name="session store", refs=[hash_a]
    )
    store = MagicMock()
    store.get.return_value = [existing]

    current_keys = _persist(
        store,
        content_hash=hash_b,
        compiled_graph=_graph([_node("ENTITY", "session store")]),
    )

    assert current_keys == frozenset({"session store:ENTITY"})
    # unchanged: no content rewrite at all, only the provenance set changes.
    store.upsert_nodes.assert_not_called()
    [add_ref_call] = _structured_query_calls(store, "SET n.provenance_refs = refs")
    assert add_ref_call.kwargs["param_map"] == {
        "node_key": "session store:ENTITY",
        "ref_to_add": hash_b,
        "ref_to_remove": "",
    }


def test_persist_is_a_true_no_op_when_the_ref_already_present() -> None:
    hash_a = "a" * 64
    existing = _existing_entity(
        "session store:ENTITY", "ENTITY", display_name="session store", refs=[hash_a]
    )
    store = MagicMock()
    store.get.return_value = [existing]

    _persist(store, content_hash=hash_a, compiled_graph=_graph([_node("ENTITY", "session store")]))

    store.upsert_nodes.assert_not_called()
    store.structured_query.assert_not_called()


# -- persist_compiled_graph: drop_ref / refcounting (A7) ---------------------------


def test_persist_deletes_a_node_when_its_last_ref_is_released() -> None:
    prior_hash = "a" * 64
    existing = _existing_entity(
        "session store:ENTITY", "ENTITY", display_name="session store", refs=[prior_hash]
    )
    store = MagicMock()
    store.get.return_value = [existing]

    _persist(
        store,
        content_hash="c" * 64,
        previous_content_hash=prior_hash,
        previous_node_keys=frozenset({"session store:ENTITY"}),
        compiled_graph=_graph([]),  # this version no longer contributes the entity
    )

    # The conditional delete-when-empty is inside the atomic Cypher itself (verified directly
    # against a live Neo4j instance -- see the docstring on _RELEASE_REF_QUERY); a mock can only
    # confirm the release was routed correctly, not which branch the database took.
    [release_call] = _structured_query_calls(store, "DETACH DELETE n")
    assert release_call.kwargs["param_map"] == {"node_key": "session store:ENTITY", "ref": prior_hash}
    store.upsert_nodes.assert_not_called()
    store.delete.assert_not_called()


def test_persist_survives_ref_release_when_another_document_still_holds_a_ref() -> None:
    prior_hash = "a" * 64
    other_hash = "b" * 64
    existing = _existing_entity(
        "session store:ENTITY",
        "ENTITY",
        display_name="session store",
        refs=[prior_hash, other_hash],
    )
    store = MagicMock()
    store.get.return_value = [existing]

    _persist(
        store,
        content_hash="c" * 64,
        previous_content_hash=prior_hash,
        previous_node_keys=frozenset({"session store:ENTITY"}),
        compiled_graph=_graph([]),
    )

    store.delete.assert_not_called()
    store.upsert_nodes.assert_not_called()
    [release_call] = _structured_query_calls(store, "DETACH DELETE n")
    assert release_call.kwargs["param_map"] == {"node_key": "session store:ENTITY", "ref": prior_hash}


def test_persist_drop_ref_is_idempotent_when_a_prior_attempt_already_released_it() -> None:
    # The worker can crash (or lose its lease) after this Neo4j write lands but before the
    # Postgres bookkeeping that follows it commits -- a retry must not re-release a ref that's
    # already gone (tracer-bullet-01.md: the Neo4j write is "self-correcting" against live state).
    other_hash = "b" * 64
    existing = _existing_entity(
        "session store:ENTITY", "ENTITY", display_name="session store", refs=[other_hash]
    )
    store = MagicMock()
    store.get.return_value = [existing]

    _persist(
        store,
        content_hash="c" * 64,
        previous_content_hash="a" * 64,  # already absent from existing's refs
        previous_node_keys=frozenset({"session store:ENTITY"}),
        compiled_graph=_graph([]),
    )

    store.delete.assert_not_called()
    store.upsert_nodes.assert_not_called()
    store.structured_query.assert_not_called()


# -- ADR-0004: pinned embedding model/dimensions -----------------------------------


def test_persist_rejects_a_node_embedded_with_a_different_model_id() -> None:
    existing = _existing_entity(
        "session store:ENTITY",
        "ENTITY",
        display_name="session store",
        refs=["a" * 64],
        model_id="golden-test-double-v1",
    )
    store = MagicMock()
    store.get.return_value = [existing]

    with pytest.raises(EmbeddingConfigurationMismatchError, match="golden-test-double-v1"):
        _persist(
            store,
            content_hash="b" * 64,
            compiled_graph=_graph([_node("ENTITY", "session store")]),
        )
    store.upsert_nodes.assert_not_called()


def test_persist_rejects_a_node_embedded_at_a_different_dimension_count() -> None:
    existing = _existing_entity(
        "session store:ENTITY",
        "ENTITY",
        display_name="session store",
        refs=["a" * 64],
        dimensions=1536,
    )
    store = MagicMock()
    store.get.return_value = [existing]

    with pytest.raises(EmbeddingConfigurationMismatchError, match="1536"):
        _persist(
            store,
            content_hash="b" * 64,
            compiled_graph=_graph([_node("ENTITY", "session store")]),
        )
    store.upsert_nodes.assert_not_called()


# -- resolve_key / aliases ----------------------------------------------------------


def test_persist_matches_through_a_confirmed_alias_survivor() -> None:
    survivor = "session store:ENTITY"
    aliases = {("sessions table", "ENTITY"): survivor}
    # Same display name as the document below contributes -- this test is about resolve_key
    # finding the survivor node at all, not about the update/unchanged content classification.
    existing = _existing_entity(survivor, "ENTITY", display_name="Sessions Table", refs=["a" * 64])
    store = MagicMock()
    store.get.return_value = [existing]

    current_keys = _persist(
        store,
        content_hash="a" * 64,
        aliases=aliases,
        compiled_graph=_graph([_node("ENTITY", "Sessions Table")]),
    )

    assert current_keys == frozenset({survivor})
    store.get.assert_called_once()
    assert set(store.get.call_args.kwargs["ids"]) == {survivor}


# -- relations -----------------------------------------------------------------------


def test_persist_upserts_relations_resolved_through_the_same_keys_as_their_nodes() -> None:
    store = MagicMock()
    store.get.return_value = []
    decision = _node("DECISION", "drop session store")
    entity = _node("ENTITY", "rate limiter")
    relation = {
        "label": "AFFECTS",
        "source_key": decision["key"],
        "target_key": entity["key"],
        "properties": {},
    }

    _persist(
        store,
        content_hash="a" * 64,
        compiled_graph=_graph([decision, entity], [relation]),
        embed=lambda text: [0.0],
    )

    [written] = store.upsert_relations.call_args.args[0]
    assert written.source_id == "drop session store:DECISION"
    assert written.target_id == "rate limiter:ENTITY"


def test_persist_skips_a_relation_referencing_a_node_missing_from_this_compile(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = MagicMock()
    store.get.return_value = []
    decision = _node("DECISION", "drop session store")
    relation = {
        "label": "AFFECTS",
        "source_key": decision["key"],
        "target_key": "nonexistent:ENTITY",
        "properties": {},
    }

    with caplog.at_level("WARNING"):
        _persist(
            store,
            content_hash="a" * 64,
            compiled_graph=_graph([decision], [relation]),
            embed=lambda text: [0.0],
        )

    store.upsert_relations.assert_not_called()
    assert "missing from this compile" in caplog.text


# -- persist_pending: claim / release semantics, mocked Postgres ------------------


def _claimed_row(digest: str, filename: str, *, lease_token: int = 3) -> tuple[Any, ...]:
    """A db.claim RETURNING tuple: hash, filename, title, author, author_source, date, tags, body, lease_token."""
    return (digest, filename, None, None, None, None, [], "body", lease_token)


def _make_conn(
    *,
    digest: str,
    filename: str,
    compiled_graph: dict[str, Any] | None,
    previous_content_hash: str | None = None,
    previous_reviewed_source_id: int = 7,
    previous_node_keys: list[str] | None = None,
) -> MagicMock:
    """A mocked psycopg.Connection dispatching persist_pending's queries by SQL substring."""
    conn = MagicMock(spec=psycopg.Connection)

    def execute(query: object, params: object = None) -> MagicMock:
        text = str(query)
        result = MagicMock()
        result.rowcount = 1
        if "SET claimed_by" in text:
            result.fetchone.return_value = _claimed_row(digest, filename)
        elif "SELECT compiled_graph" in text:
            result.fetchone.return_value = None if compiled_graph is None else (compiled_graph,)
        elif "rs_prev.content_hash" in text:
            result.fetchone.return_value = None if previous_content_hash is None else (previous_content_hash,)
        elif "SELECT node_key FROM nuron_ai.node_provenance" in text:
            result.fetchall.return_value = [(key,) for key in (previous_node_keys or [])]
        elif "FROM nuron_ai.reviewed_sources" in text:
            result.fetchone.return_value = (previous_reviewed_source_id,)
        elif "entity_aliases" in text:
            result.fetchall.return_value = []
        return result

    conn.execute.side_effect = execute
    return conn


def _calls_matching(conn: MagicMock, substring: str) -> list[Any]:
    """Returns every conn.execute() call whose SQL text contains substring."""
    matches = []
    for one_call in conn.execute.call_args_list:
        if substring in str(one_call.args[0]):
            matches.append(one_call)
    return matches


def test_persist_pending_returns_false_when_the_queue_is_empty() -> None:
    conn = MagicMock(spec=psycopg.Connection)
    conn.execute.return_value.fetchone.return_value = None

    assert persist_pending(conn, MagicMock(), _embed_that_must_not_run, _MODEL_ID, _DIMENSIONS, "worker-1") is False


def test_persist_pending_persists_a_first_version_and_transitions_to_persisted() -> None:
    digest = "a" * 64
    compiled_graph = _graph([_node("ENTITY", "session store")])
    conn = _make_conn(digest=digest, filename="b.md", compiled_graph=compiled_graph)
    store = MagicMock()
    store.get.return_value = []

    assert persist_pending(conn, store, lambda text: [0.0], _MODEL_ID, _DIMENSIONS, "worker-1") is True

    insert_calls = _calls_matching(conn, "INSERT INTO nuron_ai.node_provenance")
    assert len(insert_calls) == 1
    assert insert_calls[0].args[1] == {
        "node_key": "session store:ENTITY",
        "content_hash": digest,
        "reviewed_source_id": 7,
    }
    assert _calls_matching(conn, "DELETE FROM nuron_ai.node_provenance") == []
    release_call = conn.execute.call_args_list[-1]
    assert "state = 'persisted'" in str(release_call.args[0])


def test_persist_pending_releases_the_old_versions_provenance_on_reapproval() -> None:
    digest = "b" * 64
    prior_hash = "a" * 64
    compiled_graph = _graph([_node("ENTITY", "session store")])
    conn = _make_conn(
        digest=digest,
        filename="a.md",
        compiled_graph=compiled_graph,
        previous_content_hash=prior_hash,
        previous_reviewed_source_id=9,
        previous_node_keys=["session store:ENTITY"],
    )
    store = MagicMock()
    store.get.return_value = [
        _existing_entity("session store:ENTITY", "ENTITY", display_name="session store", refs=[prior_hash])
    ]

    assert persist_pending(conn, store, lambda text: [0.0], _MODEL_ID, _DIMENSIONS, "worker-1") is True

    delete_calls = _calls_matching(conn, "DELETE FROM nuron_ai.node_provenance")
    assert delete_calls[0].args[1] == {"content_hash": prior_hash}


def test_persist_pending_retries_when_persist_compiled_graph_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    digest = "a" * 64
    compiled_graph = _graph([_node("ENTITY", "session store")])
    conn = _make_conn(digest=digest, filename="b.md", compiled_graph=compiled_graph)
    monkeypatch.setattr(
        persist,
        "persist_compiled_graph",
        MagicMock(side_effect=ConnectionError("neo4j unreachable")),
    )

    assert persist_pending(conn, MagicMock(), _embed_that_must_not_run, _MODEL_ID, _DIMENSIONS, "worker-1") is True

    release_call = conn.execute.call_args_list[-1]
    assert "attempt_count = attempt_count + 1" in str(release_call.args[0])


def test_persist_pending_retries_when_the_compiled_graph_row_is_missing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    digest = "a" * 64
    conn = _make_conn(digest=digest, filename="b.md", compiled_graph=None)

    with caplog.at_level("ERROR"):
        result = persist_pending(conn, MagicMock(), _embed_that_must_not_run, _MODEL_ID, _DIMENSIONS, "worker-1")
        assert result is True

    assert any(record.levelname == "ERROR" and digest in record.message for record in caplog.records)
    release_call = conn.execute.call_args_list[-1]
    assert "attempt_count = attempt_count + 1" in str(release_call.args[0])


def test_persist_pending_fails_terminally_on_an_embedding_configuration_mismatch(
    caplog: pytest.LogCaptureFixture,
) -> None:
    digest = "b" * 64
    compiled_graph = _graph([_node("ENTITY", "session store")])
    conn = _make_conn(digest=digest, filename="b.md", compiled_graph=compiled_graph)
    store = MagicMock()
    store.get.return_value = [
        _existing_entity(
            "session store:ENTITY", "ENTITY", display_name="session store", refs=["a" * 64], dimensions=1536
        )
    ]

    with caplog.at_level("WARNING"):
        assert persist_pending(conn, store, _embed_that_must_not_run, _MODEL_ID, _DIMENSIONS, "worker-1") is True

    release_sql = str(conn.execute.call_args_list[-1].args[0])
    assert "state = 'failed'" in release_sql
    assert "attempt_count" not in release_sql
    assert any(record.levelname == "WARNING" and digest in record.message for record in caplog.records)
    store.upsert_nodes.assert_not_called()


# -- merge_gate_pending: compiled -> awaiting_merge_confirm ----------------------


def test_merge_gate_pending_returns_false_when_nothing_is_compiled() -> None:
    conn = MagicMock(spec=psycopg.Connection)
    conn.execute.return_value.fetchone.return_value = None

    assert merge_gate_pending(conn, "worker-1") is False


def test_merge_gate_pending_claims_compiled_and_releases_to_awaiting_merge_confirm() -> None:
    conn = MagicMock(spec=psycopg.Connection)
    conn.execute.return_value.fetchone.return_value = _claimed_row("c" * 64, "c.md")
    conn.execute.return_value.rowcount = 1

    assert merge_gate_pending(conn, "worker-1") is True

    claim_call, release_call = conn.execute.call_args_list
    assert claim_call.args[1]["state"] == "compiled"
    assert "state = 'awaiting_merge_confirm'" in str(release_call.args[0])
    assert release_call.args[1]["content_hash"] == "c" * 64
