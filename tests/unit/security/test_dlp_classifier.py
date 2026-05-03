"""Unit tests for DLPClassifier."""

from __future__ import annotations

from agentium.security.dlp_classifier import DLPClassifier


def test_dlp_blocks_private_key() -> None:
    classifier = DLPClassifier()
    decision = classifier.classify_text(
        "-----BEGIN OPENSSH PRIVATE KEY-----\nABC\n-----END OPENSSH PRIVATE KEY-----"
    )
    assert decision.blocked
    assert any(hit.label == "ssh_private_key" for hit in decision.hits)


def test_dlp_masks_email() -> None:
    classifier = DLPClassifier()
    decision = classifier.classify_text("contact me at user@example.com")
    assert not decision.blocked
    assert "[REDACTED]" in decision.masked_text
    assert any(hit.label == "email" for hit in decision.hits)


def test_dlp_payload_walks_strings() -> None:
    classifier = DLPClassifier()
    decision = classifier.classify_payload(
        {"nested": {"value": "AKIA1234567890ABCDEF"}}
    )
    assert decision.blocked
    assert any(hit.label == "aws_access_key_id" for hit in decision.hits)
