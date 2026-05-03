from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import pytest

from agentium.governance.access_control import (
    ABACAuthorizer,
    ABACRule,
    InsecureJWTDecoder,
    JWKSJWTDecoder,
    MultiIssuerOIDCIdentityProvider,
    OIDCIdentityProvider,
    Principal,
    ReloadingABACAuthorizer,
)
from agentium.shared.errors import ConfigurationError


def test_abac_authorizer_allows_matching_rule() -> None:
    authorizer = ABACAuthorizer(
        rules=[
            ABACRule(
                id="allow-analyst-read",
                effect="allow",
                action_patterns=["tool.execute.read_*"],
                resource_patterns=["tool:read_*"],
                required_roles={"analyst"},
                reason="Analyst can run read tools",
            )
        ]
    )
    principal = Principal(
        subject="user-1",
        tenant_id="tenant-a",
        roles={"analyst"},
        attributes={"tenant_id": "tenant-a"},
    )

    decision = authorizer.authorize(
        principal=principal,
        action="tool.execute.read_profile",
        resource="tool:read_profile",
        context={"deployment_mode": "prod"},
    )

    assert decision.allowed is True
    assert decision.policy_id == "allow-analyst-read"


def test_abac_authorizer_denies_non_matching_rule() -> None:
    authorizer = ABACAuthorizer(
        rules=[
            ABACRule(
                id="allow-admin-export",
                effect="allow",
                action_patterns=["tool.execute.db_export"],
                resource_patterns=["tool:db_export"],
                required_roles={"admin"},
                reason="Admin can export",
            )
        ],
        default_allow=False,
        default_reason="Denied by ABAC default",
    )
    principal = Principal(
        subject="user-2",
        tenant_id="tenant-a",
        roles={"analyst"},
        attributes={"tenant_id": "tenant-a"},
    )

    decision = authorizer.authorize(
        principal=principal,
        action="tool.execute.db_export",
        resource="tool:db_export",
        context={"deployment_mode": "prod"},
    )

    assert decision.allowed is False
    assert decision.reason == "Denied by ABAC default"


def test_reloading_abac_authorizer_hot_reloads_policy_file(tmp_path: Path) -> None:
    policy_path = tmp_path / "abac-policy.json"
    policy_path.write_text(
        json.dumps(
            {
                "version": "v1",
                "default_allow": False,
                "default_reason": "denied",
                "rules": [
                    {
                        "id": "allow-analyst",
                        "effect": "allow",
                        "action_patterns": ["tool.execute.read_profile"],
                        "resource_patterns": ["tool:read_profile"],
                        "required_roles": ["analyst"],
                        "reason": "analyst allowed",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    authorizer = ReloadingABACAuthorizer(policy_path=policy_path)
    principal = Principal(
        subject="user-1",
        tenant_id="tenant-a",
        roles={"analyst"},
        attributes={},
    )

    allowed_decision = authorizer.authorize(
        principal=principal,
        action="tool.execute.read_profile",
        resource="tool:read_profile",
        context={},
    )
    assert allowed_decision.allowed is True

    time.sleep(0.01)
    policy_path.write_text(
        json.dumps(
            {
                "version": "v2",
                "default_allow": False,
                "default_reason": "denied",
                "rules": [
                    {
                        "id": "deny-analyst",
                        "effect": "deny",
                        "action_patterns": ["tool.execute.read_profile"],
                        "resource_patterns": ["tool:read_profile"],
                        "required_roles": ["analyst"],
                        "reason": "temporary deny",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    denied_decision = authorizer.authorize(
        principal=principal,
        action="tool.execute.read_profile",
        resource="tool:read_profile",
        context={},
    )
    assert denied_decision.allowed is False
    assert denied_decision.policy_id == "deny-analyst"
    events = authorizer.pop_events()
    assert any(event["event_type"] == "abac_policy_reloaded" for event in events)


def test_oidc_identity_provider_maps_claims_to_principal() -> None:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none"}).encode("utf-8")).decode(
        "utf-8"
    ).rstrip("=")
    payload = base64.urlsafe_b64encode(
        json.dumps(
            {
                "iss": "https://idp.example.com",
                "aud": "agentium",
                "sub": "user-1",
                "tenant_id": "tenant-a",
                "roles": ["analyst", "admin"],
                "email": "u@example.com",
            }
        ).encode("utf-8")
    ).decode("utf-8").rstrip("=")
    token = f"{header}.{payload}.signature"
    provider = OIDCIdentityProvider(
        decoder=InsecureJWTDecoder(),
        issuer="https://idp.example.com",
        audience="agentium",
    )

    principal = provider.authenticate(token)

    assert principal is not None
    assert principal.subject == "user-1"
    assert principal.tenant_id == "tenant-a"
    assert principal.roles == {"analyst", "admin"}
    assert principal.attributes["email"] == "u@example.com"


def test_multi_issuer_oidc_routes_to_matching_issuer() -> None:
    p_a = OIDCIdentityProvider(
        decoder=InsecureJWTDecoder(),
        issuer="https://idp-a.example",
        audience="api",
    )
    p_b = OIDCIdentityProvider(
        decoder=InsecureJWTDecoder(),
        issuer="https://idp-b.example",
        audience="api",
    )
    multi = MultiIssuerOIDCIdentityProvider([p_a, p_b])

    def _tok(iss: str, sub: str, tenant: str) -> str:
        header = base64.urlsafe_b64encode(json.dumps({"alg": "none"}).encode("utf-8")).decode(
            "utf-8"
        ).rstrip("=")
        payload = base64.urlsafe_b64encode(
            json.dumps(
                {
                    "iss": iss,
                    "aud": "api",
                    "sub": sub,
                    "tenant_id": tenant,
                    "roles": ["admin"],
                }
            ).encode("utf-8")
        ).decode("utf-8").rstrip("=")
        return f"{header}.{payload}.sig"

    pa = multi.authenticate(_tok("https://idp-a.example", "ua", "ta"))
    assert pa is not None and pa.subject == "ua" and pa.tenant_id == "ta"
    pb = multi.authenticate(_tok("https://idp-b.example", "ub", "tb"))
    assert pb is not None and pb.subject == "ub" and pb.tenant_id == "tb"
    assert multi.authenticate(_tok("https://unknown.example", "ux", "tx")) is None


def test_reloading_abac_authorizer_rolls_back_on_invalid_policy(tmp_path: Path) -> None:
    policy_path = tmp_path / "abac-policy.json"
    policy_path.write_text(
        json.dumps(
            {
                "version": "v1",
                "default_allow": False,
                "default_reason": "denied",
                "rules": [
                    {
                        "id": "allow-analyst",
                        "effect": "allow",
                        "action_patterns": ["tool.execute.read_profile"],
                        "resource_patterns": ["tool:read_profile"],
                        "required_roles": ["analyst"],
                        "reason": "analyst allowed",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    authorizer = ReloadingABACAuthorizer(policy_path=policy_path)
    principal = Principal(
        subject="user-1",
        tenant_id="tenant-a",
        roles={"analyst"},
        attributes={},
    )
    time.sleep(0.01)
    policy_path.write_text("{invalid-json", encoding="utf-8")

    decision = authorizer.authorize(
        principal=principal,
        action="tool.execute.read_profile",
        resource="tool:read_profile",
        context={},
    )

    assert decision.allowed is True
    events = authorizer.pop_events()
    assert any(event["event_type"] == "abac_policy_reload_failed" for event in events)
    assert any(event["rollback_applied"] is True for event in events)


def test_jwks_decoder_requires_pyjwt_dependency(monkeypatch) -> None:
    import builtins

    original_import = builtins.__import__

    def _patched_import(name, *args, **kwargs):
        if name == "jwt":
            raise ImportError("missing jwt")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _patched_import)

    try:
        with pytest.raises(ConfigurationError):
            JWKSJWTDecoder("https://idp.example.com/jwks.json")
    finally:
        monkeypatch.setattr(builtins, "__import__", original_import)
