"""Tiered tool approval for chat agent loops (rules + optional classifier)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from agentium.ai_gateway.deepseek_chat import DeepSeekChatCompletionClient, DeepSeekChatCompletionError
from agentium.app.settings import AppSettings


@dataclass(frozen=True)
class ToolApprovalDecision:
    """Structured gate outcome recorded in traces and audits."""

    verdict: str
    reason_code: str
    classifier_stage: Optional[str] = None
    risk_tags: Tuple[str, ...] = ()


ClassifierFn = Callable[..., ToolApprovalDecision]


class ToolApprovalGate:
    """Combine allowlist, regex-style rules, and an optional LLM JSON classifier."""

    def __init__(
        self,
        settings: AppSettings,
        *,
        llm_client: Optional[DeepSeekChatCompletionClient] = None,
        classifier_fn: Optional[ClassifierFn] = None,
    ) -> None:
        self._settings = settings
        self._llm = llm_client
        self._classifier_fn = classifier_fn

    def evaluate(
        self,
        *,
        user_message_excerpt: str,
        tool_name: str,
        arguments: Dict[str, Any],
        tool_allowlist: Optional[Sequence[str]],
        trace_id: str,
        request_id: str,
    ) -> ToolApprovalDecision:
        """Return ``allow`` / ``deny`` / ``pending_human`` for one proposed tool call."""

        if not self._settings.tool_approval_auto_enabled:
            return ToolApprovalDecision("allow", "approval_auto_disabled")
        allow = {str(x).strip() for x in (tool_allowlist or []) if str(x).strip()}
        if tool_name in allow:
            return ToolApprovalDecision("allow", "tier1_allowlist", classifier_stage="rules")
        if self._rule_deny(tool_name, arguments):
            return ToolApprovalDecision("deny", "tier1_rule_deny", classifier_stage="rules")
        if self._classifier_fn is not None:
            return self._classifier_fn(
                user_message_excerpt=user_message_excerpt,
                tool_name=tool_name,
                arguments=arguments,
                trace_id=trace_id,
                request_id=request_id,
            )
        if self._llm is None:
            return ToolApprovalDecision("pending_human", "classifier_unconfigured", classifier_stage="none")
        return self._llm_classify(
            user_message_excerpt=user_message_excerpt,
            tool_name=tool_name,
            arguments=arguments,
            trace_id=trace_id,
            request_id=request_id,
        )

    def _rule_deny(self, tool_name: str, arguments: Dict[str, Any]) -> bool:
        blob = json.dumps(arguments, ensure_ascii=False).lower()
        if "rm -rf" in blob or "format c:" in blob or "mkfs" in blob:
            return True
        if tool_name in self._settings.tool_approval_rule_deny_tools:
            return True
        if self._settings.tool_approval_deny_shell_pattern and re.search(
            self._settings.tool_approval_deny_shell_pattern, blob
        ):
            return True
        return False

    def _fault_decision(self, reason: str) -> ToolApprovalDecision:
        if self._settings.tool_approval_on_fault == "deny":
            return ToolApprovalDecision("deny", reason, classifier_stage="classifier")
        return ToolApprovalDecision("pending_human", reason, classifier_stage="classifier")

    def _llm_classify(
        self,
        *,
        user_message_excerpt: str,
        tool_name: str,
        arguments: Dict[str, Any],
        trace_id: str,
        request_id: str,
    ) -> ToolApprovalDecision:
        model = (
            self._settings.tool_approval_classifier_model or self._settings.chat_completion_model
        ).strip()
        sys_msg = (
            "You approve or reject a single tool call for an enterprise agent. "
            "Respond with ONLY compact JSON: "
            '{"verdict":"allow|deny|pending_human","reason_code":"snake_case",'
            '"risk_tags":["optional"]}. '
            "No markdown, no extra keys."
        )
        payload_user = {
            "user_message_excerpt": user_message_excerpt[:4000],
            "tool_name": tool_name,
            "arguments": arguments,
        }
        messages: List[Dict[str, str]] = [
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": json.dumps(payload_user, ensure_ascii=False)},
        ]
        try:
            result = self._llm.complete_chat(
                messages,
                trace_id=trace_id,
                request_id=f"{request_id}-tool-appr",
                model_override=model,
                max_tokens=192,
            )
        except DeepSeekChatCompletionError:
            return self._fault_decision("classifier_transport_error")
        raw = (result.text or "").strip()
        try:
            start = raw.index("{")
            end = raw.rindex("}") + 1
            parsed = json.loads(raw[start:end])
        except (ValueError, json.JSONDecodeError):
            return self._fault_decision("classifier_invalid_response")
        if not isinstance(parsed, dict):
            return self._fault_decision("classifier_invalid_response")
        verdict = str(parsed.get("verdict") or "").strip().lower()
        reason_code = str(parsed.get("reason_code") or "unspecified").strip()
        tags_raw = parsed.get("risk_tags")
        tags: Tuple[str, ...] = ()
        if isinstance(tags_raw, list):
            tags = tuple(str(x) for x in tags_raw if str(x).strip())
        if verdict not in {"allow", "deny", "pending_human"}:
            return self._fault_decision("classifier_invalid_response")
        return ToolApprovalDecision(
            verdict=verdict,
            reason_code=reason_code or "classifier",
            classifier_stage="stage_a",
            risk_tags=tags,
        )
