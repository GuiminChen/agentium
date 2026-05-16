"""Unit tests for OpenAI-style streaming on ``DeepSeekChatCompletionClient``."""

from __future__ import annotations

import pytest

from agentium.ai_gateway.deepseek_chat import DeepSeekChatCompletionClient


class _LinesResp:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = lines

    def readline(self) -> bytes:
        return self._lines.pop(0) if self._lines else b""

    def close(self) -> None:
        pass


def test_iter_complete_chat_yields_concatenated_content(monkeypatch: pytest.MonkeyPatch) -> None:
    lines = [
        b'data: {"choices":[{"delta":{"content":"hel"}}]}' + b"\n\n",
        b'data: {"choices":[{"delta":{"content":"lo"},"finish_reason":"stop"}]}' + b"\n\n",
        b"data: [DONE]\n\n",
    ]

    def fake_urlopen(req: object, timeout: float) -> _LinesResp:  # noqa: ARG001
        del req, timeout
        return _LinesResp(list(lines))

    monkeypatch.setattr(
        "agentium.ai_gateway.deepseek_chat.urllib.request.urlopen",
        fake_urlopen,
    )

    cli = DeepSeekChatCompletionClient(
        api_key="secret",
        base_url="https://example.invalid",
        model="m",
        timeout_seconds=5.0,
    )
    deltas = list(
        cli.iter_complete_chat(
            [{"role": "user", "content": "ping"}],
            trace_id="t",
            request_id="r",
        )
    )
    assert "".join(d.content for d in deltas if d.content) == "hello"
    assert any(d.finish_reason == "stop" for d in deltas)
