"""Tests for session-namespaced wiki logical paths."""

from __future__ import annotations

import pytest

from crate.stores.wiki_paths import session_path_prefix, wiki_logical_path_for_blob


def test_wiki_logical_path_for_blob_without_session() -> None:
    assert wiki_logical_path_for_blob(blob_key="notes/a.md") == "raw/notes/a.md"


def test_wiki_logical_path_for_blob_with_session() -> None:
    assert (
        wiki_logical_path_for_blob(blob_key="notes/a.md", session_id="sess-1")
        == "sessions/sess-1/raw/notes/a.md"
    )


def test_wiki_logical_path_normalizes_backslashes_and_leading_slash() -> None:
    assert wiki_logical_path_for_blob(blob_key=r"\foo\bar.md") == "raw/foo/bar.md"


def test_wiki_logical_path_rejects_traversal() -> None:
    with pytest.raises(ValueError):
        wiki_logical_path_for_blob(blob_key="../evil.md")


def test_session_path_prefix() -> None:
    assert session_path_prefix("abc") == "sessions/abc/"


def test_session_path_prefix_invalid() -> None:
    with pytest.raises(ValueError):
        session_path_prefix("a/b")
