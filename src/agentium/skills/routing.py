"""Heuristic ranking of skills for a natural-language query.

Production systems may replace :func:`rank_skills_for_query` with embedding
similarity or an LLM router; this module provides a deterministic baseline
that matches short queries against ``name`` + ``description`` tokens and a
few file-extension hints so tests and demos run without a model.
"""

from __future__ import annotations

import re
from typing import Dict, List, Sequence, Tuple

from agentium.skills.manifest import SkillManifest

_WORD = re.compile(r"[a-z0-9]+", re.IGNORECASE)

# Map substring in query (lowercase) -> skill_id boost when extension-like hints appear.
_EXTENSION_HINTS: Dict[str, str] = {
    ".docx": "docx",
    ".pdf": "pdf",
    ".pptx": "pptx",
    ".xlsx": "xlsx",
    ".xlsm": "xlsx",
    ".csv": "xlsx",
    ".tsv": "xlsx",
}

# Light synonym boosts: token -> skill_id
_TOKEN_SKILL_BOOST: Dict[str, str] = {
    "word": "docx",
    "powerpoint": "pptx",
    "spreadsheet": "xlsx",
    "excel": "xlsx",
    "slides": "pptx",
    "deck": "pptx",
    "presentation": "pptx",
}


def _tokens(text: str) -> set[str]:
    return {m.group(0).lower() for m in _WORD.finditer(text) if len(m.group(0)) >= 2}


def _corpus_tokens(skill: SkillManifest) -> set[str]:
    parts = skill.name.replace("-", " ").replace("_", " ")
    return _tokens(parts) | _tokens(skill.description)


def _extension_boosts(query_lower: str) -> Dict[str, float]:
    boosts: Dict[str, float] = {}
    for ext, skill_id in _EXTENSION_HINTS.items():
        if ext in query_lower:
            boosts[skill_id] = boosts.get(skill_id, 0.0) + 12.0
    return boosts


def _token_boosts(query_toks: set[str]) -> Dict[str, float]:
    boosts: Dict[str, float] = {}
    for tok, skill_id in _TOKEN_SKILL_BOOST.items():
        if tok in query_toks:
            boosts[skill_id] = boosts.get(skill_id, 0.0) + 6.0
    return boosts


def rank_skills_for_query(
    query: str,
    skills: Sequence[SkillManifest],
    *,
    top_n: int | None = None,
) -> List[Tuple[SkillManifest, float]]:
    """Score each skill; higher is better. Ties broken alphabetically by skill name."""

    query_lower = query.lower()
    query_toks = _tokens(query)
    ext_boost = _extension_boosts(query_lower)
    tok_boost = _token_boosts(query_toks)

    scored: List[Tuple[SkillManifest, float]] = []
    for skill in skills:
        corpus = _corpus_tokens(skill)
        overlap = len(query_toks & corpus)
        name_toks = _tokens(skill.name.replace("-", " "))
        name_hits = len(query_toks & name_toks)
        score = float(overlap) + 2.0 * float(name_hits)
        score += ext_boost.get(skill.name, 0.0)
        score += tok_boost.get(skill.name, 0.0)
        # MCP / skill-authoring nudges
        if skill.name == "mcp-builder" and (
            "mcp" in query_toks or "model context protocol" in query_lower
        ):
            score += 8.0
        if skill.name == "skill-creator" and (
            "skill" in query_toks
            or "eval" in query_toks
            or any(t.startswith("eval") for t in query_toks)
        ):
            score += 4.0
        scored.append((skill, score))

    scored.sort(key=lambda x: (-x[1], x[0].name))
    if top_n is not None:
        return scored[:top_n]
    return scored


def primary_skill_for_query(query: str, skills: Sequence[SkillManifest]) -> SkillManifest | None:
    """Return the top-ranked skill, or ``None`` if the catalog is empty."""

    ranked = rank_skills_for_query(query, skills, top_n=1)
    if not ranked:
        return None
    best, score = ranked[0]
    if score <= 0.0:
        return None
    return best
