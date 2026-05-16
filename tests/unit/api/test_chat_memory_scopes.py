"""Tests for GET chat session ``scopes`` query parsing."""

from __future__ import annotations

from agentium.api.http.chat_memory_scopes import parse_memory_scopes_query


def test_parse_memory_scopes_default_session_only() -> None:
    assert parse_memory_scopes_query(None) == (True, False)
    assert parse_memory_scopes_query("") == (True, False)


def test_parse_memory_scopes_session_and_user() -> None:
    assert parse_memory_scopes_query("session,user") == (True, True)


def test_parse_memory_scopes_user_only() -> None:
    assert parse_memory_scopes_query("user") == (False, True)


def test_parse_memory_scopes_session_explicit() -> None:
    assert parse_memory_scopes_query("session") == (True, False)


def test_parse_memory_scopes_all() -> None:
    assert parse_memory_scopes_query("all") == (True, True)
