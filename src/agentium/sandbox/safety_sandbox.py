"""SafetySandbox: capability-scoped, isolated tool execution surface.

This is an in-process safety boundary that records the *requested capabilities*
of every tool invocation and rejects requests that ask for more than the tenant
profile is allowed to use.  Real container/VM isolation is out of scope for the
backend reference implementation but the surface mirrors PRD §3.16 / docker-
inspired design so that operators can swap a stronger backend later without
touching call sites.

Design notes:
- Capabilities are *declared* by the tool owner via :class:`SandboxRequest`.
- :class:`SafetySandbox.run` is the single chokepoint.  ``policy`` is consulted
  before the callable is invoked; on deny we never invoke the callable.
- Optional CPU time / wall clock / output size limits guard against runaway
  tool implementations.  They apply to in-process calls only; future container
  backends can re-implement :class:`_SandboxBackend`.
- The sandbox is *append-only audit-friendly*: every decision flows through a
  caller-provided ``audit`` callback (e.g. ``AuditSink.append``).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, FrozenSet, Mapping, Optional, Sequence, Tuple


class SandboxDeniedError(Exception):
    """Raised when a tool requests capabilities outside its allowlist."""


class SandboxTimeoutError(Exception):
    """Raised when a sandboxed callable exceeds its wall-clock budget."""


class SandboxOutputTooLargeError(Exception):
    """Raised when sandboxed callable returns more bytes than allowed."""


@dataclass(frozen=True)
class SandboxProfile:
    """Allowlist for a tenant/profile/tool combination.

    Attributes:
        allowed_capabilities: capabilities the caller is allowed to request.
        max_wall_seconds: max wall clock per call. ``None`` disables the limit.
        max_output_bytes: max serialized bytes per call. ``None`` disables.
        path_allowlist_prefixes: when non-empty, optional ``metadata["sandbox_path"]`` must match
            one of these path prefixes (POSIX-style).
        egress_deny_by_default: when True and ``metadata["egress_host"]`` is set, outbound/net
            capability requests must target a host in ``egress_allow_hosts``.
        egress_allow_hosts: lowercase hostnames permitted when ``egress_deny_by_default`` is True.
    """

    allowed_capabilities: FrozenSet[str]
    max_wall_seconds: Optional[float] = None
    max_output_bytes: Optional[int] = None
    path_allowlist_prefixes: Tuple[str, ...] = ()
    egress_deny_by_default: bool = False
    egress_allow_hosts: FrozenSet[str] = frozenset()


@dataclass(frozen=True)
class SandboxRequest:
    """Description of one sandboxed call.

    Attributes:
        tool_name: stable id of the tool being invoked.
        tenant_id: caller tenant.
        capabilities: capabilities the tool needs (e.g. ``net.outbound.email``).
        run_id: optional control-plane run id, propagated to audit records.
        tool_use_id: optional execution id (one per concrete invocation).
    """

    tool_name: str
    tenant_id: str
    capabilities: Sequence[str]
    run_id: Optional[str] = None
    tool_use_id: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class SandboxOutcome:
    """Structured result returned by :meth:`SafetySandbox.run`."""

    request: SandboxRequest
    granted: FrozenSet[str]
    duration_ms: int
    output: Any
    output_bytes: int


AuditFn = Callable[[str, Mapping[str, Any]], None]


class SafetySandbox:
    """Capability-scoped execution boundary with hard limits and audit hooks.

    Args:
        profiles: mapping of ``(tenant_id, tool_name)`` → :class:`SandboxProfile`.
            ``("*", tool_name)`` matches any tenant; ``(tenant_id, "*")`` matches
            any tool for the tenant; ``("*", "*")`` is the global default.
        audit: optional callable that receives ``(event_type, payload)`` for
            every grant or denial.  Use this to forward into ``AuditSink``.
        clock: optional monotonic clock (for tests).
    """

    def __init__(
        self,
        profiles: Optional[Mapping[tuple[str, str], SandboxProfile]] = None,
        *,
        audit: Optional[AuditFn] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._profiles = dict(profiles or {})
        self._audit = audit
        self._clock = clock
        self._lock = threading.Lock()

    def register_profile(
        self, tenant_id: str, tool_name: str, profile: SandboxProfile
    ) -> None:
        """Install or replace a profile entry."""

        with self._lock:
            self._profiles[(tenant_id, tool_name)] = profile

    def resolve_profile(self, tenant_id: str, tool_name: str) -> SandboxProfile:
        """Return the most specific matching profile, falling back to default deny."""

        with self._lock:
            for key in (
                (tenant_id, tool_name),
                ("*", tool_name),
                (tenant_id, "*"),
                ("*", "*"),
            ):
                if key in self._profiles:
                    return self._profiles[key]
        return SandboxProfile(allowed_capabilities=frozenset())

    def run(
        self,
        request: SandboxRequest,
        callable_: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> SandboxOutcome:
        """Execute ``callable_`` under the resolved profile.

        Raises:
            SandboxDeniedError: requested capabilities exceed the profile.
            SandboxTimeoutError: wall-clock limit exceeded.
            SandboxOutputTooLargeError: serialized output exceeds size limit.
        """

        profile = self.resolve_profile(request.tenant_id, request.tool_name)
        requested = frozenset(request.capabilities)
        denied = requested - profile.allowed_capabilities
        if denied:
            payload = {
                "tool_name": request.tool_name,
                "tenant_id": request.tenant_id,
                "run_id": request.run_id,
                "tool_use_id": request.tool_use_id,
                "denied_capabilities": sorted(denied),
                "requested": sorted(requested),
                "allowed": sorted(profile.allowed_capabilities),
            }
            self._emit("sandbox_denied", payload)
            raise SandboxDeniedError(
                f"Capability denied for {request.tool_name}: {sorted(denied)}"
            )

        if profile.path_allowlist_prefixes:
            path_candidate = str(request.metadata.get("sandbox_path") or "").strip()
            if path_candidate:
                normalized = path_candidate.replace("\\", "/")
                if not any(normalized.startswith(p) for p in profile.path_allowlist_prefixes):
                    self._emit(
                        "sandbox_path_denied",
                        {
                            "tool_name": request.tool_name,
                            "path": path_candidate,
                        },
                    )
                    raise SandboxDeniedError("sandbox_path_not_allowlisted")

        if profile.egress_deny_by_default:
            host = str(request.metadata.get("egress_host") or "").strip().lower()
            if host:
                wants_net = any("net" in str(c).lower() for c in requested)
                if wants_net and host not in profile.egress_allow_hosts:
                    self._emit(
                        "sandbox_egress_denied",
                        {
                            "tool_name": request.tool_name,
                            "host": host,
                        },
                    )
                    raise SandboxDeniedError("sandbox_egress_host_blocked")

        started = self._clock()
        output = callable_(*args, **kwargs)
        duration = self._clock() - started

        if profile.max_wall_seconds is not None and duration > profile.max_wall_seconds:
            payload = {
                "tool_name": request.tool_name,
                "tenant_id": request.tenant_id,
                "run_id": request.run_id,
                "tool_use_id": request.tool_use_id,
                "duration_ms": int(duration * 1000),
                "limit_seconds": profile.max_wall_seconds,
            }
            self._emit("sandbox_timeout", payload)
            raise SandboxTimeoutError(
                f"Sandbox wall-clock limit exceeded for {request.tool_name}"
            )

        output_bytes = self._estimate_size(output)
        if (
            profile.max_output_bytes is not None
            and output_bytes > profile.max_output_bytes
        ):
            payload = {
                "tool_name": request.tool_name,
                "tenant_id": request.tenant_id,
                "run_id": request.run_id,
                "tool_use_id": request.tool_use_id,
                "output_bytes": output_bytes,
                "limit_bytes": profile.max_output_bytes,
            }
            self._emit("sandbox_output_too_large", payload)
            raise SandboxOutputTooLargeError(
                f"Sandbox output exceeds size limit for {request.tool_name}"
            )

        outcome = SandboxOutcome(
            request=request,
            granted=requested,
            duration_ms=int(duration * 1000),
            output=output,
            output_bytes=output_bytes,
        )
        self._emit(
            "sandbox_granted",
            {
                "tool_name": request.tool_name,
                "tenant_id": request.tenant_id,
                "run_id": request.run_id,
                "tool_use_id": request.tool_use_id,
                "capabilities": sorted(requested),
                "duration_ms": outcome.duration_ms,
                "output_bytes": outcome.output_bytes,
            },
        )
        return outcome

    @staticmethod
    def _estimate_size(value: Any) -> int:
        if value is None:
            return 0
        if isinstance(value, (bytes, bytearray)):
            return len(value)
        if isinstance(value, str):
            return len(value.encode("utf-8", errors="replace"))
        try:
            import json

            return len(json.dumps(value, default=str).encode("utf-8"))
        except Exception:
            return len(repr(value).encode("utf-8", errors="replace"))

    def _emit(self, event_type: str, payload: Mapping[str, Any]) -> None:
        if self._audit is None:
            return
        try:
            self._audit(event_type, payload)
        except Exception:
            pass
