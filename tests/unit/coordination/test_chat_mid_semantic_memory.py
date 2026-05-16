"""Tests for scoped MID semantics extraction + session summary helpers."""

from __future__ import annotations

from agentium.ai_gateway.deepseek_chat import DeepSeekCompletionResult
from agentium.coordination.chat_mid_semantic_memory import (
    extract_mid_term_memories,
    extract_session_running_summary,
    normalize_fact_key,
    parse_scoped_memories_json,
    stable_fact_hash,
    stable_user_fact_hash,
)


def test_parse_scoped_json_objects() -> None:
    raw = (
        '{"memories":[{"text":"Task-local step done","scope":"session"},'
        '{"text":"User prefers concise replies","scope":"user"}]}'
    )
    assert parse_scoped_memories_json(raw) == [
        ("Task-local step done", "session"),
        ("User prefers concise replies", "user"),
    ]


def test_parse_scoped_json_legacy_string_list() -> None:
    raw = '{"memories": ["Only session by default", "Also session"]}'
    assert parse_scoped_memories_json(raw) == [
        ("Only session by default", "session"),
        ("Also session", "session"),
    ]


def test_parse_scoped_markdown_fence() -> None:
    raw = '```json\n{"memories":[{"text":"x","scope":"session"}]}\n```'
    assert parse_scoped_memories_json(raw) == [("x", "session")]


def test_parse_scoped_invalid_returns_empty() -> None:
    assert parse_scoped_memories_json("not json") == []
    assert parse_scoped_memories_json('{"memories": "bad"}') == []


def test_stable_user_fact_hash_stable() -> None:
    a = stable_user_fact_hash("t1", "u1", "likes vim")
    b = stable_user_fact_hash("t1", "u1", "likes vim")
    c = stable_user_fact_hash("t1", "u2", "likes vim")
    assert a == b and a != c


def test_normalize_fact_key_collapses_whitespace() -> None:
    assert normalize_fact_key("  A   B  ") == "a b"


def test_stable_fact_hash_stable() -> None:
    a = stable_fact_hash("s1", "hello")
    b = stable_fact_hash("s1", "hello")
    c = stable_fact_hash("s2", "hello")
    assert a == b and a != c


def test_extract_mid_term_memories_empty_turns() -> None:
    class _Stub:
        def complete_chat(self, *args: object, **kwargs: object) -> DeepSeekCompletionResult:
            raise AssertionError("should not call LLM on empty turns")

    assert (
        extract_mid_term_memories(
            _Stub(),  # type: ignore[arg-type]
            user_turn_text="",
            assistant_turn_text="",
            trace_id="t",
            request_id="r",
            model_override=None,
        )
        == []
    )


def test_extract_mid_term_memories_parses_model_output() -> None:
    class _Stub:
        def complete_chat(self, *args: object, **kwargs: object) -> DeepSeekCompletionResult:
            return DeepSeekCompletionResult(
                text='{"memories":[{"text":"Fact one","scope":"session"}]}',
                raw_finish_reason="stop",
            )

    facts = extract_mid_term_memories(
        _Stub(),  # type: ignore[arg-type]
        user_turn_text="ping",
        assistant_turn_text="pong",
        trace_id="t",
        request_id="r",
        model_override=None,
    )
    assert facts == [("Fact one", "session")]


def test_extract_session_running_summary_stub() -> None:
    class _Stub:
        def complete_chat(self, *args: object, **kwargs: object) -> DeepSeekCompletionResult:
            return DeepSeekCompletionResult(
                text="### Goal\nDo thing\n### Progress\n(none)\n",
                raw_finish_reason="stop",
            )

    text = extract_session_running_summary(
        _Stub(),  # type: ignore[arg-type]
        user_turn_text="hi",
        assistant_turn_text="hello",
        trace_id="t",
        request_id="r",
        model_override=None,
    )
    assert "### Goal" in text
