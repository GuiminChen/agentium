"""Social engineering / phishing guard for inbound prompts and outbound replies.

Two threat classes are addressed:

1. **Inbound credential phishing** (PRD §3.16) – the agent must refuse to help
   draft messages that ask end users to hand over secrets, MFA codes, etc.
2. **Outbound urgency manipulation** – the agent should not generate replies
   that pressure users into bypassing controls.

The detector is rule-based on purpose: deterministic, auditable and unit
testable.  Higher-fidelity model-based classifiers can be plugged in later by
re-implementing :meth:`SocialEngineeringGuard.classify`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Sequence, Tuple


PatternRule = Tuple[str, "re.Pattern", int]


@dataclass(frozen=True)
class SocialEngineeringHit:
    """One detected risk signal."""

    label: str
    snippet: str
    severity: int


@dataclass
class SocialEngineeringDecision:
    """Aggregate verdict from :class:`SocialEngineeringGuard`."""

    blocked: bool
    severity: int = 0
    hits: List[SocialEngineeringHit] = field(default_factory=list)
    reason: str = ""


_DEFAULT_PATTERNS: Sequence[Tuple[str, str, int]] = (
    ("credential_phishing", r"(?:please|kindly)\s+(?:send|share|provide)\s+(?:me\s+)?(?:your\s+)?(?:password|otp|mfa|2fa|verification\s+code|api\s+key|access\s+token|secret)", 3),
    ("password_request", r"(?:what(?:'s| is)\s+(?:the|your))\s+(?:password|api\s+key|secret|token)", 3),
    ("urgency_pressure", r"(?:within\s+\d+\s*(?:minutes|seconds|hrs|hours)|right\s+now|immediately|asap|do\s+not\s+tell)", 1),
    ("authority_impersonation", r"(?:this\s+is\s+(?:the\s+)?(?:ceo|cfo|cio|cto|hr|legal|it\s+department))", 2),
    ("policy_bypass", r"(?:bypass|disable|skip)\s+(?:the\s+)?(?:approval|policy|review|guard|guardrail|2fa|mfa)", 3),
    ("transfer_funds", r"(?:wire|transfer|send)\s+\$?\d", 2),
)


class SocialEngineeringGuard:
    """Pattern-based phishing/coercion detector.

    Args:
        patterns: optional override for ``(label, regex, severity)`` rules.
        block_threshold: cumulative severity at which the decision is
            ``blocked``.  Default ``3`` lines up with one severity-3 hit.
    """

    def __init__(
        self,
        patterns: Sequence[Tuple[str, str, int]] = _DEFAULT_PATTERNS,
        *,
        block_threshold: int = 3,
    ) -> None:
        if block_threshold <= 0:
            raise ValueError("block_threshold must be positive")
        self._patterns: List[PatternRule] = [
            (label, re.compile(pattern, re.IGNORECASE), severity)
            for label, pattern, severity in patterns
        ]
        self._threshold = block_threshold

    def classify(self, text: str) -> SocialEngineeringDecision:
        """Inspect ``text`` and return a decision."""

        if not text:
            return SocialEngineeringDecision(blocked=False)
        hits: List[SocialEngineeringHit] = []
        total = 0
        for label, pattern, severity in self._patterns:
            for match in pattern.finditer(text):
                snippet = text[max(0, match.start() - 16) : match.end() + 16]
                hits.append(
                    SocialEngineeringHit(
                        label=label, snippet=snippet, severity=severity
                    )
                )
                total += severity
        blocked = total >= self._threshold
        reason = (
            "social_engineering_detected" if blocked else ""
        )
        return SocialEngineeringDecision(
            blocked=blocked, severity=total, hits=hits, reason=reason
        )
