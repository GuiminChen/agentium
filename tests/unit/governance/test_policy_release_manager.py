from __future__ import annotations

import pytest

from agentium.governance.audit_lineage import InMemoryAuditSink
from agentium.governance.policy_release import HMACPolicySigner, PolicyBundle
from agentium.governance.policy_release_manager import (
    PolicyReleaseManager,
    PolicyReleaseStatus,
)
from agentium.models.context import DecisionType, RequestContext
from agentium.shared.errors import PolicyDeniedError


def _context(tenant_id: str = "tenant-a", role: str = "admin") -> RequestContext:
    return RequestContext(
        request_id="req-1",
        run_id="run-1",
        tenant_id=tenant_id,
        user_id="release-admin",
        trace_id="trace-1",
        role=role,
        deployment_mode="prod",
    )


def _policy_document(version: str, tool_name: str) -> dict:
    return {
        "version": version,
        "default_decision": "deny",
        "default_reason": "denied by default",
        "rules": [
            {
                "id": "allow-" + tool_name,
                "decision": "allow",
                "reason": "allowed",
                "tools": [tool_name],
                "roles": ["admin"],
            }
        ],
    }


def _bundle(version: str, tool_name: str, signer: HMACPolicySigner) -> PolicyBundle:
    document = _policy_document(version=version, tool_name=tool_name)
    return PolicyBundle(
        version=version,
        policy_document=document,
        signature=signer.sign(version, document),
        metadata={"submitted_by": "release-admin"},
    )


def test_policy_release_manager_blocks_invalid_signature() -> None:
    signer = HMACPolicySigner(secret="dev-secret")
    audit_sink = InMemoryAuditSink()
    manager = PolicyReleaseManager(signer=signer, audit_sink=audit_sink)
    bad_bundle = _bundle("candidate-v1", "read_profile", signer).copy(
        update={"signature": "bad-signature"}
    )

    with pytest.raises(PolicyDeniedError):
        manager.submit_release(bundle=bad_bundle, context=_context())

    records = audit_sink.query(run_id="run-1", tenant_id="tenant-a")
    assert any(record.event_type == "policy_signature_rejected" for record in records)


def test_policy_release_manager_requires_approval_before_activation() -> None:
    signer = HMACPolicySigner(secret="dev-secret")
    manager = PolicyReleaseManager(signer=signer, audit_sink=InMemoryAuditSink())
    release = manager.submit_release(
        bundle=_bundle("candidate-v1", "read_profile", signer),
        context=_context(),
    )

    with pytest.raises(PolicyDeniedError):
        manager.activate_canary(
            release_id=release.release_id,
            tenant_ids={"tenant-a"},
            activated_by="release-admin",
        )


def test_policy_release_manager_canary_and_rollback() -> None:
    signer = HMACPolicySigner(secret="dev-secret")
    audit_sink = InMemoryAuditSink()
    manager = PolicyReleaseManager(signer=signer, audit_sink=audit_sink)
    base_release = manager.submit_release(
        bundle=_bundle("stable-v1", "read_profile", signer),
        context=_context(),
    )
    manager.approve_release(
        release_id=base_release.release_id,
        approver_id="security-1",
        comment="baseline",
    )
    manager.activate_canary(
        release_id=base_release.release_id,
        tenant_ids={"tenant-a", "tenant-b"},
        activated_by="release-admin",
    )

    candidate_release = manager.submit_release(
        bundle=_bundle("candidate-v2", "db_export", signer),
        context=_context(),
    )
    manager.approve_release(
        release_id=candidate_release.release_id,
        approver_id="security-1",
        comment="canary",
    )
    manager.activate_canary(
        release_id=candidate_release.release_id,
        tenant_ids={"tenant-a"},
        activated_by="release-admin",
    )

    tenant_a_engine = manager.select_policy_engine(_context(tenant_id="tenant-a"))
    tenant_b_engine = manager.select_policy_engine(_context(tenant_id="tenant-b"))
    assert tenant_a_engine.decide_tool_call(_context(), "db_export", {}).decision == DecisionType.ALLOW
    assert tenant_b_engine.decide_tool_call(
        _context(tenant_id="tenant-b"), "read_profile", {}
    ).decision == DecisionType.ALLOW

    manager.rollback(release_id=candidate_release.release_id, rolled_back_by="ops-1")

    tenant_a_after = manager.select_policy_engine(_context(tenant_id="tenant-a"))
    assert tenant_a_after.decide_tool_call(_context(), "read_profile", {}).decision == DecisionType.ALLOW
    assert any(
        record.event_type == "policy_release_rolled_back"
        for record in audit_sink.query(run_id="run-1")
    )
