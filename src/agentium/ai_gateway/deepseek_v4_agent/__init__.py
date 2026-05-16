"""DeepSeek-V4 agent-facing adapters (official API): DSML hints, Think Max system text."""

from agentium.ai_gateway.deepseek_v4_agent.dsml import (
    build_dsml_tool_system_addon,
    dsml_tool_calls_to_openai_tool_calls,
    extract_dsml_tool_block,
)
from agentium.ai_gateway.deepseek_v4_agent.model_gate import is_deepseek_v4_series_model
from agentium.ai_gateway.deepseek_v4_agent.think_max import THINK_MAX_SYSTEM_INSTRUCTION

__all__ = [
    "THINK_MAX_SYSTEM_INSTRUCTION",
    "build_dsml_tool_system_addon",
    "dsml_tool_calls_to_openai_tool_calls",
    "extract_dsml_tool_block",
    "is_deepseek_v4_series_model",
]
