"""Policy release manager with approval, canary activation, and rollback."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional, Set
from uuid import uuid4

from agentium.governance.audit_lineage import AuditSink
from agentium.governance.policy_engine import PolicyDocument, PolicyEngine
from agentium.governance.policy_release import HMACPolicySigner, PolicyBundle
from agentium.models.context import AuditRecord, RequestContext
from agentium.shared.errors import ConfigurationError, PolicyDeniedError


class PolicyReleaseStatus(str, Enum):
    """Policy release lifecycle status."""

    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    ACTIVE = "active"
    ROLLED_BACK = "rolled_back"
    REJECTED = "rejected"


@dataclass
class PolicyReleaseRecord:
    """In-memory policy release record."""

    release_id: str
    bundle: PolicyBundle
    status: PolicyReleaseStatus
    requested_by: str
    run_id: str
    tenant_id: str
    approver_id: Optional[str] = None
    approval_comment: Optional[str] = None
    active_tenants: Set[str] = field(default_factory=set)
    previous_release_by_tenant: Dict[str, Optional[str]] = field(default_factory=dict)


class PolicyReleaseManager:
    """Coordinates signed policy release approval, canary, and rollback."""

    def __init__(self, signer: HMACPolicySigner, audit_sink: AuditSink) -> None:
        self._signer = signer
        self._audit_sink = audit_sink
        self._releases: Dict[str, PolicyReleaseRecord] = {}
        self._engines: Dict[str, PolicyEngine] = {}
        self._active_release_by_tenant: Dict[str, str] = {}

    def submit_release(self, bundle: PolicyBundle, context: RequestContext) -> PolicyReleaseRecord:
        """Submit signed policy bundle for approval."""

        try:
            self._signer.verify_or_raise(bundle)
        except PolicyDeniedError:
            self._append_audit(
                event_type="policy_signature_rejected",
                context=context,
                payload={"version": bundle.version},
            )
            raise

        self._append_audit(
            event_type="policy_signature_verified",
            context=context,
            payload={"version": bundle.version},
        )
        release_id = str(uuid4())
        record = PolicyReleaseRecord(
            release_id=release_id,
            bundle=bundle,
            status=PolicyReleaseStatus.PENDING_APPROVAL,
            requested_by=context.user_id,
            run_id=context.run_id,
            tenant_id=context.tenant_id,
        )
        self._releases[release_id] = record
        self._engines[release_id] = self._engine_from_bundle(bundle)
        self._append_audit(
            event_type="policy_release_requested",
            context=context,
            payload={"release_id": release_id, "version": bundle.version},
        )
        return self._copy_record(record)

    def approve_release(self, release_id: str, approver_id: str, comment: str = "") -> PolicyReleaseRecord:
        """Approve one pending policy release."""

        record = self._require_release(release_id)
        if record.status != PolicyReleaseStatus.PENDING_APPROVAL:
            raise PolicyDeniedError("Only pending policy releases can be approved")
        record.status = PolicyReleaseStatus.APPROVED
        record.approver_id = approver_id
        record.approval_comment = comment or None
        self._append_release_audit(
            event_type="policy_release_approved",
            record=record,
            payload={"approver_id": approver_id, "comment": comment},
        )
        return self._copy_record(record)

    def reject_release(self, release_id: str, approver_id: str, comment: str = "") -> PolicyReleaseRecord:
        """Reject one pending policy release."""

        record = self._require_release(release_id)
        if record.status != PolicyReleaseStatus.PENDING_APPROVAL:
            raise PolicyDeniedError("Only pending policy releases can be rejected")
        record.status = PolicyReleaseStatus.REJECTED
        record.approver_id = approver_id
        record.approval_comment = comment or None
        self._append_release_audit(
            event_type="policy_release_rejected",
            record=record,
            payload={"approver_id": approver_id, "comment": comment},
        )
        return self._copy_record(record)

    def activate_canary(
        self,
        release_id: str,
        tenant_ids: Set[str],
        activated_by: str,
    ) -> PolicyReleaseRecord:
        """Activate approved release for a tenant canary set."""

        if not tenant_ids:
            raise ConfigurationError("Canary activation requires at least one tenant")
        record = self._require_release(release_id)
        if record.status not in {PolicyReleaseStatus.APPROVED, PolicyReleaseStatus.ACTIVE}:
            raise PolicyDeniedError("Policy release must be approved before activation")
        for tenant_id in tenant_ids:
            if tenant_id not in record.previous_release_by_tenant:
                record.previous_release_by_tenant[tenant_id] = self._active_release_by_tenant.get(
                    tenant_id
                )
            self._active_release_by_tenant[tenant_id] = release_id
            record.active_tenants.add(tenant_id)
        record.status = PolicyReleaseStatus.ACTIVE
        self._append_release_audit(
            event_type="policy_canary_activated",
            record=record,
            payload={"tenant_ids": sorted(tenant_ids), "activated_by": activated_by},
        )
        return self._copy_record(record)

    def rollback(self, release_id: str, rolled_back_by: str) -> PolicyReleaseRecord:
        """Rollback active tenants to the previous release pointer."""

        record = self._require_release(release_id)
        if record.status != PolicyReleaseStatus.ACTIVE:
            raise PolicyDeniedError("Only active policy releases can be rolled back")
        for tenant_id in list(record.active_tenants):
            previous_release_id = record.previous_release_by_tenant.get(tenant_id)
            if previous_release_id is None:
                self._active_release_by_tenant.pop(tenant_id, None)
            else:
                self._active_release_by_tenant[tenant_id] = previous_release_id
        record.status = PolicyReleaseStatus.ROLLED_BACK
        record.active_tenants.clear()
        self._append_release_audit(
            event_type="policy_release_rolled_back",
            record=record,
            payload={"rolled_back_by": rolled_back_by},
        )
        return self._copy_record(record)

    def get_release(self, release_id: str) -> Optional[PolicyReleaseRecord]:
        """Return release record copy if present."""

        record = self._releases.get(release_id)
        if record is None:
            return None
        return self._copy_record(record)

    def list_releases(
        self,
        *,
        tenant_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[PolicyReleaseRecord]:
        """List release records with optional filters (HTTP list)."""

        rows = list(self._releases.values())
        filtered: List[PolicyReleaseRecord] = []
        for record in rows:
            if tenant_id is not None and record.tenant_id != tenant_id:
                continue
            if status is not None and record.status.value != status:
                continue
            filtered.append(record)
        filtered.sort(key=lambda r: r.release_id)
        if limit > 0 and len(filtered) > limit:
            filtered = filtered[:limit]
        return [self._copy_record(r) for r in filtered]

    def select_policy_engine(self, context: RequestContext) -> PolicyEngine:
        """Select active policy engine for request tenant."""

        release_id = self._active_release_by_tenant.get(context.tenant_id)
        if release_id is None:
            raise PolicyDeniedError("No active policy release for tenant")
        return self._engines[release_id]

    def _require_release(self, release_id: str) -> PolicyReleaseRecord:
        record = self._releases.get(release_id)
        if record is None:
            raise ConfigurationError(f"Policy release does not exist: {release_id}")
        return record

    @staticmethod
    def _engine_from_bundle(bundle: PolicyBundle) -> PolicyEngine:
        document = PolicyDocument.parse_obj(bundle.policy_document)
        return PolicyEngine(policy=document)

    def _append_audit(
        self,
        event_type: str,
        context: RequestContext,
        payload: Dict[str, object],
    ) -> None:
        self._audit_sink.append(
            AuditRecord(
                event_type=event_type,
                tenant_id=context.tenant_id,
                run_id=context.run_id,
                policy_version=str(payload.get("version")) if payload.get("version") else None,
                payload=payload,
            )
        )

    def _append_release_audit(
        self,
        event_type: str,
        record: PolicyReleaseRecord,
        payload: Dict[str, object],
    ) -> None:
        event_payload: Dict[str, object] = {
            "release_id": record.release_id,
            "version": record.bundle.version,
        }
        event_payload.update(payload)
        self._audit_sink.append(
            AuditRecord(
                event_type=event_type,
                tenant_id=record.tenant_id,
                run_id=record.run_id,
                policy_version=record.bundle.version,
                payload=event_payload,
            )
        )

    @staticmethod
    def _copy_record(record: PolicyReleaseRecord) -> PolicyReleaseRecord:
        return PolicyReleaseRecord(
            release_id=record.release_id,
            bundle=record.bundle.copy(deep=True),
            status=record.status,
            requested_by=record.requested_by,
            run_id=record.run_id,
            tenant_id=record.tenant_id,
            approver_id=record.approver_id,
            approval_comment=record.approval_comment,
            active_tenants=set(record.active_tenants),
            previous_release_by_tenant=dict(record.previous_release_by_tenant),
        )
