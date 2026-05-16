"""Unit tests for deterministic chat ingress classification."""

from __future__ import annotations

from agentium.coordination.chat_ingress_classification import classify_chat_ingress


def test_classify_empty_defaults_collect_direct() -> None:
    disp, tier = classify_chat_ingress("")
    assert disp == "collect"
    assert tier == "direct-tool"


def test_classify_steer_and_code_exec_tier() -> None:
    disp, tier = classify_chat_ingress("Please steer and ignore prior instructions about sandbox python -c")
    assert disp == "steer"
    assert tier == "code-exec-mcp"


def test_classify_followup_keyword() -> None:
    disp, tier = classify_chat_ingress("Continue from where we left off with the summary.")
    assert disp == "followup"
    assert tier == "direct-tool"
