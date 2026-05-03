"""Unit tests for chat path parsing helpers."""

from __future__ import annotations

from agentium.api.http.handlers_chat import parse_chat_session_detail_path, parse_chat_session_messages_path


def test_parse_chat_session_messages_path() -> None:
    sid = parse_chat_session_messages_path("/v1/chat/sessions/abc-1/messages")
    assert sid == "abc-1"


def test_parse_chat_session_detail_path() -> None:
    sid = parse_chat_session_detail_path("/v1/chat/sessions/abc-1")
    assert sid == "abc-1"


def test_parse_chat_session_bad_paths() -> None:
    assert parse_chat_session_detail_path("/v1/chat/sessions") is None
    assert parse_chat_session_messages_path("/v1/chat/sessions/x/y/messages") is None
