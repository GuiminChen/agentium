"""Content safety pipeline applied around LLM calls.

This composes existing primitives so the AI gateway has a single,
auditable check before sending a prompt and after receiving a response:

- :class:`PromptInjectionProbe` for inbound user-controlled content;
- :class:`DLPClassifier` for outbound responses (no secrets leak through
  the model surface);
- :class:`SecretLeakGuard` complements DLP with high-entropy detection;
- :class:`ConstitutionalGuard` for refusal-style misuse.

The result is deterministic, dependency-free, and exposes ``decision``
metadata so the router can log a structured reason chain.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Mapping, Optional

from agentium.security.constitutional_guard import ConstitutionalGuard
from agentium.security.dlp_classifier import DLPClassifier
from agentium.security.prompt_injection_probe import PromptInjectionProbe
from agentium.security.secret_leak_guard import SecretLeakGuard


@dataclass
class ContentSafetyDecision:
    """Aggregate verdict from :class:`ContentSafetyPipeline`."""

    allowed: bool = True
    blocked_reason: Optional[str] = None
    inbound_findings: List[str] = field(default_factory=list)
    outbound_findings: List[str] = field(default_factory=list)
    redacted_output: Optional[str] = None


class ContentSafetyPipeline:
    """Pre-LLM and post-LLM safety enforcement."""

    def __init__(
        self,
        *,
        prompt_injection: Optional[PromptInjectionProbe] = None,
        dlp: Optional[DLPClassifier] = None,
        secret_guard: Optional[SecretLeakGuard] = None,
        constitutional: Optional[ConstitutionalGuard] = None,
    ) -> None:
        self._injection = prompt_injection
        self._dlp = dlp
        self._secret = secret_guard
        self._constitutional = constitutional

    def evaluate_inbound(self, prompt: str) -> ContentSafetyDecision:
        decision = ContentSafetyDecision()
        if self._injection is not None:
            probe = self._injection.scan(source="ai_gateway", content=prompt)
            if probe.blocked:
                decision.allowed = False
                decision.blocked_reason = "prompt_injection_blocked"
                decision.inbound_findings.extend(probe.indicators)
        return decision

    def evaluate_outbound(
        self,
        response: str,
        *,
        prompt_for_constitution: Optional[str] = None,
    ) -> ContentSafetyDecision:
        decision = ContentSafetyDecision()
        if self._dlp is not None:
            dlp_decision = self._dlp.classify_payload({"response": response})
            if dlp_decision.blocked:
                decision.allowed = False
                decision.blocked_reason = "dlp_blocked"
                decision.outbound_findings.extend(
                    sorted({hit.label for hit in dlp_decision.hits})
                )
                return decision
        if self._secret is not None:
            secret_decision = self._secret.scan_text(response, location="response")
            if secret_decision.blocked:
                decision.allowed = False
                decision.blocked_reason = "secret_leak_blocked"
                decision.outbound_findings.append("high_entropy_secret")
                decision.redacted_output = secret_decision.redacted_preview
                return decision
        if self._constitutional is not None and prompt_for_constitution is not None:
            verdict = self._constitutional.evaluate_exchange(
                input_text=prompt_for_constitution,
                output_text=response,
            )
            if verdict.output_blocked or verdict.input_blocked:
                decision.allowed = False
                decision.blocked_reason = "constitutional_blocked"
                decision.outbound_findings.append(verdict.policy_label)
        return decision


__all__ = ["ContentSafetyDecision", "ContentSafetyPipeline"]
