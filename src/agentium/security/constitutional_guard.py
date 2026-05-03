"""Constitutional-style input/output safeguard decisions."""

from __future__ import annotations

from typing import Dict, Optional, Tuple

from pydantic import BaseModel, Field


class ConstitutionalGuardDecision(BaseModel):
    """Decision emitted by constitutional safeguard checks."""

    input_blocked: bool = False
    output_blocked: bool = False
    policy_label: str = Field(default="none", min_length=1)
    fallback_mode: str = Field(default="safe_rewrite", min_length=1)


class ConstitutionalGuard:
    """Rule-based constitutional classifier baseline."""

    _POLICIES: Dict[str, Tuple[str, ...]] = {
        "malware": ("malware", "payload", "ransomware", "backdoor"),
        "credential_theft": ("steal password", "credential stuffing", "phishing kit"),
        "violent_harm": ("build bomb", "chemical weapon", "poison at scale"),
    }

    def evaluate_exchange(
        self, input_text: str, output_text: str
    ) -> ConstitutionalGuardDecision:
        """Evaluate both user input and model output for harmful content."""

        input_label = self._match_label(input_text)
        output_label = self._match_label(output_text)

        input_blocked = input_label is not None
        output_blocked = output_label is not None
        policy_label = output_label or input_label or "none"

        if output_blocked:
            fallback_mode = "deny"
        elif input_blocked:
            fallback_mode = "hitl_required"
        else:
            fallback_mode = "safe_rewrite"

        return ConstitutionalGuardDecision(
            input_blocked=input_blocked,
            output_blocked=output_blocked,
            policy_label=policy_label,
            fallback_mode=fallback_mode,
        )

    def _match_label(self, text: str) -> Optional[str]:
        normalized = text.lower()
        for label, keywords in self._POLICIES.items():
            if any(keyword in normalized for keyword in keywords):
                return label
        return None

