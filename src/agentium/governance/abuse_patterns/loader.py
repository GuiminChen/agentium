"""Load abuse pattern hints for governance signals (P1-9 MVP)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Mapping, Sequence

import yaml


@dataclass(frozen=True)
class AbusePattern:
    """One configured abuse heuristic."""

    pattern_id: str
    severity: str
    mitigation: str
    needle: str = ""


def load_abuse_patterns(path: Path) -> List[AbusePattern]:
    """Parse a YAML file with a top-level ``patterns`` list."""

    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    rows = raw.get("patterns")
    if not isinstance(rows, list):
        return []
    out: List[AbusePattern] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        pid = str(row.get("id", "")).strip()
        if not pid:
            continue
        out.append(
            AbusePattern(
                pattern_id=pid,
                severity=str(row.get("severity", "info")).strip(),
                mitigation=str(row.get("mitigation", "")).strip(),
                needle=str(row.get("needle", "")).strip(),
            )
        )
    return out


def match_abuse_patterns(haystack: str, patterns: Sequence[AbusePattern]) -> List[str]:
    """Return pattern ids whose ``needle`` substring appears in ``haystack``."""

    matched: List[str] = []
    for p in patterns:
        if p.needle and p.needle in haystack:
            matched.append(p.pattern_id)
    return matched
