"""Unit tests for wikilink graph builder."""

from __future__ import annotations

from dataclasses import dataclass

from agentium.plugins.llm_wiki.wikilink_graph import (
    build_wiki_graph_payload,
    extract_wikilinks,
    resolve_wikilink_target,
)


@dataclass
class _FakePage:
    logical_path: str
    body_md: str


def test_extract_wikilinks_pipe_and_plain() -> None:
    body = "See [[foo/bar]] and [[x|Display]] end."
    pairs = extract_wikilinks(body)
    assert pairs[0] == ("foo/bar", None)
    assert pairs[1][0] == "x"
    assert pairs[1][1] == "Display"


def test_resolve_basename() -> None:
    known = {"sessions/s1/raw/a.md", "notes/Deep.md"}
    assert resolve_wikilink_target("Deep", known) == "notes/Deep.md"
    assert resolve_wikilink_target("Deep.md", known) == "notes/Deep.md"


def test_build_graph_includes_orphan() -> None:
    pages = [
        _FakePage("a.md", "link [[missing]]"),
        _FakePage("b.md", "no"),
    ]
    out = build_wiki_graph_payload(pages=pages)
    ids = {n["id"] for n in out["nodes"]}
    assert "a.md" in ids and "b.md" in ids
    orphan = [n for n in out["nodes"] if n["id"].startswith("_orphan/")]
    assert len(orphan) == 1
    assert any(e["source"] == "a.md" for e in out["edges"])
