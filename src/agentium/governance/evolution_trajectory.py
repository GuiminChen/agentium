"""Trajectory contract + sanitization for :class:`~agentium.governance.evolution_plugin.EvolutionPlugin`.

HTTP ingestion and DeepResearch completion paths must only feed bounded, reviewer-safe
structures into learning plugins (no raw secrets, no arbitrary blobs).
"""

from __future__ import annotations

import re
from typing import Any

from agentium.governance.evolution_plugin import TrajectoryBatch, TrajectoryEvent

MAX_TRAJECTORY_EVENTS = 64
MAX_STEP_TYPE_LEN = 128
MAX_RUN_ID_LEN = 256
MAX_PAYLOAD_TOP_KEYS = 48
MAX_PAYLOAD_DEPTH = 4
MAX_SCALAR_STR = 4096

_STEP_TYPE_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9._-]{0,127}$")


def _sanitize_value(val: Any, depth: int) -> Any:
    if depth <= 0:
        return None
    if val is None or isinstance(val, (bool, int, float)):
        return val
    if isinstance(val, str):
        if len(val) > MAX_SCALAR_STR:
            return val[: MAX_SCALAR_STR - 3] + "..."
        return val
    if isinstance(val, list):
        out: list[Any] = []
        for item in val[:32]:
            out.append(_sanitize_value(item, depth - 1))
        return out
    if isinstance(val, dict):
        out_d: dict[str, Any] = {}
        for i, (k, v) in enumerate(val.items()):
            if i >= MAX_PAYLOAD_TOP_KEYS:
                break
            if not isinstance(k, str):
                continue
            sk = k[:128]
            out_d[sk] = _sanitize_value(v, depth - 1)
        return out_d
    return str(val)[:256]


def _sanitize_payload(data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    trimmed: dict[str, Any] = {}
    for i, (k, v) in enumerate(data.items()):
        if i >= MAX_PAYLOAD_TOP_KEYS:
            break
        if not isinstance(k, str) or not k.strip():
            continue
        trimmed[k[:128]] = _sanitize_value(v, MAX_PAYLOAD_DEPTH)
    return trimmed


def sanitize_trajectory_batch(batch: TrajectoryBatch) -> TrajectoryBatch:
    """Return a reconstructed, bounded trajectory or raise ``ValueError``."""

    if len(batch.run_id) > MAX_RUN_ID_LEN or not batch.run_id.strip():
        raise ValueError("invalid run_id length")
    if len(batch.events) > MAX_TRAJECTORY_EVENTS:
        raise ValueError(f"at most {MAX_TRAJECTORY_EVENTS} events allowed")
    cleaned: list[TrajectoryEvent] = []
    for ev in batch.events:
        st = ev.step_type.strip()
        if not st or len(st) > MAX_STEP_TYPE_LEN:
            raise ValueError("invalid step_type")
        if not _STEP_TYPE_PATTERN.match(st):
            raise ValueError("step_type has invalid characters")
        cleaned.append(
            TrajectoryEvent(step_type=st, payload=_sanitize_payload(dict(ev.payload)))
        )
    return TrajectoryBatch(run_id=batch.run_id.strip(), events=cleaned)


__all__ = [
    "MAX_TRAJECTORY_EVENTS",
    "sanitize_trajectory_batch",
]
