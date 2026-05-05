"""Chat HTTP request schema smoke tests."""

from __future__ import annotations

from agentium.api.http.chat_schemas import ChatMessageSendRequest


def test_chat_message_send_defaults_mcp_tier() -> None:
    req = ChatMessageSendRequest.model_validate(
        {"session_id": "s1", "content": "hi", "stream": False},
    )
    assert req.mcp_execution_tier == "direct-tool"


def test_chat_message_send_accepts_mcp_tier() -> None:
    req = ChatMessageSendRequest.model_validate(
        {
            "session_id": "s1",
            "content": "hi",
            "mcp_execution_tier": "code-exec-mcp",
            "stream": False,
        },
    )
    assert req.mcp_execution_tier == "code-exec-mcp"
