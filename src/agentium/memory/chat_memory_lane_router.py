"""Resolve per-session chat memory lane (native layered vs Mem0 platform)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

import structlog

from agentium.app.plugins_config import Mem0PluginConfig, MemoryPluginConfig
from agentium.governance.audit_lineage import AuditSink
from agentium.infra.db.sqlite_chat_session_store import SqliteChatSessionStore
from agentium.memory.factory import build_memory_backend
from agentium.memory.memory_service import MemoryService

_LOGGER = structlog.get_logger(__name__)

YamlMemoryBackend = Literal["memory", "sqlite", "mem0"]


class ChatMemoryLaneRouter:
    """Pick :class:`MemoryService` for chat recall/write based on session workspace_agent."""

    def __init__(
        self,
        *,
        sessions: SqliteChatSessionStore,
        native: Optional[MemoryService],
        mem0: Optional[MemoryService],
        yaml_primary_backend: YamlMemoryBackend,
    ) -> None:
        self._sessions = sessions
        self._native = native
        self._mem0 = mem0
        self._yaml_primary_backend = yaml_primary_backend

    @classmethod
    def single_backend(
        cls,
        *,
        sessions: SqliteChatSessionStore,
        memory_service: MemoryService,
        yaml_primary_backend: YamlMemoryBackend = "memory",
    ) -> ChatMemoryLaneRouter:
        """Single lane: ignore workspace preference when only one backend exists."""

        return cls(
            sessions=sessions,
            native=memory_service,
            mem0=None,
            yaml_primary_backend=yaml_primary_backend,
        )

    def resolve(self, *, tenant_id: str, session_id: str) -> Optional[MemoryService]:
        """Return memory service for chat I/O for this session."""

        pref = self._read_preference(tenant_id=tenant_id, session_id=session_id)
        return self._pick_lane(preferred=pref)

    def _read_preference(self, *, tenant_id: str, session_id: str) -> Literal["native", "mem0"]:
        rec = self._sessions.try_get_session(tenant_id=tenant_id, session_id=session_id)
        if rec is None:
            return "native"
        raw_wa = rec.metadata.get("workspace_agent")
        if not isinstance(raw_wa, dict):
            return "native"
        mp = raw_wa.get("memory_plugin")
        if mp == "mem0":
            return "mem0"
        return "native"

    def _pick_lane(self, *, preferred: Literal["native", "mem0"]) -> Optional[MemoryService]:
        want_mem0 = preferred == "mem0"
        if want_mem0:
            if self._mem0 is not None:
                return self._mem0
            if self._native is not None:
                _LOGGER.info(
                    "chat_memory_lane_fallback",
                    requested="mem0",
                    used="native",
                    yaml_backend=self._yaml_primary_backend,
                )
                return self._native
            return None
        if self._native is not None:
            return self._native
        if self._mem0 is not None:
            _LOGGER.info(
                "chat_memory_lane_fallback",
                requested="native",
                used="mem0",
                yaml_backend=self._yaml_primary_backend,
            )
            return self._mem0
        return None


def build_chat_memory_lane_router(
    *,
    plugins_mem: MemoryPluginConfig,
    data_dir: Path,
    audit_sink: AuditSink,
    chat_session_store: SqliteChatSessionStore,
) -> tuple[ChatMemoryLaneRouter, MemoryService]:
    """Build lane router plus the service used for background consolidation (native lane).

    Returns:
        Router for chat handlers / :class:`~agentium.coordination.chat_turn_service.ChatTurnService`,
        and consolidation target (SQLite/in-memory native lane when available).
    """

    native_svc: Optional[MemoryService] = None
    mem0_svc: Optional[MemoryService] = None

    if plugins_mem.backend in ("memory", "sqlite"):
        native_backend = build_memory_backend(plugins_mem, data_dir)
        native_svc = MemoryService(backend=native_backend, audit_sink=audit_sink)
        if plugins_mem.optional_mem0_lane:
            mem0_cfg = MemoryPluginConfig(
                backend="mem0",
                sqlite_relative_path=plugins_mem.sqlite_relative_path,
                mem0=plugins_mem.mem0,
            )
            try:
                mem0_backend = build_memory_backend(mem0_cfg, data_dir)
                mem0_svc = MemoryService(backend=mem0_backend, audit_sink=audit_sink)
            except Exception as exc:
                _LOGGER.warning(
                    "optional_mem0_lane_disabled",
                    error=str(exc),
                    backend=plugins_mem.backend,
                )
    elif plugins_mem.backend == "mem0":
        mem0_backend = build_memory_backend(plugins_mem, data_dir)
        mem0_svc = MemoryService(backend=mem0_backend, audit_sink=audit_sink)
        sqlite_cfg = MemoryPluginConfig(
            backend="sqlite",
            sqlite_relative_path=plugins_mem.sqlite_relative_path,
            mem0=Mem0PluginConfig(),
        )
        native_backend = build_memory_backend(sqlite_cfg, data_dir)
        native_svc = MemoryService(backend=native_backend, audit_sink=audit_sink)
    else:
        raise ValueError(f"unknown plugins.memory.backend: {plugins_mem.backend!r}")

    consolidation = native_svc if native_svc is not None else mem0_svc
    if consolidation is None:
        raise RuntimeError("memory lane wiring produced no MemoryService")

    router = ChatMemoryLaneRouter(
        sessions=chat_session_store,
        native=native_svc,
        mem0=mem0_svc,
        yaml_primary_backend=plugins_mem.backend,
    )
    return router, consolidation


__all__ = ["ChatMemoryLaneRouter", "build_chat_memory_lane_router"]
