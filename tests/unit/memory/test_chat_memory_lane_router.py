"""Tests for chat memory lane routing."""

from __future__ import annotations

from pathlib import Path

from agentium.governance.audit_lineage import InMemoryAuditSink
from agentium.infra.db.sqlite_chat_session_store import SqliteChatSessionStore
from agentium.memory.backends.inmemory_backend import InMemoryBackend
from agentium.memory.chat_memory_lane_router import ChatMemoryLaneRouter
from agentium.memory.memory_service import MemoryService


def test_single_backend_always_returns_same_service(tmp_path: Path) -> None:
    db_path = tmp_path / "chat.db"
    store = SqliteChatSessionStore(db_path)
    audit = InMemoryAuditSink()
    native = MemoryService(InMemoryBackend(), audit_sink=audit)
    router = ChatMemoryLaneRouter.single_backend(sessions=store, memory_service=native)
    store.create_session(
        tenant_id="t1",
        session_id="s1",
        title=None,
        skill="workspace_agent",
        intro_text=None,
        metadata={"workspace_agent": {"skill_tags": ["workspace_agent"], "memory_plugin": "mem0"}},
    )
    assert router.resolve(tenant_id="t1", session_id="s1") is native


def test_dual_lane_prefers_mem0_when_configured(tmp_path: Path) -> None:
    db_path = tmp_path / "chat.db"
    store = SqliteChatSessionStore(db_path)
    audit = InMemoryAuditSink()
    native = MemoryService(InMemoryBackend(), audit_sink=audit)
    mem0 = MemoryService(InMemoryBackend(), audit_sink=audit)
    router = ChatMemoryLaneRouter(
        sessions=store,
        native=native,
        mem0=mem0,
        yaml_primary_backend="memory",
    )
    store.create_session(
        tenant_id="t1",
        session_id="s-mem0",
        title=None,
        skill="workspace_agent",
        intro_text=None,
        metadata={"workspace_agent": {"skill_tags": ["workspace_agent"], "memory_plugin": "mem0"}},
    )
    assert router.resolve(tenant_id="t1", session_id="s-mem0") is mem0


def test_dual_lane_native_when_session_prefers_native(tmp_path: Path) -> None:
    db_path = tmp_path / "chat.db"
    store = SqliteChatSessionStore(db_path)
    audit = InMemoryAuditSink()
    native = MemoryService(InMemoryBackend(), audit_sink=audit)
    mem0 = MemoryService(InMemoryBackend(), audit_sink=audit)
    router = ChatMemoryLaneRouter(
        sessions=store,
        native=native,
        mem0=mem0,
        yaml_primary_backend="memory",
    )
    store.create_session(
        tenant_id="t1",
        session_id="s-native",
        title=None,
        skill="workspace_agent",
        intro_text=None,
        metadata={"workspace_agent": {"skill_tags": ["workspace_agent"], "memory_plugin": "native"}},
    )
    assert router.resolve(tenant_id="t1", session_id="s-native") is native


def test_fallback_to_native_when_mem0_lane_missing(tmp_path: Path) -> None:
    db_path = tmp_path / "chat.db"
    store = SqliteChatSessionStore(db_path)
    audit = InMemoryAuditSink()
    native = MemoryService(InMemoryBackend(), audit_sink=audit)
    router = ChatMemoryLaneRouter(
        sessions=store,
        native=native,
        mem0=None,
        yaml_primary_backend="sqlite",
    )
    store.create_session(
        tenant_id="t1",
        session_id="s1",
        title=None,
        skill="workspace_agent",
        intro_text=None,
        metadata={"workspace_agent": {"skill_tags": ["workspace_agent"], "memory_plugin": "mem0"}},
    )
    assert router.resolve(tenant_id="t1", session_id="s1") is native
