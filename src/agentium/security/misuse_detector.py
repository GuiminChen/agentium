"""Detection of common malicious-use signal patterns."""

from __future__ import annotations

from typing import Dict, List, Tuple

from pydantic import BaseModel, Field


class MisuseSignal(BaseModel):
    """One detected misuse signal with confidence and action."""

    signal_type: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    action: str = Field(min_length=1)


class MisuseDetector:
    """Lightweight detector for high-priority abuse patterns."""

    _RULES: Dict[str, Tuple[str, ...]] = {
        "credential_abuse": (
            "username:password",
            "leaked credentials",
            "credential stuffing",
            "camera login",
        ),
        "fraud_pattern": (
            "job offer payment",
            "advance fee scam",
            "fake invoice",
            "social engineering scam",
        ),
        "bot_orchestration": (
            "bot accounts",
            "coordinate bots",
            "automated repost",
            "engagement farm",
        ),
    }

    _ACTIONS: Dict[str, str] = {
        "credential_abuse": "manual_review",
        "fraud_pattern": "account_hold",
        "bot_orchestration": "rate_limit",
    }

    def detect(self, content: str) -> List[MisuseSignal]:
        """Return detected misuse signals in one content sample."""

        normalized = content.lower()
        signals: List[MisuseSignal] = []
        for signal_type, keywords in self._RULES.items():
            hits = [keyword for keyword in keywords if keyword in normalized]
            if not hits:
                continue
            confidence = min(1.0, 0.4 + 0.2 * len(hits))
            signals.append(
                MisuseSignal(
                    signal_type=signal_type,
                    confidence=confidence,
                    action=self._ACTIONS[signal_type],
                )
            )
        return signals

