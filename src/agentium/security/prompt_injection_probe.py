"""Prompt injection scanning for external untrusted content."""

from __future__ import annotations

from typing import Dict, List, Tuple

from pydantic import BaseModel, Field


class PromptInjectionScanResult(BaseModel):
    """Result for one prompt injection scan pass."""

    source: str = Field(min_length=1)
    risk_level: str = Field(min_length=1)
    indicators: List[str] = Field(default_factory=list)
    blocked: bool = False


class PromptInjectionProbe:
    """Keyword-based prompt injection probe for first-line defense."""

    _HIGH_RISK_PATTERNS: Dict[str, Tuple[str, ...]] = {
        "instruction_override": ("ignore previous instructions", "system prompt"),
        "data_exfiltration": ("exfiltrate", "reveal secrets", "leak credentials"),
        "privilege_escalation": ("bypass approval", "disable policy", "admin mode"),
    }
    _MEDIUM_RISK_PATTERNS: Dict[str, Tuple[str, ...]] = {
        "social_engineering": ("urgent security fix", "trust me", "confidential override"),
        "tool_hijack": ("run shell command", "execute rm -rf", "download and run"),
    }

    def scan(self, source: str, content: str) -> PromptInjectionScanResult:
        """Scan content and return structured risk judgment.

        Args:
            source: Input source channel name.
            content: Raw untrusted content.
        """

        normalized = content.lower()
        high_indicators = self._match_indicators(normalized, self._HIGH_RISK_PATTERNS)
        if high_indicators:
            return PromptInjectionScanResult(
                source=source,
                risk_level="high",
                indicators=high_indicators,
                blocked=True,
            )

        medium_indicators = self._match_indicators(
            normalized, self._MEDIUM_RISK_PATTERNS
        )
        if medium_indicators:
            return PromptInjectionScanResult(
                source=source,
                risk_level="medium",
                indicators=medium_indicators,
                blocked=False,
            )

        return PromptInjectionScanResult(
            source=source,
            risk_level="low",
            indicators=[],
            blocked=False,
        )

    @staticmethod
    def _match_indicators(
        content: str, patterns: Dict[str, Tuple[str, ...]]
    ) -> List[str]:
        indicators: List[str] = []
        for indicator, keywords in patterns.items():
            if any(keyword in content for keyword in keywords):
                indicators.append(indicator)
        return indicators

