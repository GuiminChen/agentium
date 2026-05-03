"""Policy release domain models and HMAC signing utilities."""

from __future__ import annotations

import hmac
import json
from hashlib import sha256
from typing import Any, Dict

from pydantic import BaseModel, Field

from agentium.shared.errors import PolicyDeniedError


class PolicySignatureError(PolicyDeniedError):
    """Raised when a policy bundle signature is missing or invalid."""


class PolicyBundle(BaseModel):
    """Signed policy bundle submitted for release governance.

    Attributes:
        version: Candidate policy version.
        policy_document: Raw policy document payload.
        signature: HMAC-SHA256 signature over version and document.
        metadata: Operator supplied release metadata.
    """

    version: str = Field(min_length=1)
    policy_document: Dict[str, Any]
    signature: str = Field(min_length=1)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        """Pydantic model configuration."""

        extra = "forbid"


class HMACPolicySigner:
    """HMAC-SHA256 signer and verifier for policy bundles."""

    def __init__(self, secret: str) -> None:
        if not secret:
            raise ValueError("HMAC policy signing secret must not be empty")
        self._secret = secret.encode("utf-8")

    def sign(self, version: str, policy_document: Dict[str, Any]) -> str:
        """Return deterministic HMAC signature for policy payload."""

        payload = self._canonical_payload(version=version, policy_document=policy_document)
        return hmac.new(self._secret, payload, sha256).hexdigest()

    def verify(self, bundle: PolicyBundle) -> bool:
        """Return True when bundle signature matches payload."""

        expected = self.sign(version=bundle.version, policy_document=bundle.policy_document)
        return hmac.compare_digest(expected, bundle.signature)

    def verify_or_raise(self, bundle: PolicyBundle) -> None:
        """Verify bundle or raise a fail-closed policy error."""

        if not self.verify(bundle):
            raise PolicySignatureError("Policy bundle signature is invalid")

    @staticmethod
    def _canonical_payload(version: str, policy_document: Dict[str, Any]) -> bytes:
        payload = {"version": version, "policy_document": policy_document}
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
