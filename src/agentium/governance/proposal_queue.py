"""Asynchronous proposal queue for governed state and knowledge changes.

The queue complements :class:`ApprovalGate` (which gates synchronous tool calls)
by carrying *asynchronous* change proposals: long-term memory promotions,
candidate policy deltas, prompt-template updates, and preference sketches.
Such changes originate from learning loops, outer-loop review workflows, or
manual operator requests, and must not mutate production parameters without an
explicit human decision. Every state transition emits an audit record so the
lineage is reviewable alongside synchronous policy/approval events.

Contract highlights aligned with the paper:

* Proposals are scoped to a tenant and a ``run_id``; cross-tenant reads return
  empty slices rather than data from other tenants.
* State transitions are single-writer through an internal lock; only pending
  proposals may transition to approved/rejected/withdrawn.
* The queue uses an in-memory store by default; production deployments may
  substitute a persistent store by implementing the same public methods.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from threading import Lock
from typing import Any, Dict, List, Optional
from uuid import uuid4

from agentium.governance.audit_lineage import AuditSink
from agentium.models.context import AuditRecord, RequestContext


class ProposalKind(str, Enum):
    """Supported proposal categories."""

    MEMORY_PROMOTION = "memory_promotion"
    POLICY_DELTA = "policy_delta"
    PROMPT_TEMPLATE = "prompt_template"
    PREFERENCE_UPDATE = "preference_update"


class ProposalStatus(str, Enum):
    """Lifecycle states for one proposal."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


@dataclass
class Proposal:
    """One asynchronous change proposal tracked by :class:`ProposalQueue`."""

    proposal_id: str
    kind: ProposalKind
    tenant_id: str
    run_id: str
    submitted_by: str
    payload: Dict[str, Any]
    status: ProposalStatus
    submitted_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    reviewer_id: Optional[str] = None
    comment: Optional[str] = None
    policy_version: Optional[str] = None
    decided_at: Optional[datetime] = None


class ProposalQueue:
    """In-memory proposal queue with audit trails for state transitions.

    Thread-safety: a single :class:`threading.Lock` guards the underlying map;
    callers obtain deep copies of records to prevent external mutation.
    """

    _AUDIT_EVENT = "proposal_state_changed"

    def __init__(self, audit_sink: Optional[AuditSink] = None) -> None:
        self._proposals: Dict[str, Proposal] = {}
        self._lock = Lock()
        self._audit_sink = audit_sink

    def submit(
        self,
        context: RequestContext,
        kind: ProposalKind,
        payload: Dict[str, Any],
        *,
        policy_version: Optional[str] = None,
    ) -> Proposal:
        """Create a proposal in ``pending`` state and emit audit trail."""

        if not isinstance(payload, dict):
            raise TypeError("proposal payload must be a dict")
        proposal_id = str(uuid4())
        proposal = Proposal(
            proposal_id=proposal_id,
            kind=kind,
            tenant_id=context.tenant_id,
            run_id=context.run_id,
            submitted_by=context.user_id,
            payload=dict(payload),
            status=ProposalStatus.PENDING,
            policy_version=policy_version,
        )
        with self._lock:
            self._proposals[proposal_id] = proposal
        self._audit(
            proposal=proposal,
            transition="submitted",
            actor=context.user_id,
        )
        return self._copy(proposal)

    def approve(
        self,
        proposal_id: str,
        reviewer_id: str,
        comment: str = "",
    ) -> Optional[Proposal]:
        """Approve a pending proposal. Returns updated proposal or ``None``."""

        return self._transition(
            proposal_id=proposal_id,
            target_status=ProposalStatus.APPROVED,
            reviewer_id=reviewer_id,
            comment=comment,
            transition="approved",
        )

    def reject(
        self,
        proposal_id: str,
        reviewer_id: str,
        comment: str = "",
    ) -> Optional[Proposal]:
        """Reject a pending proposal. Returns updated proposal or ``None``."""

        return self._transition(
            proposal_id=proposal_id,
            target_status=ProposalStatus.REJECTED,
            reviewer_id=reviewer_id,
            comment=comment,
            transition="rejected",
        )

    def withdraw(
        self,
        proposal_id: str,
        actor_id: str,
        comment: str = "",
    ) -> Optional[Proposal]:
        """Withdraw a pending proposal (submitter cancels before decision)."""

        return self._transition(
            proposal_id=proposal_id,
            target_status=ProposalStatus.WITHDRAWN,
            reviewer_id=actor_id,
            comment=comment,
            transition="withdrawn",
        )

    def get(self, proposal_id: str) -> Optional[Proposal]:
        """Return a deep copy of the proposal or ``None`` if missing."""

        with self._lock:
            proposal = self._proposals.get(proposal_id)
            if proposal is None:
                return None
            return self._copy(proposal)

    def list_pending(
        self,
        *,
        tenant_id: Optional[str] = None,
        kind: Optional[ProposalKind] = None,
        limit: int = 100,
    ) -> List[Proposal]:
        """List pending proposals filtered by tenant/kind (paginated)."""

        return self._list(
            status=ProposalStatus.PENDING,
            tenant_id=tenant_id,
            kind=kind,
            limit=limit,
        )

    def list_proposals(
        self,
        *,
        tenant_id: Optional[str] = None,
        status: Optional[ProposalStatus] = None,
        kind: Optional[ProposalKind] = None,
        limit: int = 100,
    ) -> List[Proposal]:
        """List proposals with arbitrary status/tenant/kind filters."""

        return self._list(
            status=status,
            tenant_id=tenant_id,
            kind=kind,
            limit=limit,
        )

    def _list(
        self,
        *,
        status: Optional[ProposalStatus],
        tenant_id: Optional[str],
        kind: Optional[ProposalKind],
        limit: int,
    ) -> List[Proposal]:
        with self._lock:
            rows = list(self._proposals.values())
        filtered: List[Proposal] = []
        for proposal in rows:
            if status is not None and proposal.status != status:
                continue
            if tenant_id is not None and proposal.tenant_id != tenant_id:
                continue
            if kind is not None and proposal.kind != kind:
                continue
            filtered.append(self._copy(proposal))
        filtered.sort(key=lambda p: p.submitted_at)
        if limit > 0:
            filtered = filtered[-limit:]
        return filtered

    def _transition(
        self,
        *,
        proposal_id: str,
        target_status: ProposalStatus,
        reviewer_id: str,
        comment: str,
        transition: str,
    ) -> Optional[Proposal]:
        with self._lock:
            proposal = self._proposals.get(proposal_id)
            if proposal is None or proposal.status != ProposalStatus.PENDING:
                return None
            proposal.status = target_status
            proposal.reviewer_id = reviewer_id
            proposal.comment = comment or None
            proposal.decided_at = datetime.now(timezone.utc)
            snapshot = self._copy(proposal)
        self._audit(
            proposal=snapshot,
            transition=transition,
            actor=reviewer_id,
        )
        return snapshot

    @staticmethod
    def _copy(proposal: Proposal) -> Proposal:
        return Proposal(
            proposal_id=proposal.proposal_id,
            kind=proposal.kind,
            tenant_id=proposal.tenant_id,
            run_id=proposal.run_id,
            submitted_by=proposal.submitted_by,
            payload=dict(proposal.payload),
            status=proposal.status,
            submitted_at=proposal.submitted_at,
            reviewer_id=proposal.reviewer_id,
            comment=proposal.comment,
            policy_version=proposal.policy_version,
            decided_at=proposal.decided_at,
        )

    def _audit(self, *, proposal: Proposal, transition: str, actor: str) -> None:
        if self._audit_sink is None:
            return
        try:
            self._audit_sink.append(
                AuditRecord(
                    event_type=self._AUDIT_EVENT,
                    tenant_id=proposal.tenant_id,
                    run_id=proposal.run_id,
                    policy_version=proposal.policy_version,
                    payload={
                        "proposal_id": proposal.proposal_id,
                        "kind": proposal.kind.value,
                        "transition": transition,
                        "status": proposal.status.value,
                        "actor": actor,
                    },
                )
            )
        except Exception:
            pass


__all__ = [
    "Proposal",
    "ProposalKind",
    "ProposalQueue",
    "ProposalStatus",
]
