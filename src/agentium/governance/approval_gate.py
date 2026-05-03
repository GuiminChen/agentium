"""In-memory approval gate for human-in-the-loop checkpoints."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from threading import Lock
from typing import Dict, List, Optional
from uuid import uuid4

from typing_extensions import Protocol

from agentium.models.context import RequestContext


class ApprovalStatus(str, Enum):
    """Possible states for approval requests."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


@dataclass
class ApprovalRequest:
    """Approval request record tracked by ApprovalGate."""

    approval_id: str
    run_id: str
    tenant_id: str
    tool_name: str
    reason: str
    args_hash: str
    status: ApprovalStatus
    requested_by: str
    requested_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    approver_id: Optional[str] = None
    comment: Optional[str] = None
    expires_at: Optional[datetime] = None
    resume_state_json: Optional[str] = None


class ApprovalService(Protocol):
    """Protocol for approval gate backends."""

    def request_approval(
        self,
        context: RequestContext,
        tool_name: str,
        reason: str,
        args_hash: str,
        ttl_seconds: Optional[int] = None,
        resume_state_json: Optional[str] = None,
    ) -> ApprovalRequest:
        """Create new approval request."""

    def approve(self, approval_id: str, approver_id: str, comment: str = "") -> bool:
        """Approve pending request."""

    def reject(self, approval_id: str, approver_id: str, comment: str = "") -> bool:
        """Reject pending request."""

    def get_request(self, approval_id: str) -> Optional[ApprovalRequest]:
        """Get request by id."""

    def expire_pending(self, now: Optional[datetime] = None) -> List[ApprovalRequest]:
        """Expire any pending requests whose expires_at has passed."""

    def list_pending_for_run(self, run_id: str) -> List[ApprovalRequest]:
        """List pending requests for a given run id (for resume planning)."""

    def list_approvals(
        self,
        *,
        tenant_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[ApprovalRequest]:
        """List approval requests with optional tenant/status filters (HTTP use)."""


class ApprovalGate:
    """In-memory HITL approval gate with request and decision APIs."""

    def __init__(self) -> None:
        self._requests: Dict[str, ApprovalRequest] = {}
        self._lock = Lock()

    def request_approval(
        self,
        context: RequestContext,
        tool_name: str,
        reason: str,
        args_hash: str,
        ttl_seconds: Optional[int] = None,
        resume_state_json: Optional[str] = None,
    ) -> ApprovalRequest:
        """Create a new approval request entry."""

        approval_id = str(uuid4())
        expires_at: Optional[datetime] = None
        if ttl_seconds and ttl_seconds > 0:
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        request = ApprovalRequest(
            approval_id=approval_id,
            run_id=context.run_id,
            tenant_id=context.tenant_id,
            tool_name=tool_name,
            reason=reason,
            args_hash=args_hash,
            status=ApprovalStatus.PENDING,
            requested_by=context.user_id,
            expires_at=expires_at,
            resume_state_json=resume_state_json,
        )
        with self._lock:
            self._requests[approval_id] = request
        return request

    def approve(self, approval_id: str, approver_id: str, comment: str = "") -> bool:
        """Approve one pending request."""

        with self._lock:
            request = self._requests.get(approval_id)
            if request is None or request.status != ApprovalStatus.PENDING:
                return False
            request.status = ApprovalStatus.APPROVED
            request.approver_id = approver_id
            request.comment = comment or None
            return True

    def reject(self, approval_id: str, approver_id: str, comment: str = "") -> bool:
        """Reject one pending request."""

        with self._lock:
            request = self._requests.get(approval_id)
            if request is None or request.status != ApprovalStatus.PENDING:
                return False
            request.status = ApprovalStatus.REJECTED
            request.approver_id = approver_id
            request.comment = comment or None
            return True

    def get_request(self, approval_id: str) -> Optional[ApprovalRequest]:
        """Get one approval request by id."""

        with self._lock:
            request = self._requests.get(approval_id)
            if request is None:
                return None
            return ApprovalRequest(**request.__dict__)

    def expire_pending(self, now: Optional[datetime] = None) -> List[ApprovalRequest]:
        """Move pending requests past their expires_at to EXPIRED state."""

        cutoff = now or datetime.now(timezone.utc)
        expired: List[ApprovalRequest] = []
        with self._lock:
            for request in self._requests.values():
                if request.status != ApprovalStatus.PENDING:
                    continue
                if request.expires_at is None:
                    continue
                if request.expires_at <= cutoff:
                    request.status = ApprovalStatus.EXPIRED
                    expired.append(ApprovalRequest(**request.__dict__))
        return expired

    def list_pending_for_run(self, run_id: str) -> List[ApprovalRequest]:
        """List pending requests for one run id."""

        with self._lock:
            return [
                ApprovalRequest(**request.__dict__)
                for request in self._requests.values()
                if request.run_id == run_id and request.status == ApprovalStatus.PENDING
            ]

    def list_approvals(
        self,
        *,
        tenant_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[ApprovalRequest]:
        """Return matching requests up to limit, stable order by requested_at."""

        with self._lock:
            rows = list(self._requests.values())
        filtered: List[ApprovalRequest] = []
        for request in rows:
            if tenant_id is not None and request.tenant_id != tenant_id:
                continue
            if status is not None and request.status.value != status:
                continue
            filtered.append(ApprovalRequest(**request.__dict__))
        filtered.sort(key=lambda r: r.requested_at)
        if limit > 0:
            filtered = filtered[-limit:]
        return filtered
