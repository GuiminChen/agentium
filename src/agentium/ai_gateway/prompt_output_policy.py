"""Prompt construction and output normalisation policy.

PRD §3.16 explicitly requires the AI gateway to enforce policy on the
*shape* of prompts and outputs (max length, banned headers, structural
schema).  This module is the deterministic enforcement layer applied
*around* the model provider.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, List, Mapping, Optional, Sequence


@dataclass(frozen=True)
class PromptOutputPolicy:
    """Declarative limits on prompt and response shape.

    Attributes:
        max_prompt_chars: hard upper bound; longer prompts are rejected.
        max_response_chars: hard upper bound on raw model output length.
        banned_prompt_substrings: regex strings that, if present in the
            prompt, immediately reject the call with a structured reason.
        required_response_prefixes: when set, the response must start with
            one of these prefixes (e.g. ``"{"`` for forced JSON).
        ban_personally_identifiable: when ``True``, the policy rejects
            outputs that obviously echo emails or 9+ digit numbers (a tiny
            heuristic backstop in addition to DLP).
    """

    max_prompt_chars: int = 16_000
    max_response_chars: int = 12_000
    banned_prompt_substrings: Sequence[str] = field(default_factory=tuple)
    required_response_prefixes: Sequence[str] = field(default_factory=tuple)
    ban_personally_identifiable: bool = False


class PromptOutputPolicyError(Exception):
    """Raised when prompt or response violates the active policy."""

    def __init__(self, reason: str, *, location: str) -> None:
        super().__init__(f"{location}:{reason}")
        self.reason = reason
        self.location = location


_PII_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PII_LONG_DIGITS_RE = re.compile(r"\b\d{9,}\b")


def _violations_in_prompt(prompt: str, policy: PromptOutputPolicy) -> List[str]:
    findings: List[str] = []
    if len(prompt) > policy.max_prompt_chars:
        findings.append("prompt_too_long")
    for pattern in policy.banned_prompt_substrings:
        if re.search(pattern, prompt):
            findings.append(f"banned_pattern:{pattern}")
    return findings


def _violations_in_response(response: str, policy: PromptOutputPolicy) -> List[str]:
    findings: List[str] = []
    if len(response) > policy.max_response_chars:
        findings.append("response_too_long")
    if policy.required_response_prefixes:
        if not any(
            response.startswith(prefix) for prefix in policy.required_response_prefixes
        ):
            findings.append("missing_required_prefix")
    if policy.ban_personally_identifiable:
        if _PII_EMAIL_RE.search(response) or _PII_LONG_DIGITS_RE.search(response):
            findings.append("pii_present")
    return findings


def assert_prompt_complies(prompt: str, policy: PromptOutputPolicy) -> None:
    """Raise :class:`PromptOutputPolicyError` if ``prompt`` violates ``policy``."""

    findings = _violations_in_prompt(prompt, policy)
    if findings:
        raise PromptOutputPolicyError(",".join(findings), location="prompt")


def assert_response_complies(response: str, policy: PromptOutputPolicy) -> None:
    """Raise :class:`PromptOutputPolicyError` if ``response`` violates ``policy``."""

    findings = _violations_in_response(response, policy)
    if findings:
        raise PromptOutputPolicyError(",".join(findings), location="response")


def explain(prompt: str, response: str, policy: PromptOutputPolicy) -> Mapping[str, Iterable[str]]:
    """Return a non-raising audit-friendly summary of policy findings."""

    return {
        "prompt": tuple(_violations_in_prompt(prompt, policy)),
        "response": tuple(_violations_in_response(response, policy)),
    }


__all__ = [
    "PromptOutputPolicy",
    "PromptOutputPolicyError",
    "assert_prompt_complies",
    "assert_response_complies",
    "explain",
]
