"""Data-loss prevention classifier for outbound payloads."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Pattern, Tuple


@dataclass(frozen=True)
class DLPHit:
    """One DLP detection result."""

    label: str
    matched_text: str
    action: str  # "mask" or "block"


@dataclass(frozen=True)
class DLPDecision:
    """Aggregate DLP decision for a payload."""

    blocked: bool
    masked_text: str
    hits: List[DLPHit]


class DLPClassifier:
    """Regex/keyword based DLP for the output side of tool execution."""

    DEFAULT_RULES: Tuple[Tuple[str, str, str], ...] = (
        # label, pattern, action
        ("email", r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "mask"),
        ("phone_us", r"\+?1?[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}", "mask"),
        ("ssn", r"\b\d{3}-\d{2}-\d{4}\b", "block"),
        ("aws_access_key_id", r"AKIA[0-9A-Z]{16}", "block"),
        ("private_key_block", r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----", "block"),
        ("ssh_private_key", r"-----BEGIN OPENSSH PRIVATE KEY-----", "block"),
        ("bearer_token", r"\bBearer\s+[A-Za-z0-9._\-]{20,}\b", "block"),
        ("credit_card", r"\b(?:\d[ -]*?){13,16}\b", "mask"),
    )

    def __init__(
        self,
        rules: Tuple[Tuple[str, str, str], ...] = DEFAULT_RULES,
        mask_token: str = "[REDACTED]",
    ) -> None:
        self._compiled: List[Tuple[str, Pattern[str], str]] = [
            (label, re.compile(pattern, flags=re.IGNORECASE), action)
            for label, pattern, action in rules
        ]
        self._mask_token = mask_token

    def classify_text(self, text: str) -> DLPDecision:
        """Classify one text payload, returning hits and masked text."""

        if not text:
            return DLPDecision(blocked=False, masked_text=text, hits=[])
        hits: List[DLPHit] = []
        masked = text
        blocked = False
        for label, pattern, action in self._compiled:
            for match in pattern.finditer(text):
                hits.append(
                    DLPHit(label=label, matched_text=match.group(0), action=action)
                )
                if action == "block":
                    blocked = True
                else:
                    masked = pattern.sub(self._mask_token, masked)
        return DLPDecision(blocked=blocked, masked_text=masked, hits=hits)

    def classify_payload(self, payload: Dict[str, Any]) -> DLPDecision:
        """Classify a structured payload by inspecting all string leaves."""

        leaves = list(_walk_string_leaves(payload))
        if not leaves:
            return DLPDecision(blocked=False, masked_text="", hits=[])
        all_hits: List[DLPHit] = []
        masked_parts: List[str] = []
        blocked = False
        for value in leaves:
            decision = self.classify_text(value)
            all_hits.extend(decision.hits)
            masked_parts.append(decision.masked_text)
            if decision.blocked:
                blocked = True
        return DLPDecision(
            blocked=blocked,
            masked_text="\n".join(masked_parts),
            hits=all_hits,
        )


def _walk_string_leaves(payload: Any) -> "list[str]":
    out: List[str] = []
    if isinstance(payload, str):
        out.append(payload)
    elif isinstance(payload, dict):
        for value in payload.values():
            out.extend(_walk_string_leaves(value))
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            out.extend(_walk_string_leaves(item))
    return out
