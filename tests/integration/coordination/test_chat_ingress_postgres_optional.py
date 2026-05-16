"""PostgreSQL ingress backend checks when ``AGENTIUM_CHAT_INGRESS_TEST_DATABASE_URL`` is set."""

from __future__ import annotations

import os

import pytest

from agentium.coordination.chat_ingress.postgres_backend import PostgresChatIngressBackend

_PG = os.getenv("AGENTIUM_CHAT_INGRESS_TEST_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _PG,
    reason="Set AGENTIUM_CHAT_INGRESS_TEST_DATABASE_URL to run Postgres ingress integration.",
)


@pytest.mark.integration
def test_postgres_backend_lease_followup_steer_roundtrip() -> None:
    b = PostgresChatIngressBackend(url=_PG)
    sk = "tenant:session-pg-it"

    assert b.has_lease(sk) is False
    assert b.try_acquire_lease(sk, "tok1", 120.0) is True
    assert b.has_lease(sk) is True
    assert b.try_acquire_lease(sk, "other", 120.0) is False
    assert b.followup_enqueue(sk, '{"k":1}') == 1
    assert b.followup_pop(sk) == '{"k":1}'
    b.steer_append(sk, "s1")
    assert "s1" in b.steer_drain(sk)
    b.release_lease(sk, "tok1")
    assert b.has_lease(sk) is False
