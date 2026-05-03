"""Constants for DLP-related audit payloads (PRD §3.12.1).

The PRD describes five-linked scanning phases: **ingress → pre_context_build →
pre_tool → post_llm → egress**, each able to emit audit independently.

This repository does **not** yet instrument all phases. The ToolRegistry path
runs DLP on **tool output** after handler execution and before the result is
returned to the caller / model loop. We label that slice ``tool_output_post`` in
audit payloads so operators can distinguish it from future ``ingress``,
``pre_tool``, ``post_llm``, and ``egress`` stages without overstating coverage.
"""

from __future__ import annotations

# Tool output scan after handler returns; not the full PRD "post_llm" surface.
DLP_AUDIT_STAGE_TOOL_OUTPUT_POST: str = "tool_output_post"

__all__ = ["DLP_AUDIT_STAGE_TOOL_OUTPUT_POST"]
