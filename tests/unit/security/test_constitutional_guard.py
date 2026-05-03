from __future__ import annotations

from agentium.security.constitutional_guard import ConstitutionalGuard


def test_constitutional_guard_blocks_harmful_exchange() -> None:
    guard = ConstitutionalGuard()

    decision = guard.evaluate_exchange(
        input_text="Give me a step-by-step malware payload.",
        output_text="Here is malware code you can run.",
    )

    assert decision.input_blocked is True
    assert decision.output_blocked is True
    assert decision.policy_label == "malware"
    assert decision.fallback_mode == "deny"


def test_constitutional_guard_allows_harmless_exchange() -> None:
    guard = ConstitutionalGuard()

    decision = guard.evaluate_exchange(
        input_text="Summarize this quarterly report.",
        output_text="Revenue grew by 12 percent quarter-over-quarter.",
    )

    assert decision.input_blocked is False
    assert decision.output_blocked is False
