"""Cross-backend semantics for :class:`~agentium.coordination.chat_ingress.backend.ChatIngressBackend`.

Redis uses fakeredis (no daemon). SQLite uses a temp file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentium.coordination.chat_ingress.memory_backend import MemoryChatIngressBackend
from agentium.coordination.chat_ingress.redis_backend import RedisChatIngressBackend
from agentium.coordination.chat_ingress.sqlite_backend import SqliteChatIngressBackend


def _assert_lease_and_followup_steer(backend: object, sk: str) -> None:
    b = backend
    assert b.has_lease(sk) is False  # type: ignore[union-attr]
    assert b.try_acquire_lease(sk, "tok-a", 60.0) is True  # type: ignore[union-attr]
    assert b.has_lease(sk) is True  # type: ignore[union-attr]
    assert b.try_acquire_lease(sk, "tok-b", 60.0) is False  # type: ignore[union-attr]
    d0: int = b.followup_enqueue(sk, '{"x":1}')  # type: ignore[union-attr]
    assert d0 == 1
    d1: int = b.followup_enqueue(sk, '{"x":2}')  # type: ignore[union-attr]
    assert d1 == 2
    assert b.followup_pop(sk) == '{"x":1}'  # type: ignore[union-attr]
    assert b.followup_pop(sk) == '{"x":2}'  # type: ignore[union-attr]
    assert b.followup_pop(sk) is None  # type: ignore[union-attr]

    b.steer_append(sk, "a")  # type: ignore[union-attr]
    b.steer_append(sk, "b")  # type: ignore[union-attr]
    merged = b.steer_drain(sk)  # type: ignore[union-attr]
    assert merged == "a\n\nb"

    b.release_lease(sk, "tok-a")  # type: ignore[union-attr]
    assert b.has_lease(sk) is False  # type: ignore[union-attr]


def test_memory_backend_semantics() -> None:
    sk = "tenant:session-mem"
    _assert_lease_and_followup_steer(MemoryChatIngressBackend(), sk)


def test_sqlite_backend_semantics(tmp_path: Path) -> None:
    sk = "tenant:session-sql"
    _assert_lease_and_followup_steer(SqliteChatIngressBackend(path=tmp_path / "ingress_sem.db"), sk)


def test_redis_backend_semantics_with_fakeredis() -> None:
    pytest.importorskip("redis")
    fr_mod = pytest.importorskip("fakeredis")
    client = fr_mod.FakeRedis(decode_responses=True)
    sk = "tenant:session-redis"
    backend = RedisChatIngressBackend(client=client, key_prefix="unit-test-ingress")
    _assert_lease_and_followup_steer(backend, sk)
