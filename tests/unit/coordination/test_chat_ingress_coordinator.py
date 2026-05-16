"""Unit tests for :class:`~agentium.coordination.chat_ingress.coordinator.ChatIngressCoordinator`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentium.app.settings import load_settings
from agentium.coordination.chat_ingress.coordinator import ChatIngressCoordinator
from agentium.coordination.chat_ingress.exceptions import ChatIngressDeferred
from agentium.coordination.chat_ingress.memory_backend import MemoryChatIngressBackend


def _prep(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENTIUM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTIUM_CHAT_MODEL", "")


def test_followup_deferred_when_lease_held(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prep(monkeypatch, tmp_path)
    monkeypatch.setenv("AGENTIUM_CHAT_INGRESS_DEBOUNCE_MS", "500")
    settings = load_settings()
    backend = MemoryChatIngressBackend()
    coord = ChatIngressCoordinator(backend=backend, settings=settings)
    sk = "tenant-a:session-1"
    assert backend.try_acquire_lease(sk, "lease-1", settings.chat_ingress_lease_ttl_seconds) is True
    payload = {"session_id": "session-1", "tenant_id": "tenant-a", "x": 1}
    with pytest.raises(ChatIngressDeferred) as exc:
        coord.admit_user_turn(
            session_key=sk,
            effective_disposition="followup",
            working_content="hello",
            regenerate=False,
            followup_payload=payload,
        )
    assert exc.value.kind == "followup"
    assert exc.value.queue_depth == 1
    backend.release_lease(sk, "lease-1")


def test_steer_buffered_when_lease_held(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prep(monkeypatch, tmp_path)
    settings = load_settings()
    backend = MemoryChatIngressBackend()
    coord = ChatIngressCoordinator(backend=backend, settings=settings)
    sk = "t:s"
    assert backend.try_acquire_lease(sk, "tok", settings.chat_ingress_lease_ttl_seconds) is True
    with pytest.raises(ChatIngressDeferred) as exc:
        coord.admit_user_turn(
            session_key=sk,
            effective_disposition="steer",
            working_content="  nudge ",
            regenerate=False,
            followup_payload={},
        )
    assert exc.value.kind == "steer"
    assert backend.steer_drain(sk) == "nudge"
    backend.release_lease(sk, "tok")


def test_collect_immediate_merge_when_debounce_zero(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prep(monkeypatch, tmp_path)
    monkeypatch.setenv("AGENTIUM_CHAT_INGRESS_DEBOUNCE_MS", "0")
    settings = load_settings()
    assert settings.chat_ingress_debounce_ms == 0
    backend = MemoryChatIngressBackend()
    coord = ChatIngressCoordinator(backend=backend, settings=settings)
    sk = "t:s2"
    admission = coord.admit_user_turn(
        session_key=sk,
        effective_disposition="collect",
        working_content="a",
        regenerate=False,
        followup_payload={},
    )
    assert admission.working_content == "a"
    assert backend.has_lease(sk) is True
    backend.release_lease(sk, admission.lease_token)


def test_finish_turn_drains_followup_fifo(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prep(monkeypatch, tmp_path)
    settings = load_settings()
    backend = MemoryChatIngressBackend()
    coord = ChatIngressCoordinator(backend=backend, settings=settings)
    sk = "t:s3"
    backend.followup_enqueue(sk, json.dumps({"seq": 1}))
    backend.followup_enqueue(sk, json.dumps({"seq": 2}))
    order: list[int] = []

    def _drain(payload: dict[str, object]) -> None:
        order.append(int(payload["seq"]))

    coord.finish_turn_release_and_drain(session_key=sk, lease_token="no-lease", drain_fn=_drain)
    assert order == [1, 2]

