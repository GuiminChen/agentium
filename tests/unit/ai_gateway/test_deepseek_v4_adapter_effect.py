"""Deterministic checks for DeepSeek-V4 adapter behaviour (DSML fallback + thinking payload).

Paper alignment: ``docs/paper/arxiv/main.tex`` evaluates governance hypotheses H1--H6 and
control-plane microbenchmarks—not proprietary LLM scores. These tests therefore assert
**integration-path** properties (tool-call recovery, request envelope), not answer quality.

Client ``model`` here matches ``AGENTIUM_CHAT_MODEL`` default ``deepseek-v4-flash`` (official V4
also exposes ``deepseek-v4-pro``); offline tests mock HTTP and never contact the API.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Deque, Dict

import pytest

from agentium.ai_gateway.deepseek_chat import (
    DeepSeekChatCompletionClient,
    DeepSeekThinkingCompletionOptions,
)


def _dsml_noop_content() -> str:
    return (
        "thought.\n"
        "<|DSML|tool_calls>\n"
        '<|DSML|invoke name="echo_micro">\n'
        '<|DSML|parameter name="text" string="true">hello</|DSML|parameter>\n'
        "</|DSML|invoke>\n"
        "</|DSML|tool_calls>\n"
    )


def _choice_dsml_only() -> Dict[str, Any]:
    return {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": _dsml_noop_content(),
                },
            }
        ]
    }


def _choice_native_tool() -> Dict[str, Any]:
    return {
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {"name": "echo_micro", "arguments": "{\"text\":\"x\"}"},
                        }
                    ],
                },
            }
        ]
    }


@pytest.fixture()
def patched_client(monkeypatch: pytest.MonkeyPatch) -> DeepSeekChatCompletionClient:
    queue: Deque[Dict[str, Any]] = deque()

    def fake_post(
        self: DeepSeekChatCompletionClient,
        payload: Dict[str, Any],
        *,
        trace_id: str,
        request_id: str,
        message_count: int,
        tools_present: bool,
        thinking_enabled: bool,
    ) -> Dict[str, Any]:
        del self, payload, trace_id, request_id, message_count, tools_present, thinking_enabled
        return queue.popleft()

    monkeypatch.setattr(DeepSeekChatCompletionClient, "_post_completion", fake_post)
    client = DeepSeekChatCompletionClient(
        api_key="sk-test",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
        timeout_seconds=30.0,
    )
    client._response_queue = queue  # type: ignore[attr-defined]
    return client


def test_dsml_fallback_recovers_tool_calls_when_native_empty(
    patched_client: DeepSeekChatCompletionClient,
) -> None:
    queue: Deque[Dict[str, Any]] = patched_client._response_queue  # type: ignore[attr-defined]
    queue.append(_choice_dsml_only())
    tools = [
        {
            "type": "function",
            "function": {
                "name": "echo_micro",
                "description": "echo",
                "parameters": {"type": "object", "additionalProperties": True},
            },
        }
    ]
    no_fallback = patched_client.complete_chat_round(
        [{"role": "user", "content": "hi"}],
        tools=tools,
        trace_id="t",
        request_id="r1",
        dsml_fallback=False,
    )
    assert no_fallback.tool_calls == []

    queue.append(_choice_dsml_only())
    with_fallback = patched_client.complete_chat_round(
        [{"role": "user", "content": "hi"}],
        tools=tools,
        trace_id="t",
        request_id="r2",
        dsml_fallback=True,
    )
    assert len(with_fallback.tool_calls) == 1
    fn = with_fallback.tool_calls[0].get("function") or {}
    assert fn.get("name") == "echo_micro"
    args = fn.get("arguments")
    assert isinstance(args, str)
    assert "hello" in args


def test_native_tool_calls_unchanged_with_dsml_fallback_enabled(
    patched_client: DeepSeekChatCompletionClient,
) -> None:
    queue: Deque[Dict[str, Any]] = patched_client._response_queue  # type: ignore[attr-defined]
    queue.append(_choice_native_tool())
    tools = [
        {
            "type": "function",
            "function": {"name": "echo_micro", "description": "echo", "parameters": {"type": "object"}},
        }
    ]
    rr = patched_client.complete_chat_round(
        [{"role": "user", "content": "hi"}],
        tools=tools,
        trace_id="t",
        request_id="r3",
        dsml_fallback=True,
    )
    assert len(rr.tool_calls) == 1
    assert rr.tool_calls[0].get("id") == "c1"


def test_thinking_payload_includes_reasoning_effort_not_temperature() -> None:
    client = DeepSeekChatCompletionClient(
        api_key="sk-test",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
        timeout_seconds=30.0,
    )
    on = client._base_payload(
        [],
        thinking=DeepSeekThinkingCompletionOptions(enabled=True, reasoning_effort="max"),
        model_override=None,
    )
    assert on["reasoning_effort"] == "max"
    assert on["thinking"] == {"type": "enabled"}
    assert "temperature" not in on

    off = client._base_payload([], thinking=None, model_override=None)
    assert off.get("temperature") == 0.7
    assert "reasoning_effort" not in off
    assert "thinking" not in off
