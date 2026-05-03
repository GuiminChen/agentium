"""Parent/child run supervision and orphan handling."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from threading import Lock
from typing import Dict, Optional


class OrphanPolicy(str, Enum):
    """Policy applied to children when a parent run terminates."""

    FAIL = "fail"
    ADOPT = "adopt"
    CANCEL = "cancel"


class TaskRunStatus(str, Enum):
    """Status for supervised task graph nodes."""

    ACTIVE = "active"
    TERMINATED = "terminated"
    FAILED = "failed"
    ADOPTED = "adopted"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class TaskRunRecord:
    """One run in a parent/child task graph."""

    run_id: str
    tenant_id: str
    parent_run_id: Optional[str] = None
    lease_id: Optional[str] = None
    orphan_policy: OrphanPolicy = OrphanPolicy.FAIL
    status: TaskRunStatus = TaskRunStatus.ACTIVE
    orphaned: bool = False


class TaskGraphSupervisor:
    """In-memory task graph supervisor with explicit orphan policies."""

    def __init__(self) -> None:
        self._runs: Dict[str, TaskRunRecord] = {}
        self._lock = Lock()

    def register_run(
        self,
        run_id: str,
        tenant_id: str,
        parent_run_id: Optional[str] = None,
        lease_id: Optional[str] = None,
        orphan_policy: OrphanPolicy = OrphanPolicy.FAIL,
    ) -> TaskRunRecord:
        """Register a run and optional parent relationship."""

        record = TaskRunRecord(
            run_id=run_id,
            tenant_id=tenant_id,
            parent_run_id=parent_run_id,
            lease_id=lease_id,
            orphan_policy=orphan_policy,
        )
        with self._lock:
            self._runs[run_id] = record
        return record

    def terminate_run(
        self, run_id: str, adopter_run_id: Optional[str] = None
    ) -> Dict[str, TaskRunRecord]:
        """Terminate a parent and apply orphan policy to direct children."""

        changed: Dict[str, TaskRunRecord] = {}
        with self._lock:
            parent = self._runs.get(run_id)
            if parent is not None:
                terminated = self._replace(parent, status=TaskRunStatus.TERMINATED)
                self._runs[run_id] = terminated
                changed[run_id] = terminated

            for child in list(self._runs.values()):
                if child.parent_run_id != run_id or child.status != TaskRunStatus.ACTIVE:
                    continue
                next_record = self._orphan_child(child, adopter_run_id)
                self._runs[child.run_id] = next_record
                changed[child.run_id] = next_record
        return changed

    def get(self, run_id: str) -> Optional[TaskRunRecord]:
        """Return a supervised run by id."""

        with self._lock:
            return self._runs.get(run_id)

    def children_of(self, run_id: str) -> list[TaskRunRecord]:
        """Return direct children for a run."""

        with self._lock:
            return [record for record in self._runs.values() if record.parent_run_id == run_id]

    def _orphan_child(
        self, child: TaskRunRecord, adopter_run_id: Optional[str]
    ) -> TaskRunRecord:
        if child.orphan_policy == OrphanPolicy.ADOPT:
            return self._replace(
                child,
                parent_run_id=adopter_run_id,
                status=TaskRunStatus.ADOPTED,
                orphaned=True,
            )
        if child.orphan_policy == OrphanPolicy.CANCEL:
            return self._replace(child, status=TaskRunStatus.CANCELLED, orphaned=True)
        return self._replace(child, status=TaskRunStatus.FAILED, orphaned=True)

    @staticmethod
    def _replace(record: TaskRunRecord, **changes: object) -> TaskRunRecord:
        values = {
            "run_id": record.run_id,
            "tenant_id": record.tenant_id,
            "parent_run_id": record.parent_run_id,
            "lease_id": record.lease_id,
            "orphan_policy": record.orphan_policy,
            "status": record.status,
            "orphaned": record.orphaned,
        }
        values.update(changes)
        return TaskRunRecord(**values)  # type: ignore[arg-type]
