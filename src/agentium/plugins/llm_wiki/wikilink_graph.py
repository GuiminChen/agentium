"""Build Obsidian-style wikilink graphs from markdown bodies (server-side).

Resolves ``[[target]]`` and ``[[target|alias]]`` against known ``logical_path``s.
Unresolved targets appear as dangling ``_orphan/<hash12>`` nodes so the frontend
still renders Obsidian-like edges.

Design note: deterministic resolution prefers exact paths, suffix matches,
then basename match within the supplied page corpus (typically bounded by HTTP
``max_pages``).
"""

from __future__ import annotations

import hashlib
import re
from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

_WIKILINK = re.compile(
    r"\[\[\s*([^\]#|]+?)\s*(?:\|\s*([^\]]+?)\s*)?\]\]",
    re.MULTILINE,
)


def extract_wikilinks(body: str) -> List[Tuple[str, Optional[str]]]:
    """Return ``(wikilink_target, display_alias_optional)`` from markdown."""

    pairs: List[Tuple[str, Optional[str]]] = []
    for m in _WIKILINK.finditer(body or ""):
        tgt = (m.group(1) or "").strip()
        alias_raw = (m.group(2) or "").strip()
        alias = alias_raw if alias_raw else None
        if tgt:
            pairs.append((tgt, alias))
    return pairs


def resolve_wikilink_target(target: str, known_paths: Set[str]) -> Optional[str]:
    """Map a wikilink target string to ``logical_path`` or ``None``."""

    raw = target.strip().replace("\\", "/").lstrip("/")
    if not raw:
        return None
    segments = raw.split("/")
    if ".." in segments:
        return None

    if raw in known_paths:
        return raw

    if "/" in raw:
        for kp in known_paths:
            if kp.endswith("/" + raw) or kp.endswith("/" + raw + ".md") or kp == raw:
                return kp
        return None

    names_to_try = [raw]
    if not raw.endswith(".md"):
        names_to_try.append(raw + ".md")

    stem = PurePosixPath(raw).stem
    for name in dict.fromkeys(names_to_try):
        for kp in known_paths:
            if PurePosixPath(kp).name == name:
                return kp

    if stem != raw:
        for kp in known_paths:
            pn = PurePosixPath(kp).name
            if PurePosixPath(pn).stem == stem or pn == stem + ".md":
                return kp

    for kp in known_paths:
        if kp.endswith("/" + raw) or kp.endswith("/" + raw + ".md"):
            return kp
    return None


def build_wiki_graph_payload(*, pages: Sequence[Any]) -> Dict[str, Any]:
    """Produce ``nodes`` / ``edges`` for Control Plane UI (React Flow-ready).

    Each *pages* row must expose ``logical_path`` and ``body_md`` attributes
    (:class:`~crate.stores.wiki_database.WikiPageRecord`).

    Returns:
        Dict with ``nodes: List[dict]`` (``id``, ``label``, ``path``) and
        ``edges: List[dict]`` (``source``, ``target``).
    """

    known: Set[str] = {str(getattr(p, "logical_path")) for p in pages}
    nodes: Dict[str, Dict[str, str]] = {}
    edges_set: Set[Tuple[str, str]] = set()

    for p in pages:
        lp = str(getattr(p, "logical_path"))
        label = PurePosixPath(lp).name or lp
        nodes[lp] = {"id": lp, "label": label, "path": lp}

    orphan_map: Dict[str, str] = {}

    def orphan_node_id(raw_target: str) -> str:
        if raw_target not in orphan_map:
            digest = hashlib.sha256(raw_target.encode("utf-8")).hexdigest()[:12]
            oid = f"_orphan/{digest}"
            orphan_map[raw_target] = oid
            short = raw_target.strip()[:120]
            nodes[oid] = {"id": oid, "label": short or oid, "path": ""}
        return orphan_map[raw_target]

    for p in pages:
        lp = str(getattr(p, "logical_path"))
        body = str(getattr(p, "body_md", "") or "")
        for target, _alias in extract_wikilinks(body):
            resolved = resolve_wikilink_target(target, known)
            tgt_id = resolved if resolved else orphan_node_id(target.strip())
            if tgt_id != lp:
                edges_set.add((lp, tgt_id))

    edges = [{"source": a, "target": b} for a, b in sorted(edges_set)]
    node_list = [nodes[k] for k in sorted(nodes.keys())]
    return {"nodes": node_list, "edges": edges}


__all__ = [
    "build_wiki_graph_payload",
    "extract_wikilinks",
    "resolve_wikilink_target",
]
