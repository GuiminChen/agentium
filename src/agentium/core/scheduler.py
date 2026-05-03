"""Tenant-fair scheduler with cooperative cancellation and timeout layers."""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, Dict, List, Optional

from agentium.core.cancel import CancelToken, CancelledError


@dataclass
class TimeoutLayers:
    """Timeout budget for the four layers per PRD/technical-design.

    Each value is a hard upper bound in seconds for that layer. The smallest
    layer SHOULD wrap the larger ones to provide deterministic short-circuit.
    """

    tool_seconds: float = 30.0
    llm_seconds: float = 60.0
    turn_seconds: float = 120.0
    node_seconds: float = 300.0

    def smallest(self) -> float:
        return min(
            self.tool_seconds,
            self.llm_seconds,
            self.turn_seconds,
            self.node_seconds,
        )


class TimeoutExceededError(RuntimeError):
    """Raised when one of the four timeout layers is exceeded."""

    def __init__(self, layer: str, elapsed_seconds: float) -> None:
        super().__init__(f"timeout exceeded at layer={layer} elapsed={elapsed_seconds:.3f}s")
        self.layer = layer
        self.elapsed_seconds = elapsed_seconds


@dataclass
class _TenantQueue:
    """Per-tenant FIFO queue used by the round-robin scheduler."""

    tenant_id: str
    queue: Deque["_QueuedJob"] = field(default_factory=deque)
    inflight: int = 0


@dataclass
class _QueuedJob:
    job_id: str
    tenant_id: str
    work: Callable[[CancelToken], object]
    cancel_token: CancelToken
    enqueued_at: float
    started_event: threading.Event = field(default_factory=threading.Event)
    done_event: threading.Event = field(default_factory=threading.Event)
    result: object = None
    error: Optional[BaseException] = None


class TenantFairScheduler:
    """Round-robin scheduler with per-tenant max-concurrency and backpressure.

    The scheduler is intentionally simple and synchronous-friendly: callers
    submit a job, then call ``run_pending()`` (typically from a worker thread)
    to drain ready slots. This avoids importing asyncio and works well with
    the existing thread-based HTTP server.
    """

    def __init__(
        self,
        max_concurrency_per_tenant: int = 4,
        global_max_concurrency: int = 32,
        max_queue_per_tenant: int = 64,
    ) -> None:
        if max_concurrency_per_tenant <= 0 or global_max_concurrency <= 0:
            raise ValueError("concurrency limits must be positive")
        self._per_tenant = max_concurrency_per_tenant
        self._global = global_max_concurrency
        self._max_queue_per_tenant = max_queue_per_tenant
        self._queues: Dict[str, _TenantQueue] = {}
        self._tenant_order: Deque[str] = deque()
        self._global_inflight = 0
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)

    def submit(
        self,
        job_id: str,
        tenant_id: str,
        work: Callable[[CancelToken], object],
        cancel_token: Optional[CancelToken] = None,
    ) -> _QueuedJob:
        """Enqueue a job. Raises BackpressureError when tenant queue is full."""

        token = cancel_token or CancelToken()
        job = _QueuedJob(
            job_id=job_id,
            tenant_id=tenant_id,
            work=work,
            cancel_token=token,
            enqueued_at=time.monotonic(),
        )
        with self._cv:
            queue = self._queues.get(tenant_id)
            if queue is None:
                queue = _TenantQueue(tenant_id=tenant_id)
                self._queues[tenant_id] = queue
                self._tenant_order.append(tenant_id)
            if len(queue.queue) >= self._max_queue_per_tenant:
                raise BackpressureError(tenant_id=tenant_id)
            queue.queue.append(job)
            self._cv.notify_all()
        return job

    def run_pending(self, max_jobs: int = 16) -> int:
        """Drain up to ``max_jobs`` ready jobs in round-robin tenant order."""

        executed = 0
        for _ in range(max_jobs):
            job = self._pick_next_job()
            if job is None:
                return executed
            executed += 1
            self._execute_job(job)
        return executed

    def cancel_all(self, source: str = "scheduler.cancel_all") -> int:
        """Cancel all queued and inflight jobs by token."""

        cancelled = 0
        with self._lock:
            for queue in self._queues.values():
                for job in queue.queue:
                    job.cancel_token.cancel(source=source)
                    cancelled += 1
        return cancelled

    def _pick_next_job(self) -> Optional[_QueuedJob]:
        with self._lock:
            if self._global_inflight >= self._global:
                return None
            attempts = len(self._tenant_order)
            for _ in range(attempts):
                tenant_id = self._tenant_order[0]
                self._tenant_order.rotate(-1)
                queue = self._queues.get(tenant_id)
                if queue is None or not queue.queue:
                    continue
                if queue.inflight >= self._per_tenant:
                    continue
                job = queue.queue.popleft()
                queue.inflight += 1
                self._global_inflight += 1
                return job
            return None

    def _execute_job(self, job: _QueuedJob) -> None:
        job.started_event.set()
        try:
            job.result = job.work(job.cancel_token)
        except BaseException as exc:  # noqa: BLE001
            job.error = exc
        finally:
            with self._lock:
                queue = self._queues.get(job.tenant_id)
                if queue is not None:
                    queue.inflight = max(0, queue.inflight - 1)
                self._global_inflight = max(0, self._global_inflight - 1)
                self._cv.notify_all()
            job.done_event.set()


class BackpressureError(RuntimeError):
    """Raised when a tenant queue is saturated."""

    def __init__(self, tenant_id: str) -> None:
        super().__init__(f"backpressure: tenant_id={tenant_id} queue full")
        self.tenant_id = tenant_id


def run_with_timeout(
    work: Callable[[CancelToken], object],
    layer: str,
    timeout_seconds: float,
    cancel_token: Optional[CancelToken] = None,
) -> object:
    """Run ``work`` in a worker thread with a hard timeout.

    On timeout we cancel the cooperative token, then raise
    ``TimeoutExceededError``. The thread is left to finish on its own (after
    cooperative cancellation) to avoid the dangers of force-killing.
    """

    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    token = cancel_token or CancelToken()
    container: Dict[str, object] = {}

    def _runner() -> None:
        try:
            container["result"] = work(token)
        except BaseException as exc:  # noqa: BLE001
            container["error"] = exc

    thread = threading.Thread(target=_runner, name=f"agentium-{layer}", daemon=True)
    started = time.monotonic()
    thread.start()
    thread.join(timeout=timeout_seconds)
    if thread.is_alive():
        token.cancel(source="run_with_timeout", detail=layer)
        elapsed = time.monotonic() - started
        raise TimeoutExceededError(layer=layer, elapsed_seconds=elapsed)
    if "error" in container:
        error = container["error"]
        if isinstance(error, CancelledError):
            raise error
        if isinstance(error, BaseException):
            raise error
    return container.get("result")
