"""Unit tests for the SecretLeakGuard high-entropy detector."""

from __future__ import annotations

from agentium.security.secret_leak_guard import SecretLeakGuard


def test_secret_leak_guard_flags_high_entropy_token():
    guard = SecretLeakGuard()
    decision = guard.scan_text(
        "leaking key=AbCdEfGhIjKlMnOpQrStUvWxYz0123456789+ZxCv"
    )
    assert decision.blocked is True
    assert decision.hits and "[SECRET_REDACTED]" in decision.redacted_preview


def test_secret_leak_guard_ignores_known_fixtures():
    guard = SecretLeakGuard()
    text = "checksum sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    decision = guard.scan_text(text)
    assert decision.blocked is False
    assert decision.hits == []


def test_secret_leak_guard_walks_payload_paths():
    guard = SecretLeakGuard(block_threshold=2)
    payload = {
        "credentials": {
            "primary": "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789+ZxCv",
            "secondary": "QwErTyUiOpAsDfGhJkLzXcVbNm1234567890+QqWw",
        },
        "comment": "this should not match",
    }
    decision = guard.scan_payload(payload)
    assert decision.blocked is True
    locations = sorted(h.location for h in decision.hits)
    assert "credentials.primary" in locations
    assert "credentials.secondary" in locations
