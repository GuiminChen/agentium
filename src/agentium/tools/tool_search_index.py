"""Keyword-style scoring for chat-eligible tools (P1-26 tool_search skeleton)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List, Sequence, Tuple


def _tokenize(text: str) -> List[str]:
    return [t for t in re.split(r"\W+", text.lower()) if len(t) > 1]


@dataclass(frozen=True)
class ToolSearchHit:
    """One tool row returned from ``tool_search``."""

    name: str
    score: float
    snippet: str


def score_query_against_text(query: str, haystack: str) -> float:
    """Return a simple overlap score (term matches in haystack)."""

    q_terms = set(_tokenize(query))
    if not q_terms:
        return 0.0
    h_terms = set(_tokenize(haystack))
    if not h_terms:
        return 0.0
    return float(len(q_terms & h_terms))


def rank_tool_rows(
    rows: Sequence[Tuple[str, str, str]],
    *,
    query: str,
    limit: int,
) -> List[ToolSearchHit]:
    """Rank ``(name, description, concat_haystack)`` tuples and truncate.

    Args:
        rows: Sequence of name, description, and pre-merged search text (name + description).
        query: User query string.
        limit: Max hits (>= 1).
    """

    cap = max(1, int(limit))
    scored: List[Tuple[float, str, str]] = []
    for name, _desc, hay in rows:
        s = score_query_against_text(query, hay)
        scored.append((s, name, hay))
    scored.sort(key=lambda x: (-x[0], x[1]))
    out: List[ToolSearchHit] = []
    for s, name, hay in scored[:cap]:
        snippet = hay.strip().replace("\n", " ")
        if len(snippet) > 200:
            snippet = snippet[:197] + "..."
        out.append(ToolSearchHit(name=name, score=s, snippet=snippet))
    return out


def stable_initial_exposed(names: Iterable[str], *, limit: int) -> List[str]:
    """Deterministic first slice when defer_loading activates (sorted names)."""

    cap = max(1, int(limit))
    return sorted({str(n).strip() for n in names if str(n).strip()})[:cap]
