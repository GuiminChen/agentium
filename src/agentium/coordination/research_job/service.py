"""Lightweight research job orchestration API backing (P1-24 MVP)."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import structlog

if TYPE_CHECKING:
    from agentium.app.settings import AppSettings
    from agentium.coordination.task_lock.protocol import TaskLockBackend

from agentium.coordination.harness_task_coordination import (
    acquire_harness_locks,
    run_minimal_oracle_if_configured,
)
from agentium.coordination.task_lock.types import TaskLockLease
from agentium.models.harness_contract import HarnessContract

_LOGGER = structlog.get_logger(__name__)


@dataclass
class ResearchJobRecord:
    """In-memory research job snapshot."""

    job_id: str
    tenant_id: str
    status: str
    phase: str
    payload: Dict[str, Any] = field(default_factory=dict)
    token_estimate: Optional[int] = None
    lock_leases: List[TaskLockLease] = field(default_factory=list)


class ResearchJobService:
    """Minimal job store + state transitions (queued → running); extend with workers later."""

    def __init__(
        self,
        *,
        settings: Optional[AppSettings] = None,
        task_lock_backend: Optional[TaskLockBackend] = None,
    ) -> None:
        self._records: Dict[str, ResearchJobRecord] = {}
        self._settings = settings
        self._task_lock_backend = task_lock_backend

    def create_job(
        self,
        *,
        tenant_id: str,
        query: str,
        max_workers: int = 2,
        harness: Optional[HarnessContract] = None,
    ) -> ResearchJobRecord:
        started = time.monotonic()
        job_id = str(uuid.uuid4())
        rec = ResearchJobRecord(
            job_id=job_id,
            tenant_id=str(tenant_id).strip(),
            status="queued",
            phase="enqueue",
            payload={"query": query, "max_workers": max(1, min(16, int(max_workers)))},
        )

        if (
            harness is not None
            and self._settings is not None
            and self._task_lock_backend is not None
            and self._settings.feature_task_lock_enabled
            and harness.lock_resource_keys
        ):
            ok, leases = acquire_harness_locks(
                backend=self._task_lock_backend,
                settings=self._settings,
                tenant_id=rec.tenant_id,
                holder_run_id=job_id,
                contract=harness,
            )
            if not ok:
                rec.status = "blocked"
                rec.phase = "task_lock_denied"
                self._records[job_id] = rec
                _LOGGER.info(
                    "research_job_transition",
                    job_id=job_id,
                    tenant_id=rec.tenant_id,
                    status=rec.status,
                    phase=rec.phase,
                    create_latency_ms=int((time.monotonic() - started) * 1000),
                )
                return rec
            rec.lock_leases = leases

        rec.status = "running"
        rec.phase = "orchestrator"
        rec.token_estimate = len(query) // 4 + 10
        self._records[job_id] = rec
        _LOGGER.info(
            "research_job_transition",
            job_id=job_id,
            tenant_id=rec.tenant_id,
            status=rec.status,
            phase=rec.phase,
            create_latency_ms=int((time.monotonic() - started) * 1000),
        )
        _LOGGER.info(
            "research_token_estimate",
            job_id=job_id,
            research_token_estimate=rec.token_estimate,
        )
        if harness is not None and self._settings is not None:
            run_minimal_oracle_if_configured(
                settings=self._settings,
                contract=harness,
                job_id=job_id,
                tenant_id=rec.tenant_id,
            )
        return rec

    def get(self, *, tenant_id: str, job_id: str) -> Optional[ResearchJobRecord]:
        rec = self._records.get(str(job_id).strip())
        if rec is None or rec.tenant_id != str(tenant_id).strip():
            return None
        return rec

    def to_http_dict(self, rec: ResearchJobRecord) -> Dict[str, Any]:
        return {
            "job_id": rec.job_id,
            "tenant_id": rec.tenant_id,
            "status": rec.status,
            "phase": rec.phase,
            "payload": dict(rec.payload),
            "token_estimate": rec.token_estimate,
        }
