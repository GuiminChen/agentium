"""Helpers for ``GET /v1/chat/sessions/{id}/memory`` query semantics."""

from __future__ import annotations


def parse_memory_scopes_query(raw: str | None) -> tuple[bool, bool]:
    """Parse ``scopes`` query into ``(include_session, include_user)``.

    Tokens are comma-separated, case-insensitive:

    - Empty / omitted → ``(True, False)`` (session-scoped rows only; backward compatible).
    - ``session`` / ``user`` may combine (e.g. ``session,user``).
    - ``all`` → ``(True, True)``.

    Args:
        raw: Raw query substring from ``scopes=``.

    Returns:
        Flags controlling which recall paths merge into the response.
    """

    tokens = [t.strip().lower() for t in (raw or "").split(",") if t.strip()]
    if not tokens:
        return True, False
    if "all" in tokens:
        return True, True
    return ("session" in tokens, "user" in tokens)


__all__ = ["parse_memory_scopes_query"]
