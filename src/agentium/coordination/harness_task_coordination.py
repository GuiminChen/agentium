"""Harness-scoped task locks and optional minimal oracle (P2 / #17)."""

from __future__ import annotations

import os
import subprocess
from typing import List, Optional, Tuple

import structlog

from agentium.app.settings import AppSettings
from agentium.coordination.task_lock.protocol import TaskLockBackend
from agentium.coordination.task_lock.types import TaskLockLease
from agentium.models.harness_contract import HarnessContract

_LOGGER = structlog.get_logger(__name__)


def acquire_harness_locks(
    *,
    backend: TaskLockBackend,
    settings: AppSettings,
    tenant_id: str,
    holder_run_id: str,
    contract: HarnessContract,
    ttl_seconds: Optional[float] = None,
) -> Tuple[bool, List[TaskLockLease]]:
    """Acquire all ``lock_resource_keys`` in order; rollback on first denial."""

    cap = float(settings.task_lock_max_ttl_seconds)
    req = float(ttl_seconds) if ttl_seconds is not None else cap
    ttl = max(1.0, min(req, cap))
    leases: list[TaskLockLease] = []
    for key in contract.lock_resource_keys:
        key_stripped = str(key).strip()
        if not key_stripped:
            continue
        lease = backend.try_acquire(
            tenant_id=tenant_id,
            resource_key=key_stripped,
            holder_run_id=holder_run_id,
            ttl_seconds=ttl,
            metadata_json=None,
        )
        if lease is None:
            for held in leases:
                backend.release(
                    tenant_id=held.tenant_id,
                    resource_key=held.resource_key,
                    holder_run_id=holder_run_id,
                )
            return False, []
        leases.append(lease)
    return True, leases


def release_harness_locks(*, backend: TaskLockBackend, leases: List[TaskLockLease]) -> None:
    """Release previously acquired leases (best-effort per key)."""

    for lease in leases:
        backend.release(
            tenant_id=lease.tenant_id,
            resource_key=lease.resource_key,
            holder_run_id=lease.holder_run_id,
        )


def run_minimal_oracle_if_configured(
    *,
    settings: AppSettings,
    contract: HarnessContract,
    job_id: str,
    tenant_id: str,
) -> None:
    """Log ``harness_oracle_result`` for built-in deterministic refs when enabled."""

    if not settings.harness_oracle_enabled:
        return
    ref = (contract.oracle_command_ref or "").strip()
    if not ref:
        return
    if ref == "builtin:exit0":
        if os.name == "nt":
            cmd = ["cmd", "/c", "exit", "0"]
        else:
            cmd = ["/bin/sh", "-c", "exit 0"]
        proc = subprocess.run(cmd, check=False, capture_output=True, timeout=5.0)
        ok = proc.returncode == 0
        raw = (proc.stderr or b"") + (proc.stdout or b"")
        preview = raw[:256].decode("utf-8", errors="replace")
        _LOGGER.info(
            "harness_oracle_result",
            job_id=job_id,
            tenant_id=tenant_id,
            oracle_ref=ref,
            passed=ok,
            returncode=proc.returncode,
            detail=preview or None,
        )
        return
    if ref == "builtin:token_ok":
        _LOGGER.info(
            "harness_oracle_result",
            job_id=job_id,
            tenant_id=tenant_id,
            oracle_ref=ref,
            passed=True,
            detail="deterministic_token",
        )
        return
    _LOGGER.info(
        "harness_oracle_result",
        job_id=job_id,
        tenant_id=tenant_id,
        oracle_ref=ref,
        passed=False,
        detail="unknown_oracle_ref",
    )
