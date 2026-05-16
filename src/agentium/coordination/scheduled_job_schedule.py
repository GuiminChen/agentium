"""Trigger math for persisted scheduled jobs (interval, one_shot, optional cron).

Cron uses ``croniter`` when installed (``pip install '.[cron]'``); expressions are evaluated in **UTC**.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional


def _utc_ms_to_dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)


def _dt_to_utc_ms(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def validate_cron_expression(expr: str) -> None:
    """Raise ``ValueError`` if expression is invalid or ``croniter`` is missing."""

    raw = (expr or "").strip()
    if not raw:
        raise ValueError("cron_expression_required")
    try:
        from croniter import croniter  # type: ignore[import-untyped]
    except ImportError as exc:
        raise ValueError(
            "croniter_package_required: install agentium with optional dependency [cron] "
            "(pip install 'agentium[cron]')"
        ) from exc
    try:
        base = datetime.now(timezone.utc)
        croniter(raw, base).get_next(datetime)
    except Exception as exc:
        raise ValueError(f"invalid_cron_expression:{exc}") from exc


def next_unix_ms_after(trigger: Dict[str, Any], *, after_unix_ms: int) -> Optional[int]:
    """Next fire strictly after ``after_unix_ms`` (exclusive floor), or ``None`` for terminal one_shot."""

    kind = trigger.get("kind")
    if kind == "interval":
        interval_s = max(60, int(trigger.get("interval_seconds", 60)))
        return after_unix_ms + interval_s * 1000
    if kind == "one_shot":
        return None
    if kind == "cron":
        expr = str(trigger.get("cron_expression") or "").strip()
        validate_cron_expression(expr)
        from croniter import croniter  # type: ignore[import-untyped]

        base = _utc_ms_to_dt(after_unix_ms)
        it = croniter(expr, base)
        nxt = it.get_next(datetime)
        ms = _dt_to_utc_ms(nxt)
        if ms <= after_unix_ms:
            nxt2 = it.get_next(datetime)
            ms = _dt_to_utc_ms(nxt2)
        return ms
    raise ValueError(f"unsupported_trigger_kind:{kind!r}")


def initial_next_unix_ms(trigger: Dict[str, Any], *, now_unix_ms: int) -> Optional[int]:
    """Earliest fire time for a newly created or rescheduled job."""

    kind = trigger.get("kind")
    if kind == "interval":
        interval_s = max(60, int(trigger.get("interval_seconds", 60)))
        return now_unix_ms + interval_s * 1000
    if kind == "one_shot":
        return int(trigger["run_at_unix_ms"])
    if kind == "cron":
        expr = str(trigger.get("cron_expression") or "").strip()
        validate_cron_expression(expr)
        from croniter import croniter  # type: ignore[import-untyped]

        base = _utc_ms_to_dt(now_unix_ms)
        nxt = croniter(expr, base).get_next(datetime)
        return _dt_to_utc_ms(nxt)
    raise ValueError(f"unsupported_trigger_kind:{kind!r}")


__all__ = [
    "initial_next_unix_ms",
    "next_unix_ms_after",
    "validate_cron_expression",
]
