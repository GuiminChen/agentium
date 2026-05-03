"""Compare two release-gate summary payloads (persisted eval runs)."""

from __future__ import annotations

from typing import Any, Dict, List


def compare_eval_summaries(baseline: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
    """Diff ``results[]`` by gate ``name``; expects collect_release_gate_summary shape."""

    b_results = baseline.get("results") or []
    c_results = candidate.get("results") or []
    if not isinstance(b_results, list):
        b_results = []
    if not isinstance(c_results, list):
        c_results = []

    b_map: Dict[str, Dict[str, Any]] = {}
    for item in b_results:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            b_map[item["name"]] = item
    c_map: Dict[str, Dict[str, Any]] = {}
    for item in c_results:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            c_map[item["name"]] = item

    b_names = frozenset(b_map)
    c_names = frozenset(c_map)
    only_baseline = sorted(b_names - c_names)
    only_candidate = sorted(c_names - b_names)
    changed: List[Dict[str, Any]] = []
    for name in sorted(b_names & c_names):
        br, cr = b_map[name], c_map[name]
        bp = br.get("passed")
        cp = cr.get("passed")
        bd = br.get("duration_ms")
        cd = cr.get("duration_ms")
        if bp != cp or bd != cd:
            changed.append(
                {
                    "name": name,
                    "baseline": {"passed": bp, "duration_ms": bd},
                    "candidate": {"passed": cp, "duration_ms": cd},
                }
            )

    return {
        "only_baseline": only_baseline,
        "only_candidate": only_candidate,
        "changed": changed,
    }


__all__ = ["compare_eval_summaries"]
