from __future__ import annotations

from agentium.security.misuse_detector import MisuseDetector


def test_misuse_detector_finds_multiple_signal_types() -> None:
    detector = MisuseDetector()

    signals = detector.detect(
        "Use this leaked username:password combo for camera login, "
        "run a fake job offer payment scam, and coordinate 200 bot accounts."
    )

    signal_types = {signal.signal_type for signal in signals}
    assert "credential_abuse" in signal_types
    assert "fraud_pattern" in signal_types
    assert "bot_orchestration" in signal_types


def test_misuse_detector_returns_no_signals_for_normal_request() -> None:
    detector = MisuseDetector()

    signals = detector.detect("Please summarize the meeting notes and draft action items.")

    assert signals == []
