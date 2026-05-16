"""Chat HTTP request schema smoke tests."""

from __future__ import annotations

from agentium.api.http.chat_schemas import ChatMessageSendRequest


def test_chat_message_send_defaults_mcp_tier() -> None:
    req = ChatMessageSendRequest.model_validate(
        {"session_id": "s1", "content": "hi", "stream": False},
    )
    assert req.mcp_execution_tier == "direct-tool"
    assert req.enable_agent_tools is False


def test_chat_message_send_accepts_enable_agent_tools() -> None:
    req = ChatMessageSendRequest.model_validate(
        {
            "session_id": "s1",
            "content": "hi",
            "stream": False,
            "enable_agent_tools": True,
        },
    )
    assert req.enable_agent_tools is True


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


def test_chat_message_send_accepts_deepseek_overrides() -> None:
    req = ChatMessageSendRequest.model_validate(
        {
            "session_id": "s1",
            "content": "hi",
            "stream": False,
            "deepseek_thinking_enabled": False,
            "deepseek_reasoning_effort": "max",
        },
    )
    assert req.deepseek_thinking_enabled is False
    assert req.deepseek_reasoning_effort == "max"


def test_chat_message_send_auto_ingress_default_false() -> None:
    req = ChatMessageSendRequest.model_validate({"session_id": "s1", "content": "hi", "stream": False})
    assert req.auto_ingress is False


def test_chat_message_send_regenerate_allows_empty_content() -> None:
    req = ChatMessageSendRequest.model_validate(
        {
            "session_id": "s1",
            "content": "",
            "stream": False,
            "regenerate_from_message_id": "pair-1",
        },
    )
    assert req.regenerate_from_message_id == "pair-1"
