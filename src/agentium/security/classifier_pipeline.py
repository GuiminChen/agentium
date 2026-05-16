"""Ordered classifier stages for I/O gating (P1-10 / P1-11)."""

from __future__ import annotations

from typing import List, Sequence


def normalize_classifier_stages(raw: Sequence[str]) -> List[str]:
    """Return a defensive copy with empty tokens removed."""

    out: List[str] = []
    for item in raw:
        token = str(item).strip()
        if token:
            out.append(token)
    return out
