from __future__ import annotations

import pytest

from agentium.governance.policy_release import (
    HMACPolicySigner,
    PolicyBundle,
    PolicySignatureError,
)


def _policy_document() -> dict:
    return {
        "version": "candidate-v1",
        "default_decision": "deny",
        "default_reason": "denied by default",
        "rules": [
            {
                "id": "allow-read",
                "decision": "allow",
                "reason": "read allowed",
                "tools": ["read_profile"],
                "roles": ["analyst"],
            }
        ],
    }


def test_hmac_policy_signer_verifies_signed_bundle() -> None:
    signer = HMACPolicySigner(secret="dev-secret")
    bundle = PolicyBundle(
        version="candidate-v1",
        policy_document=_policy_document(),
        signature=signer.sign("candidate-v1", _policy_document()),
        metadata={"submitted_by": "user-1"},
    )

    verified = signer.verify(bundle)

    assert verified is True


def test_hmac_policy_signer_rejects_tampered_bundle() -> None:
    signer = HMACPolicySigner(secret="dev-secret")
    bundle = PolicyBundle(
        version="candidate-v1",
        policy_document=_policy_document(),
        signature=signer.sign("candidate-v1", _policy_document()),
        metadata={},
    )
    tampered_bundle = bundle.copy(
        update={
            "policy_document": {
                **bundle.policy_document,
                "default_decision": "allow",
            }
        }
    )

    with pytest.raises(PolicySignatureError):
        signer.verify_or_raise(tampered_bundle)


def test_policy_bundle_requires_signature() -> None:
    with pytest.raises(ValueError):
        PolicyBundle(version="candidate-v1", policy_document=_policy_document(), signature="")
