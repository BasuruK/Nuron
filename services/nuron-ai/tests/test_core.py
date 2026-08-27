import hashlib
from datetime import date
from pathlib import Path

from nuron_ai.core import (
    EntityAlias,
    NodeDelta,
    content_hash,
    merge_alias,
    natural_key,
    normalize,
    object_key,
    parse_header,
    plan_delta,
    release_ref,
    resolve_key,
)

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "watched"


# -- content_hash / object_key -------------------------------------------------


def test_content_hash_is_sha256_hex_digest():
    data = b"hello world"
    assert content_hash(data) == hashlib.sha256(data).hexdigest()


def test_content_hash_is_content_sensitive():
    assert content_hash(b"these bytes") != content_hash(b"other bytes")


def test_object_key_is_two_char_prefix_then_full_digest():
    digest = content_hash(b"some file contents")
    assert object_key(digest) == f"{digest[:2]}/{digest}"


# -- parse_header ---------------------------------------------------------------


def test_parse_header_reads_in_prose_signature():
    text = FIXTURES.joinpath("auth-token-decision.md").read_text()
    header = parse_header(text, filename="auth-token-decision.md", source_owner=None)

    assert header.author == "Basuru"
    assert header.author_source == "extracted"
    assert header.timestamp == date(2026, 5, 14)
    assert header.subject == "Moving off server-side sessions"
    assert header.tags == []


def test_parse_header_reads_frontmatter_title_and_tags():
    text = FIXTURES.joinpath("cdn-token-caching.md").read_text()
    header = parse_header(text, filename="cdn-token-caching.md", source_owner=None)

    assert header.subject == "CDN cache keys and signed URLs"
    assert header.tags == ["performance", "auth", "infrastructure"]
    # No author anywhere in this fixture -- nothing to extract, no fallback given.
    assert header.author is None
    assert header.author_source == "unknown"


def test_parse_header_frontmatter_author_wins_over_signature():
    text = (
        "---\n"
        "author: Frontmatter Author\n"
        "---\n"
        "Body text.\n\n"
        "— Signature Author, 2026-01-01\n"
    )
    header = parse_header(text, filename="doc.md", source_owner=None)

    assert header.author == "Frontmatter Author"
    assert header.author_source == "extracted"


def test_parse_header_signature_survives_en_dash_from_markdown_conversion():
    # CONTEXT.md: the signature regex "must survive markdown conversion so the same
    # rule works on LlamaParse output (A11)" -- PDF-to-markdown commonly emits en-dash.
    text = "# Notes\n\nBody text.\n\n– Signature Author, 2026-01-01\n"
    header = parse_header(text, filename="doc.md", source_owner=None)

    assert header.author == "Signature Author"
    assert header.author_source == "extracted"
    assert header.timestamp == date(2026, 1, 1)


def test_parse_header_falls_back_to_source_owner_when_undocumented():
    text = "# Some Notes\n\nNo attribution anywhere in this body.\n"
    header = parse_header(text, filename="notes.md", source_owner="Fallback Owner")

    assert header.author == "Fallback Owner"
    assert header.author_source == "default"


def test_parse_header_falls_back_to_filename_date():
    text = "# Some Notes\n\nNo dates anywhere in the body or frontmatter.\n"
    header = parse_header(text, filename="notes-2025-03-02.md", source_owner=None)

    assert header.timestamp == date(2025, 3, 2)


def test_parse_header_never_consults_mtime_leaves_blank_when_nothing_matches():
    text = "# Some Notes\n\nNothing to extract.\n"
    header = parse_header(text, filename="notes.md", source_owner=None)

    assert header.timestamp is None
    assert header.author is None
    assert header.author_source == "unknown"


# -- normalize / natural_key / resolve_key / merge_alias ------------------------


def test_normalize_case_folds_and_trims_whitespace():
    assert normalize("  Session   Store  ") == "session store"


def test_normalize_does_not_merge_different_referents():
    assert normalize("session store") != normalize("sessions table")


def test_natural_key_combines_normalized_name_and_label():
    assert natural_key("Session Store", "ENTITY") == "session store:ENTITY"


def test_resolve_key_falls_back_to_natural_key_without_alias():
    assert resolve_key("Session Store", "ENTITY", aliases={}) == natural_key(
        "Session Store", "ENTITY"
    )


def test_resolve_key_uses_confirmed_alias_survivor():
    survivor = natural_key("session store", "ENTITY")
    aliases = {("sessions table", "ENTITY"): survivor}

    assert resolve_key("Sessions Table", "ENTITY", aliases=aliases) == survivor


def test_merge_alias_builds_record_pointing_at_survivor():
    alias = merge_alias("Sessions Table", "ENTITY", survivor_name="session store")

    assert alias == EntityAlias(
        alias_key="sessions table",
        label="ENTITY",
        survivor_key="session store:ENTITY",
    )


# -- plan_delta / release_ref -----------------------------------------------------


def test_plan_delta_new_node_is_add_and_needs_embedding():
    deltas = plan_delta(prior={}, current={"n1": {"name": "A"}})

    assert deltas == [NodeDelta("n1", "add", needs_embedding=True)]


def test_plan_delta_unchanged_node_skips_embedding():
    node = {"n1": {"name": "A"}}
    deltas = plan_delta(prior=node, current=node)

    assert deltas == [NodeDelta("n1", "unchanged", needs_embedding=False)]


def test_plan_delta_changed_properties_is_update_and_needs_embedding():
    deltas = plan_delta(
        prior={"n1": {"name": "A", "note": "old"}},
        current={"n1": {"name": "A", "note": "new"}},
    )

    assert deltas == [NodeDelta("n1", "update", needs_embedding=True)]


def test_plan_delta_node_missing_from_current_is_drop_ref():
    deltas = plan_delta(prior={"n1": {"name": "A"}}, current={})

    assert deltas == [NodeDelta("n1", "drop_ref", needs_embedding=False)]


def test_release_ref_keeps_node_alive_while_refs_remain():
    remaining, delete_node = release_ref(frozenset({"doc-a", "doc-b"}), "doc-a")

    assert remaining == frozenset({"doc-b"})
    assert delete_node is False


def test_release_ref_marks_node_for_deletion_when_last_ref_released():
    remaining, delete_node = release_ref(frozenset({"doc-a"}), "doc-a")

    assert remaining == frozenset()
    assert delete_node is True
